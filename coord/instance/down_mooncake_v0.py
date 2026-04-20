import requests

from store.instance.store import InstanceStore
from config.ADDRESS_CONFIG import CONFIG_METADATA_IP, CONFIG_METADATA_PORT

storage = InstanceStore()


def update_pair(prefill_instance, decode_instance):
    """向vLLM实例的MooncakePipe发配对指令"""
    try:
        target_url = f"http://{storage.host}:{storage.vllm_port + 100}/update"
        prefill_instance_ip, prefill_instance_port = prefill_instance.rsplit(":", 1)
        decode_instance_ip, decode_instance_port = decode_instance.rsplit(":", 1)
        # metadata_server = f"http://{prefill_instance_ip}:{8080}/metadata"
        metadata_server = f"http://{CONFIG_METADATA_IP}:{CONFIG_METADATA_PORT}/metadata"
        prefill_url = f"{prefill_instance_ip}:{12000 + (int(prefill_instance_port) * 30) % 1000}"
        decode_url = f"{decode_instance_ip}:{13000 + (int(decode_instance_port) * 30) % 1000}"
        payload = {
            "prefill_url": prefill_url,
            "decode_url": decode_url,
            "metadata_server": metadata_server,
            "protocol": storage.MOONCAKE_PROTOCOL,
            "device_name": storage.MOONCAKE_DEVICE_NAME
        }
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            return True
        else:
            return False
    except Exception as e:
        print("Error occurred in function update_pair, ", e)
        return False


def close_pair():
    """向vLLM实例的MooncakePipe发关闭配对指令"""
    try:
        target_url = f"http://{storage.host}:{storage.vllm_port + 100}/close"
        payload = {
            "force_stop": "1"
        }
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            return True
        else:
            return False
    except Exception as e:
        print("Error occurred in function close_pair, ", e)
        return False


def close_peer():
    """向对端实例代理发配对关闭通知"""
    try:
        target_url = f"http://{storage.peer}/pd/close"
        payload = {
            "peer": f"{storage.host}:{storage.port}",
        }
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            return True
        else:
            return False
    except Exception as e:
        print("Error occurred in function close_peer, ", e)
        return False
