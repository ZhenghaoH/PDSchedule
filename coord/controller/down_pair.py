import requests


def send_pairing_command(prefill, decode, force=False):
    """向PD池发送PD实例配对指令"""
    try:
        url = f"http://{prefill}/pd/pair"
        headers = {"Content-Type": "application/json"}
        payload = {
            "prefill": prefill,
            "decode": decode,
            "force": force
        }
        response = requests.post(url, json=payload, headers=headers)
        response_data = response.json()
        if response.status_code == 200:
            prefill = response_data.get('prefill', "")
            decode = response_data.get('decode', "")
            if prefill and decode:
                print(f"Select: Prefill({prefill}) - Decode({decode})")
            return True
        else:
            message = response_data.get('message', "")
            if message:
                print(f"Error: {message}")
            return False
    except Exception as e:
        print("Error in send_pairing_command, ", e)
        return False

