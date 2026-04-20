# SPDX-License-Identifier: Apache-2.0

import json
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Union

import torch
import zmq
from safetensors.torch import load as safetensors_load
from safetensors.torch import save as safetensors_save

from vllm.config import KVTransferConfig
from vllm.distributed.kv_transfer.kv_pipe.base import KVPipeBase
from vllm.logger import init_logger

import time
import socketserver
import http.server
import threading
import concurrent.futures
import socket


logger = init_logger(__name__)
NONE_INT = -150886311


def is_port_available(host, port):
    """
    检查指定主机和端口是否可用（使用标准 socket 模块）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except socket.error:
            return False


@dataclass
class MooncakeTransferEngineConfig:
    prefill_url: str
    decode_url: str
    metadata_backend: Union[str, None]
    metadata_server: str
    protocol: str
    device_name: str

    @staticmethod
    def from_file(file_path: str) -> 'MooncakeTransferEngineConfig':
        """Load the config from a JSON file."""
        with open(file_path) as fin:
            config = json.load(fin)
        return MooncakeTransferEngineConfig(
            prefill_url=config.get("prefill_url"),
            decode_url=config.get("decode_url"),
            metadata_backend=config.get("metadata_backend", None),
            metadata_server=config.get("metadata_server"),
            protocol=config.get("protocol", "tcp"),
            device_name=config.get("device_name", ""),
        )

    @staticmethod
    def load_from_env() -> 'MooncakeTransferEngineConfig':
        """Load config from a file specified in the environment variable."""
        config_file_path = os.getenv('MOONCAKE_CONFIG_PATH')
        if config_file_path is None:
            raise ValueError(
                "The environment variable 'MOONCAKE_CONFIG_PATH' is not set.")
        return MooncakeTransferEngineConfig.from_file(config_file_path)

    @staticmethod
    def from_dict(config_dict: dict[str, any]) -> 'MooncakeTransferEngineConfig':
        """
        Create a new config instance from a dictionary.
        Mimics the logic of `from_file`.
        """
        return MooncakeTransferEngineConfig(
            prefill_url=config_dict.get("prefill_url"),
            decode_url=config_dict.get("decode_url"),
            metadata_backend=config_dict.get("metadata_backend", None),
            metadata_server=config_dict.get("metadata_server"),
            protocol=config_dict.get("protocol", "tcp"),
            device_name=config_dict.get("device_name", ""),
        )

    def merge(self, config_data: dict[str, any]) -> 'MooncakeTransferEngineConfig':
        """
        Merge the current configuration with the provided dictionary.
        Fields are updated one by one, with explicit handling of defaults.
        """
        return MooncakeTransferEngineConfig(
            prefill_url=config_data.get("prefill_url", self.prefill_url),
            decode_url=config_data.get("decode_url", self.decode_url),
            metadata_backend=config_data.get("metadata_backend", None),
            metadata_server=config_data.get("metadata_server", self.metadata_server),
            protocol=config_data.get("protocol", self.protocol),
            device_name=config_data.get("device_name", self.device_name),
        )


class MooncakeTransferEngine:
    """Handles the transfer of data using mooncake_vllm_adaptor and ZeroMQ."""

    def __init__(self, mooncake_config: MooncakeTransferEngineConfig, kv_rank: int, local_rank: int):
        try:
            from mooncake.engine import TransferEngine
        except ImportError as e:
            raise ImportError(
                "Please install mooncake by following the instructions at "
                "https://github.com/kvcache-ai/Mooncake/blob/main/doc/en/build.md "  # noqa: E501
                "to run vLLM with MooncakeConnector.") from e

        self.engine = TransferEngine()
        self.local_rank = local_rank

        self.config = mooncake_config

        def extract_ip_and_port(s):
            if '[' in s:
                ip_part = s.split(']:')[0]  # 提取IP部分并去掉中括号
                ip = ip_part.strip('[]')
                port = s.split(':')[-1]  # 提取端口号
            else:
                ip = s.split(':')[0]
                port = s.split(':')[-1]
            return ip, port

        if '[' in self.config.prefill_url:
            prefill_host, base_prefill_port = extract_ip_and_port(self.config.prefill_url)
            decode_host, base_decode_port = extract_ip_and_port(self.config.decode_url)
            prefill_host = '['+prefill_host+']'
            decode_host = '['+decode_host+']'
        else:
            prefill_host, base_prefill_port = extract_ip_and_port(self.config.prefill_url)
            decode_host, base_decode_port = extract_ip_and_port(self.config.decode_url)

        # Avoid ports conflict when running prefill and decode on the same node
        if prefill_host == decode_host and \
                base_prefill_port == base_decode_port:
            base_decode_port = str(int(base_decode_port) + 100)

        prefill_port = int(base_prefill_port) + self.local_rank
        decode_port = int(base_decode_port) + self.local_rank
        self.prefill_url = ':'.join([prefill_host, str(prefill_port)])
        self.decode_url = ':'.join([decode_host, str(decode_port)])

        self.initialize(self.prefill_url if kv_rank == 0 else self.decode_url,
                        self.config.metadata_server, self.config.protocol,
                        self.config.device_name, self.config.metadata_backend)

        self.remote_url = (self.decode_url
                           if kv_rank == 0 else self.prefill_url)

        # Initialize ZeroMQ context and sockets
        self.context = zmq.Context()  # type: ignore[attr-defined]
        if '[' in prefill_host:
            self.context.setsockopt(zmq.IPV6, 1)
        self.sender_socket = self.context.socket(zmq.constants.PUSH)
        self.receiver_socket = self.context.socket(zmq.constants.PULL)
        self.sender_ack = self.context.socket(zmq.constants.PULL)
        self.receiver_ack = self.context.socket(zmq.constants.PUSH)

        self.buffer_cleaner = ThreadPoolExecutor(max_workers=1)

        self._setup_metadata_sockets(kv_rank, prefill_host, base_prefill_port,
                                     decode_host, base_decode_port)

    def _setup_metadata_sockets(self, kv_rank: int, p_host: str, p_port: str,
                                d_host: str, d_port: str) -> None:
        """Set up ZeroMQ sockets for sending and receiving data."""
        # Offsets < 8 are left for initialization in case tp and pp are enabled
        p_rank_offset = int(p_port) + 8 + self.local_rank * 2
        d_rank_offset = int(d_port) + 8 + self.local_rank * 2
        if kv_rank == 0:
            self.sender_socket.bind(f"tcp://{p_host}:{p_rank_offset + 1}")
            self.receiver_socket.connect(f"tcp://{d_host}:{d_rank_offset + 1}")
            self.sender_ack.connect(f"tcp://{d_host}:{d_rank_offset + 2}")
            self.receiver_ack.bind(f"tcp://{p_host}:{p_rank_offset + 2}")
        else:
            self.receiver_socket.connect(f"tcp://{p_host}:{p_rank_offset + 1}")
            self.sender_socket.bind(f"tcp://{d_host}:{d_rank_offset + 1}")
            self.receiver_ack.bind(f"tcp://{d_host}:{d_rank_offset + 2}")
            self.sender_ack.connect(f"tcp://{p_host}:{p_rank_offset + 2}")

    def initialize(self, local_hostname: str, metadata_server: str,
                   protocol: str, device_name: str,
                   metadata_backend: Union[str, None]) -> None:
        """Initialize the mooncake instance."""
        if metadata_backend is None:
            self.engine.initialize(local_hostname, metadata_server, protocol,
                                   device_name)
        else:
            supported_backend = ["etcd", "redis"]
            metadata_backend = metadata_backend.lower()
            if metadata_backend not in supported_backend:
                raise ValueError(
                    "Mooncake Configuration error. `metadata_backend`"
                    f" should be one of {supported_backend}.")

            self.engine.initialize_ext(local_hostname, metadata_server,
                                       protocol, device_name, metadata_backend)

    def allocate_managed_buffer(self, length: int) -> int:
        """Allocate a managed buffer of the specified length."""
        ret = self.engine.allocate_managed_buffer(length)
        if ret <= 0:
            logger.error("Allocation Return Error")
            raise Exception("Allocation Return Error")
        return ret

    def free_managed_buffer(self, buffer: int, length: int) -> int:
        """Free a previously allocated managed buffer."""
        return self.engine.free_managed_buffer(buffer, length)

    def transfer_sync(self, buffer: int, peer_buffer_address: int,
                      length: int) -> int:
        """Synchronously transfer data to the specified address."""
        ret = self.engine.transfer_sync_read(self.remote_url, buffer,
                                             peer_buffer_address, length)
        if ret < 0:
            logger.error("Transfer Return Error")
            raise Exception("Transfer Return Error")
        return ret

    def write_bytes_to_buffer(self, buffer: int, user_data: bytes,
                              length: int) -> int:
        """Write bytes to the allocated buffer."""
        return self.engine.write_bytes_to_buffer(buffer, user_data, length)

    def read_bytes_from_buffer(self, buffer: int, length: int) -> bytes:
        """Read bytes from the allocated buffer."""
        return self.engine.read_bytes_from_buffer(buffer, length)

    def wait_for_ack(self, src_ptr: int, length: int) -> None:
        """Asynchronously wait for ACK from the receiver."""
        ack = self.sender_ack.recv()
        if ack != b'ACK':
            logger.error("Failed to receive ACK from the receiver")

        self.free_managed_buffer(src_ptr, length)

    def send_bytes(self, user_data: bytes) -> None:
        """Send bytes to the remote process."""
        length = len(user_data)
        src_ptr = self.allocate_managed_buffer(length)
        self.write_bytes_to_buffer(src_ptr, user_data, length)
        self.sender_socket.send_multipart(
            [struct.pack("!Q", src_ptr),
             struct.pack("!Q", length)])
        self.buffer_cleaner.submit(self.wait_for_ack, src_ptr, length)

    def recv_bytes(self) -> bytes:
        """Receive bytes from the remote process."""
        data = self.receiver_socket.recv_multipart()
        src_ptr = struct.unpack("!Q", data[0])[0]
        length = struct.unpack("!Q", data[1])[0]
        dst_ptr = self.allocate_managed_buffer(length)
        self.transfer_sync(dst_ptr, src_ptr, length)
        ret = self.read_bytes_from_buffer(dst_ptr, length)

        # Buffer cleanup
        self.receiver_ack.send(b'ACK')
        self.free_managed_buffer(dst_ptr, length)

        return ret

    def close(self, force=False) -> None:
        self.sender_socket.close()
        self.receiver_socket.close()
        self.sender_ack.close()
        self.receiver_ack.close()
        self.context.term()
        self.buffer_cleaner.shutdown(wait=not force)
        logger.info("Stopped transfer engine.")


class ConfigUpdateHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, pipe=None, **kwargs):
        self.pipe = pipe
        super().__init__(*args, **kwargs)

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self.send_error(400, "No content in request")
                return

            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)

            if self.path == '/update':
                result = self._process_update(data)
            elif self.path == '/close':
                result = self._process_close(data)
            else:
                self.send_error(404, f"Path {self.path} not found")
                return

            if result is True:
                self.send_response(200)
                self.end_headers()
            else:
                self.send_error(500, "Internal Server Error")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON format: {e}")
            self.send_error(400, f"Invalid JSON format: {e}")
        except Exception as e:
            logger.error(f"Error during config update: {e}")
            self.send_error(500, f"Internal server error: {e}")

    def _process_update(self, config_data):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(self.pipe.update_config, config_data)
            try:
                result = future.result()
                logger.info("Scheduler updated config.")
                return True
            except Exception as e:
                return False

    def _process_close(self, data):
        force_stop = data.get('force_stop', None)
        if force_stop is None:
            force_stop = self.pipe.force_stop_on_update
        else:
            if isinstance(force_stop, str):
                force_stop = force_stop.lower() in ("1", "true", "yes")
            elif not isinstance(force_stop, bool):
                raise ValueError("force_stop must be a boolean")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(self.pipe.close_engine, force_stop)
            try:
                result = future.result()
                return True
            except Exception as e:
                return False


class MooncakePipe(KVPipeBase):
    """MooncakeTransferEngine based Pipe implementation."""

    def __init__(self,
                 local_rank: int,
                 config: KVTransferConfig,
                 device: Optional[str] = None):
        """Initialize the mooncake pipe and set related parameters."""
        self.config = config
        self.local_rank = local_rank
        self.kv_rank = self.config.kv_rank
        if device is None:
            self.device = self._select_device(self.config.kv_buffer_device)
        else:
            self.device = self._select_device(device)

        self.transfer_engine: Optional[MooncakeTransferEngine] = None
        self.transport_thread: Optional[ThreadPoolExecutor] = None
        self.none_tensor = torch.tensor([NONE_INT], device=self.device)
        self.mc_config: Optional[MooncakeTransferEngineConfig] = None

        try:
            self.mc_config = MooncakeTransferEngineConfig.load_from_env()
            logger.info("Mooncake Configuration loaded successfully.")
        except ValueError as e:
            logger.error(e)
        except Exception as exc:
            logger.error(
                "An error occurred while loading the configuration: %s", exc)
        # try:
        #     # 根据配置文件初始化一个连接
        #     self.transfer_engine = MooncakeTransferEngine(self.mc_config, self.kv_rank, self.local_rank)
        # except Exception as exc:
        #     logger.error(
        #         "An error occurred while creating the base transfer engine: %s", exc)

        # Start HTTP server for config updates
        self.http_ip = os.getenv("HTTP_IP", "0.0.0.0:8080")  # 监听调度器端口
        self.force_stop_on_update = os.getenv("FORCE_STOP_ON_UPDATE", "1").lower() in ("1", "true", "yes")  # 控制是否强制中断未完成的传输

        self.httpd = None
        self.server_thread = None
        self.start_http_server()

        self.simple_buffer = None  # 用于关闭上一级类 Simple Buffer的引用

    def start_http_server(self):
        try:
            server_address = (self.http_ip.split(":")[0], int(self.http_ip.split(":")[1]))
            handler_class = lambda *args: ConfigUpdateHandler(*args, pipe=self)
            self.httpd = socketserver.TCPServer(server_address, handler_class)
            self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.server_thread.start()
            logger.info(f"Started Scheduler HTTP server on {self.http_ip}")
        except Exception as e:
            logger.error(f"Failed to start Scheduler HTTP server: {e}")
            raise

    def close_engine(self, force=False):
        engine = self.transfer_engine
        try:
            if self.simple_buffer is not None and engine is not None:
                if self.kv_rank == 0 and self.simple_buffer.request_handling_thread is None:
                    pass
                else:
                    if force:
                        self.simple_buffer.force_close()
                    else:
                        self.simple_buffer.close()
            if self.transport_thread is not None:
                self.transport_thread.shutdown(wait=not force)
                self.transport_thread = None
                logger.info("Stopped transport_thread.")
            if engine is not None:
                engine.close(force)
                self.transfer_engine = None
                logger.info("Closed old transfer engine.")
        except Exception as e:
            logger.error(f"Failed to close old transfer engine: {e}")
            self.transfer_engine = engine

    def update_config(self, config_data: dict):
        try:
            if self.transfer_engine:
                old_config = self.transfer_engine.config
                new_config = old_config.merge(config_data)
            else:
                new_config = MooncakeTransferEngineConfig.from_dict(config_data)
            new_engine = MooncakeTransferEngine(new_config, self.kv_rank, self.local_rank)
        except Exception as e:
            logger.error(f"Failed to create new transfer engine: {e}")
        else:
            if self.transfer_engine:
                self.close_engine(self.force_stop_on_update)
            if self.transfer_engine is None:
                self.transfer_engine = new_engine
                logger.info("Created new transfer engine.")

    def _select_device(self, device: str) -> torch.device:
        """Select available device (CUDA or CPU)."""
        logger.info("Selecting device: %s", device)
        if device == "cuda":
            return torch.device(f"cuda:{self.local_rank}")
        else:
            return torch.device("cpu")

    def tensor_hash(self, tensor: torch.Tensor) -> int:
        """Calculate the hash value of the tensor."""
        return hash(tensor.data_ptr())

    def _send_impl(self, tensor: torch.Tensor) -> None:
        """Implement the tensor sending logic using safetensors."""
        if self.transfer_engine is not None:
            try:
                # with open('DEBUG.txt', 'a', buffering=65536) as f:
                #         #logger.info("L DEBUG P1:",time.time())
                #         print("L DEBUG P1:",time.time(),file=f)
                self.transfer_engine.send_bytes(safetensors_save({"tensor": tensor}))
                # with open('DEBUG.txt', 'a', buffering=65536) as f:
                #         #logger.info("L DEBUG P2:",time.time())
                #         print("L DEBUG P2:",time.time(),file=f)
            except Exception as e:
                logger.error(f"Failed to _send_impl: {e}")
        else:
            logger.error("Sending tensors failed: No transfer engine.")

    def _recv_impl(self) -> torch.Tensor:
        """Implement the tensor receiving logic using safetensors."""
        if self.transfer_engine is not None:
            try:
                # with open('DEBUG.txt', 'a', buffering=65536) as f:
                #         #logger.info("L DEBUG D1:",time.time())
                #         print("L DEBUG D1:",time.time(),file=f)
                data = self.transfer_engine.recv_bytes()
                # with open('DEBUG.txt', 'a', buffering=65536) as f:
                #         #logger.info("L DEBUG D2:",time.time())
                #         print("L DEBUG D2:",time.time(),file=f)
                return safetensors_load(data)["tensor"].to(self.device)
            except Exception as e:
                logger.error(f"Failed to _recv_impl: {e}")
                return None
        else:
            logger.error("Receiving tensors failed: No transfer engine.")
            return None

    def send_tensor(self, tensor: Optional[torch.Tensor]) -> None:
        """Send tensor to the target process."""
        if self.transport_thread is None:
            self.transport_thread = ThreadPoolExecutor(max_workers=1)
            logger.info("No transport_thread, new transport_thread created.")
        tensor = tensor if tensor is not None else self.none_tensor
        assert (len(tensor.shape) > 0)
        self.transport_thread.submit(self._send_impl, tensor)

    def recv_tensor(self) -> Optional[torch.Tensor]:
        """Receive tensor from other processes."""
        if self.transport_thread is None:
            self.transport_thread = ThreadPoolExecutor(max_workers=1)
            logger.info("No transport_thread, new transport_thread created.")
        tensor = self.transport_thread.submit(self._recv_impl).result()
        if tensor is not None and tensor.numel() == 1 and tensor.item() == NONE_INT:
            return None
        else:
            return tensor

    def close(self) -> None:
        """Cleanup logic when closing the pipe."""
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.server_thread.join(timeout=5)
                logger.info("Stopping HTTP server.")
            except Exception as e:
                logger.error(f"Failed to shutdown HTTP server: {e}")
        if self.transfer_engine is not None:
            self.transfer_engine.close()
            del self.transfer_engine
            logger.info("Closed the transfer engine and cleaned up resources.")
        else:
            logger.info("Transfer engine closed failed: No transfer engine.")
