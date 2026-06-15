import logging
import requests
from store.gateway.manager import GatewayStoreManager
from utils.network_metric import get_interface_metrics
from utils.pcie_metric import get_pcie_metrics


def prefill_compute_register():
    """向 Scheduler 注册 Prefill 节点"""
    endpoint = "prefill_pool/register"
    storage = GatewayStoreManager()
    target_url = f"http://{storage.controller}/{endpoint}"

    gpu_type, flops, _, max_prompt_throughput = storage.get_compute_params()
    payload = {
        "proxy_ip_address": storage.host,
        "proxy_port": storage.port,
        "flops": flops,
        "GPU_type": gpu_type,
        "max_prompt_throughput": max_prompt_throughput
    }

    logging.info(f"[Node] 正在发起注册...")
    try:
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            data = response.json()
            storage.set_pool_id(data.get("pool_id"))
            logging.info(f"[Node] 注册成功! ID: {storage.get_pool_id()}")
            return True
        else:
            logging.error(f"[Node] 注册失败: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logging.error(f"[Node] 连接失败: {e}")
        return False


def prefill_compute_update():
    """发送节点负载心跳"""
    endpoint = "prefill_pool/update"
    storage = GatewayStoreManager()
    target_url = f"http://{storage.controller}/{endpoint}"

    pool_id = storage.get_pool_id()
    load = storage.get_prefill_load()
    payload = {
        "pool_id": pool_id,
        "current_load": load
    }

    try:
        resp = requests.post(target_url, json=payload)
        if resp.status_code == 404:
            logging.warning("[Node] 未注册，重试...")
            storage.set_pool_id(None)
        elif resp.status_code != 200:
            logging.warning(f"[Node] 心跳异常: {resp.status_code}")
    except Exception as e:
        logging.error(f"[Node] 心跳错误: {e}")


def prefill_compute_unregister():
    """向 Scheduler 注销 Prefill 节点"""
    endpoint = "prefill_pool/unregister"
    storage = GatewayStoreManager()
    target_url = f"http://{storage.controller}/{endpoint}"
    pool_id = storage.get_pool_id()
    if pool_id is None:
        return False

    try:
        resp = requests.post(target_url, json={"pool_id": pool_id})
        if resp.status_code == 200:
            storage.set_pool_id(None)
            logging.info(f"[Node] 注销成功: Prefill Pool {pool_id}")
            return True
        logging.warning(f"[Node] 注销异常: {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        logging.warning(f"[Node] 注销错误: {e}")
        return False


def decode_compute_register():
    """向 Scheduler 注册 Decode 节点"""
    endpoint = "decode_pool/register"
    storage = GatewayStoreManager()
    target_url = f"http://{storage.controller}/{endpoint}"

    gpu_type, _, mem_bandwidth, _ = storage.get_compute_params()
    max_decode_req_num = storage.get_max_decode_req_num()
    payload = {
        "proxy_ip_address": storage.host,
        "proxy_port": storage.port,
        "mem_bandwidth": mem_bandwidth,
        "GPU_type": gpu_type,
        "max_decode_req_num": max_decode_req_num,
    }

    logging.info(f"[Node] 正在发起注册...")
    try:
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            data = response.json()
            storage.set_pool_id(data.get("pool_id"))
            logging.info(f"[Node] 注册成功! ID: {storage.get_pool_id()}")
            return True
        else:
            logging.error(f"[Node] 注册失败: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logging.error(f"[Node] 连接失败: {e}")
        return False


def decode_compute_update():
    """发送节点负载心跳"""
    endpoint = "decode_pool/update"
    storage = GatewayStoreManager()
    target_url = f"http://{storage.controller}/{endpoint}"

    pool_id = storage.get_pool_id()
    running_reqs = storage.get_decode_load()
    max_decode_req_num = storage.get_max_decode_req_num()
    load = running_reqs / max_decode_req_num if max_decode_req_num > 0 else 0.0
    payload = {
        "pool_id": pool_id,
        "current_load": load,
        "running_reqs": running_reqs,
        "max_decode_req_num": max_decode_req_num,
    }

    try:
        resp = requests.post(target_url, json=payload)
        if resp.status_code == 404:
            logging.warning("[Node] 未注册，重试...")
            storage.set_pool_id(None)
        elif resp.status_code != 200:
            logging.warning(f"[Node] 心跳异常: {resp.status_code}")
    except Exception as e:
        logging.error(f"[Node] 心跳错误: {e}")


def decode_compute_unregister():
    """向 Scheduler 注销 Decode 节点"""
    endpoint = "decode_pool/unregister"
    storage = GatewayStoreManager()
    target_url = f"http://{storage.controller}/{endpoint}"
    pool_id = storage.get_pool_id()
    if pool_id is None:
        return False

    try:
        resp = requests.post(target_url, json={"pool_id": pool_id})
        if resp.status_code == 200:
            storage.set_pool_id(None)
            logging.info(f"[Node] 注销成功: Decode Pool {pool_id}")
            return True
        logging.warning(f"[Node] 注销异常: {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        logging.warning(f"[Node] 注销错误: {e}")
        return False


def network_register():
    """向 Scheduler 注册网络链路"""
    endpoint = "network/register"
    storage = GatewayStoreManager()
    target_url = f"http://{storage.controller}/{endpoint}"

    try:
        # 仅上报自己的 IP 和端口
        payload = {
            "gateway_ip": storage.host,
            "gateway_port": storage.port,
            "bandwidth": 1000.0  # 默认带宽
        }
        
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            logging.info(f"[Network] 链路注册成功: {storage.host}:{storage.port}")
            return True
        else:
            # 404 可能是对端 Decode 还没注册，稍后重试
            logging.warning(f"[Network] 链路注册失败 ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logging.error(f"[Network] 链路注册异常: {e}")
        return False


def network_update():
    """向 Scheduler 更新网络链路"""
    endpoint = "network/update"
    storage = GatewayStoreManager()
    target_url = f"http://{storage.controller}/{endpoint}"

    try:
        # 获取当前本机网口的实际可用带宽
        net_metrics = get_interface_metrics(storage.host)
        pcie_metrics = get_pcie_metrics()

        # 如果获取失败，available_bw 为 None，后续逻辑回退到默认值
        available_bw = net_metrics['available_mbps'] if net_metrics else None
        nic_rtt = 10.0
        pcie_bandwidth = pcie_metrics["bandwidth"]
        pcie_rtt = pcie_metrics["rtt"]

        # 上报自己的 IP、端口、网卡带宽，以及同机链路可用的 PCIe 带宽
        payload = {
            "gateway_ip": storage.host,
            "gateway_port": storage.port,
            "bandwidth": available_bw if available_bw else 1000.0,
            "rtt": nic_rtt,
            "pcie_bandwidth": pcie_bandwidth,
            "pcie_rtt": pcie_rtt,
        }
        
        resp = requests.post(target_url, json=payload)
        if resp.status_code == 200:
            logging.debug(
                f"[Network] 更新: {storage.host}:{storage.port} | "
                f"NIC_BW:{payload['bandwidth']} | PCIE_BW:{payload['pcie_bandwidth']}"
            )
        elif resp.status_code == 404:
            logging.warning(f"[Network] 链路404，重置注册状态: {storage.host}:{storage.port}")
        else:
            logging.warning(f"[Network] 更新异常: {resp.status_code}")
    except Exception as e:
        logging.error(f"[Network] 更新请求出错: {e}")
