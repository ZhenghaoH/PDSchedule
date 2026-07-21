# main.py
"""
主程序：启动调度服务器
- 仅负责应用初始化和启动
- 通过config.py获取默认配置
- 保持原run_server的启动逻辑
"""

import argparse
import uvicorn
from fastapi import FastAPI
import logging
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from config.instanceP import *

from store.instance.store import InstanceStore
from core.routers.instance import router
from core.lifespans.instance import lifespan


def parse_gpu_indices(value: str):
    try:
        indices = [int(item.strip()) for item in str(value).split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("GPU indices must be comma-separated integers") from exc
    if not indices or any(index < 0 for index in indices) or len(indices) != len(set(indices)):
        raise argparse.ArgumentTypeError("GPU indices must be unique non-negative integers")
    return indices


def setup_logger():
    """配置全局日志系统"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )
    # 为 root logger 设置格式
    logging.getLogger().setLevel(logging.INFO)


def parse_args():
    """解析命令行参数（使用config中的默认值）"""
    parser = argparse.ArgumentParser("vLLM instance proxy server.")

    parser.add_argument(
        "--gateway",
        type=str,
        default=DEFAULT_GATEWAY,
        help="The IP address and port for the gateway server to listen on. Supports IPv4 and IPv6."
    )

    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Server port number",
    )

    parser.add_argument(
        "--vllm_port",
        type=int,
        default=VLLM_PORT,
        help="vLLM port number",
    )

    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_HOST,
        help="The IP address for the proxy server to listen on. Supports IPv4 and IPv6."
    )
    parser.add_argument("--side-channel-host", default=os.getenv("VLLM_NIXL_SIDE_CHANNEL_HOST", ""))
    parser.add_argument("--side-channel-port", type=int, default=int(os.getenv("VLLM_NIXL_SIDE_CHANNEL_PORT", "0")))
    parser.add_argument("--tp-size", type=int, default=int(os.getenv("FLEXPD_TP_SIZE", "0")))
    parser.add_argument("--connector", default=NIXL_CONNECTOR)
    parser.add_argument(
        "--gpu-indices",
        type=parse_gpu_indices,
        default=parse_gpu_indices(os.getenv("FLEXPD_GPU_INDICES", "0")),
        help="Physical GPU indices used by this instance proxy, e.g. 0 or 0,1.",
    )
    return parser.parse_args()


def main():
    """启动调度服务器"""
    # 初始化全局日志
    setup_logger()

    # 解析命令行参数
    args = parse_args()
    missing = [name for name, value in {
        "host": args.host, "port": args.port, "vllm_port": args.vllm_port,
        "gateway": args.gateway, "side_channel_host": args.side_channel_host,
        "side_channel_port": args.side_channel_port, "tp_size": args.tp_size,
    }.items() if value in (None, "", 0)]
    if missing:
        raise SystemExit(f"Missing required deployment options: {', '.join(missing)}")

    # 初始化核心组件
    storage = InstanceStore()
    storage.host = args.host
    storage.port = args.port
    storage.gateway = args.gateway
    storage.vllm_port = args.vllm_port
    storage.role = PREFILL_ROLE
    storage.connector = args.connector
    storage.side_channel_host = args.side_channel_host
    storage.side_channel_port = args.side_channel_port
    storage.tp_size = args.tp_size
    storage.gpu_indices = list(args.gpu_indices)

    storage.rank = RANK
    storage.USE_MOCK = USE_MOCK
    storage.MOONCAKE_PROTOCOL = MOONCAKE_PROTOCOL
    storage.MOONCAKE_DEVICE_NAME = MOONCAKE_DEVICE_NAME
    storage.LOOP_TASK_TIME = LOOP_TASK_TIME
    storage.KV_CONNECTOR = KV_CONNECTOR

    # 创建FastAPI应用
    app = FastAPI(
        title="Instance",
        lifespan=lifespan
    )

    # 注册路由
    app.include_router(router)

    # 启动服务器
    # uvicorn.run(app, host=args.host, port=args.port, log_level="info", loop="uvloop")  # ubuntu
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")  # windows


if __name__ == "__main__":
    main()
