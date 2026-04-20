import logging
import sys

import requests

from fastapi import Response, Request, status

import random
import time

from store.gateway.manager import GatewayStoreManager
from config.gatewayP import MOONCAKE_PROTOCOL, MOONCAKE_DEVICE_NAME


# ==============================================
# router.post("/pd/pair")(maintain_pair)
# router.post("/pd/pair/close")(handle_pd_close)
# ==============================================


async def handle_pd_close(raw_request: Request):
    try:
        request = await raw_request.json()
        prefill_instance = request.get("prefill_instance", "")
        if prefill_instance:
            close_p_instance(prefill_instance)
            return Response(status_code=status.HTTP_200_OK)
        else:
            logging.info("The request is missing parameters: prefill_instance")
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

        decode_proxy = request.get("decode_proxy", "")  # decode接收端口，包含端口

        if not decode_proxy:
            logging.info("The request is missing parameters: decode_proxy")
            return Response(status_code=status.HTTP_400_BAD_REQUEST)

        # 检索是否已有匹配好的路由
        is_paired = False
        prefill_instance = ""
        manager = GatewayStoreManager()
        links = manager.link_table
        for instance, link in links:
            if link.decode_proxy == decode_proxy:
                # TODO: 根据 metric 轮询选择合适的实例
                is_paired = True
                prefill_instance = instance

        # 没有已匹配好的，看看是否还有未配对的实例
        should_close_old_pair = False
        if not is_paired:
            links = manager.link_table
            free_instances = list(set(manager.prefill_objs) - set(links.keys()))
            if free_instances:
                # TODO: 根据一些规则选择未配对实例
                prefill_instance = random.choice(free_instances)
            else:
                # TODO: 只能断开已有的连接来选择用于配对的实例
                prefill_instance = random.choice(manager.prefill_objs)
                should_close_old_pair = True

        if not prefill_instance:
            logging.info("ProxyP choose prefill instance failed")
            return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)  # P代理选择可用结点失败

        # 通告 D代理 调度指令
        prefill_proxy = manager.host + ":" + str(manager.port)
        decode_instance = maintain_pair_d(prefill_proxy, decode_proxy, prefill_instance, is_paired)

        if not decode_instance:
            logging.info("ProxyD choose decode instance failed")
            return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)  # D 代理选择结点失败

        if not is_paired:
            if should_close_old_pair:
                # 处理旧配对
                close_d_success = close_d_instance(prefill_instance)  # 先告知d关闭
                if close_d_success:  # 如果关闭D成功了
                    close_p_instance(prefill_instance)  # 再关闭自己的p
                else:
                    logging.info("ProxyP close old prefill instance failed")
                    return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            # 更新配对
            update_pair(prefill_instance, decode_instance, decode_proxy)

        print("\nPAIR TABLE:", manager.link_table)

        return Response(status_code=status.HTTP_200_OK)
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function maintain_pair")
        print(error_messages)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


def maintain_pair_d(prefill_proxy, decode_proxy, prefill_instance, is_paired):
    """向D代理发送调度指令"""
    try:
        target_url = f"http://{decode_proxy}/pd/pair"
        payload = {
            "prefill_instance": prefill_instance,  # prefill实例，包含端口
            "prefill_proxy": prefill_proxy,  # P代理接收端口，包含端口
            "is_paired": is_paired  # 是否是已经存在的配对
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(target_url, json=payload, headers=headers)
        if response.status_code == 200:
            # 解析响应中的 JSON 数据
            response_data = response.json()
            decode_instance = response_data.get("decode_instance")
            return decode_instance
        else:
            return ""  # 判定d代理选择结点失败
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function maintain_pair_d")
        print(error_messages)
        return ""


def close_d_instance(prefill_instance):
    """告知D代理也关闭某个个连接"""
    try:
        manager = GatewayStoreManager()
        links = manager.link_table
        pair = links[prefill_instance]
        if pair:
            decode_instance = pair.decode_instance
            decode_proxy = pair.decode_proxy
        else:
            return False
        target_url = f"http://{decode_proxy}/pd/pair/close"
        payload = {
            "decode_instance": decode_instance,
            "prefill_instance": prefill_instance,  # prefill实例，包含端口
            "prefill_proxy": manager.host + ":" + str(manager.port),  # P代理接收端口，包含端口
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
        print("Error occurred in function close_d_instance")
        print(error_messages)
        return False


def close_p_instance(prefill_instance):
    """关闭p实例的连接"""
    try:
        # TODO: 下发关闭配对指令
        manager = GatewayStoreManager()
        ip, port = prefill_instance.rsplit(":", 1)
        target_url = f"http://{ip}:{int(port) + 100}/close"
        payload = {
            "force_stop": "1"
        }
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            manager.remove_from_link_table(prefill_instance)
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function close_p_instance")
        print(error_messages)


def update_pair(prefill_instance, decode_instance, decode_proxy):
    """更新新连接 """
    try:
        # TODO: 下发更新配对指令
        manager = GatewayStoreManager()
        prefill_instance_ip, prefill_instance_port = prefill_instance.rsplit(":", 1)
        decode_instance_ip, decode_instance_port = decode_instance.rsplit(":", 1)
        target_url = f"http://{prefill_instance_ip}:{int(prefill_instance_port) + 100}/update"
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
                decode_proxy=decode_proxy
            )
            manager.set_link_table(prefill_instance, link)
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function update_pair")
        print(error_messages)
