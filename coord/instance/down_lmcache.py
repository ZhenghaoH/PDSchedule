import requests

from store.instance.store import InstanceStore
from config.ADDRESS_CONFIG import CONFIG_METADATA_IP, CONFIG_METADATA_PORT

storage = InstanceStore()


def update_pair(prefill_instance, decode_instance):
    """TODO：向vLLM实例的LMCache发配对指令"""
    return True


def close_pair():
    """TODO：向vLLM实例的LMCache发关闭配对指令"""
    return True


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
