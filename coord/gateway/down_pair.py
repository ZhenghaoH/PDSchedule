import requests

from store.gateway.manager import GatewayStoreManager


def send_decode_pairing_command(prefill_instance, decode_proxy, force=False):
    """P池代理向D池代理发送配对指令"""
    try:
        manager = GatewayStoreManager()
        prefill_proxy = f"{manager.host}:{manager.port}"
        url = f"http://{decode_proxy}/pd/pair"
        headers = {"Content-Type": "application/json"}
        payload = {
            "proxy": prefill_proxy,
            "instance": prefill_instance,
            "force": force
        }
        response = requests.post(url, json=payload, headers=headers)
        response_data = response.json()
        if response.status_code == 200:
            return response_data.get("instance", "")
        else:
            message = response_data.get("message", "")
            if message:
                print(f"Get Decode instance failed: {message}")
            return ""
    except Exception as e:
        print("Error occur in send Decode pair command: ", e)
        return ""


def send_instance_pairing_command(instance, prefill, decode, force=False):
    """池代理向实例发送配对指令"""
    try:
        url = f"http://{instance}/pd/pair"
        headers = {"Content-Type": "application/json"}
        payload = {
            "prefill": prefill,
            "decode": decode,
            "force": force
        }
        response = requests.post(url, json=payload, headers=headers)
        response_data = response.json()
        if response.status_code == 200:
            return True
        else:
            message = response_data.get("message", "")
            if message:
                print(f"Pair instances failed: {message}")
            return False
    except Exception as e:
        print("Error occur in send Instance pair command: ", e)
        return False
