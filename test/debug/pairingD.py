import logging
import sys

import requests

from fastapi import Response, Request, status
from fastapi.responses import JSONResponse

import random
import time

from store.gateway.manager import GatewayStoreManager
from config.gatewayD import MOONCAKE_PROTOCOL, MOONCAKE_DEVICE_NAME


# ===================================================
# router.post("/pd/pair")(maintain_pair)
# router.post("/pd/pair/close")(handle_pd_close)
# ===================================================


async def handle_pd_close(raw_request: Request):
    try:
        request = await raw_request.json()
        decode_instance = request.get("decode_instance", "")
        if decode_instance:
            close_d_instance(decode_instance)
            return Response(status_code=status.HTTP_200_OK)
        else:
            return Response(status_code=status.HTTP_400_BAD_REQUEST)
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function handle_pd_close")
        print(error_messages)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


async def maintain_pair(raw_request: Request):
    try:
        request = await raw_request.json()

        prefill_proxy = request.get("prefill_proxy", "")  # prefill接收端口，包含端口
        prefill_instance = request.get("prefill_instance", "")
        is_paired = request.get("is_paired", False)

        if not (prefill_proxy and prefill_instance):
            logging.info("The request is missing parameters: prefill_proxy, prefill_instance")
            return JSONResponse(content={"decode_instance": ""}, status_code=400)

        manager = GatewayStoreManager()
        links = manager.link_table
        decode_instance = ""
        should_close_old_pair = False
        if is_paired:
            # 验证是否确实已有匹配好的路由
            for instance, link in links:
                if link.prefill_instance == prefill_instance:
                    decode_instance = link.decode_instance
        else:
            # 没有已匹配好的，看看是否还有未配对的实例
            free_instances = list(set(manager.decode_objs) - set(links.keys()))
            if free_instances:
                # TODO: 根据一些规则选择未配对实例
                decode_instance = random.choice(free_instances)
            else:
                # TODO: 只能断开已有的连接来选择用于配对的实例
                decode_instance = random.choice(manager.decode_objs)
                should_close_old_pair = True

        if not decode_instance:
            logging.info("ProxyP choose prefill instance failed")
            return JSONResponse(content={"decode_instance": ""}, status_code=500)  # D 代理选择结点失败

        if not is_paired:
            if should_close_old_pair:
                # 处理旧配对
                close_p_success = close_p_instance(decode_instance)  # 先告知p关闭
                if close_p_success:  # 如果关闭p成功了
                    close_d_instance(decode_instance)  # 再关闭自己的d
                else:
                    logging.info("ProxyD close old decode instance failed")
                    return JSONResponse(content={"decode_instance": ""}, status_code=500)
            # 更新配对
            update_pair(prefill_instance, decode_instance, prefill_proxy)

        print("\nPAIR TABLE:", manager.link_table)

        return JSONResponse(content={"decode_instance": decode_instance}, status_code=200)
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function maintain_pair")
        print(error_messages)
        return JSONResponse(content={"decode_instance": ""}, status_code=500)


def close_p_instance(decode_instance):
    """告知P代理也关闭某个个连接"""
    try:
        manager = GatewayStoreManager()
        links = manager.link_table
        pair = links[decode_instance]
        if pair:
            prefill_instance = pair.prefill_instance
            prefill_proxy = pair.prefill_proxy
        else:
            return False
        target_url = f"http://{prefill_proxy}/pd/pair/close"
        payload = {
            "prefill_instance": prefill_instance,  # prefill实例，包含端口
            "decode_instance": decode_instance,
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(target_url, json=payload, headers=headers)
        if response.status_code == 200:
            return True
        else:
            return False  # 判定d代理选择结点失败
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function close_p_instance")
        print(error_messages)
        return False


def close_d_instance(decode_instance):
    """关闭p实例的连接"""
    try:
        # TODO: 下发关闭配对指令
        manager = GatewayStoreManager()
        ip, port = decode_instance.rsplit(":", 1)
        target_url = f"http://{ip}:{int(port) + 100}/close"
        payload = {
            "force_stop": "0"
        }
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            manager.remove_from_link_table(decode_instance)

    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function close_d_instance")
        print(error_messages)


def update_pair(prefill_instance, decode_instance, prefill_proxy):
    """更新新连接 """
    try:
        # TODO: 下发更新配对指令
        manager = GatewayStoreManager()
        prefill_instance_ip, prefill_instance_port = prefill_instance.rsplit(":", 1)
        decode_instance_ip, decode_instance_port = decode_instance.rsplit(":", 1)
        target_url = f"http://{decode_instance_ip}:{int(decode_instance_port) + 100}/update"
        metadata_server = f"http://{prefill_instance_ip}:8080/metadata"
        prefill_url = f"{prefill_instance_ip}:{12000 + (int(prefill_instance_port) * 30) % 1000}"
        decode_url = f"{decode_instance_ip}:{13000 + (int(decode_instance_port) * 30) % 1000}"
        payload = {
            "prefill_url": prefill_url,
            "decode_url": decode_url,
            "metadata_server": metadata_server,
            "protocol": MOONCAKE_PROTOCOL,
            "device_name": MOONCAKE_DEVICE_NAME
        }
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            from store.gateway.link import InstanceLink
            link = InstanceLink(
                prefill_instance=prefill_instance,
                decode_instance=decode_instance,
                prefill_proxy=prefill_proxy
            )
            manager.set_link_table(decode_instance, link)
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function update_pair")
        print(error_messages)
