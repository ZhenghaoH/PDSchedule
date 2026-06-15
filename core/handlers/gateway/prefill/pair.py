from fastapi import Response, Request, status
from fastapi.responses import JSONResponse
import random

from store.gateway.manager import GatewayStoreManager
from coord.gateway.down_pair import send_decode_pairing_command, send_instance_pairing_command
from coord.gateway.up_store import network_register


async def handle_pd_pair(raw_request: Request):
    request_json = await raw_request.json()
    prefill_proxy = request_json.get('prefill', "")
    decode_proxy = request_json.get('decode', "")
    force = request_json.get('force', False)
    if not decode_proxy:
        return JSONResponse(content={"message": "Missing Parameters"}, status_code=400)

    prefill_instance = ""
    decode_instance = ""
    manager = GatewayStoreManager()
    links = manager.link_table
    free_instances = list(set(manager.active_prefill_objs) - set(links.keys()))
    # 非强制模式（只维持两池之间的一条配对，若配对已存在则不管，若配对不存在则创建）
    if not force:
        # 检索是否已有匹配好的路由
        for instance, link in links.items():
            # 有已匹配好的，在非强制模式下就不用再找了
            if link.decode_proxy == decode_proxy:
                # FIXME: 根据 适当规则 选择合适的实例配对进行返回
                prefill_instance = instance
                decode_instance = link.decode_instance
                if not network_register():
                    return JSONResponse(content={"message": "Failed to Register Network"}, status_code=500)
                return JSONResponse(content={"prefill": prefill_instance, "decode": decode_instance}, status_code=200)
        # 没有已匹配好的，看看是否还有未配对的实例
        if free_instances:
            # 有空闲实例
            # FIXME: 根据一些规则选择未配对实例
            prefill_instance = random.choice(free_instances)
        else:
            # 非强制模式下无可用实例
            return JSONResponse(content={"message": "No Available Prefill Instances"}, status_code=204)
    # 强制模式（强制在两池间建立一条新配对，已存在则再建一条，无可用实例则断开已有连接）
    else:
        # 优先选择空闲实例去配对
        if free_instances:
            # FIXME: 根据一些规则选择未配对实例
            prefill_instance = random.choice(free_instances)
        # 否则选择断开旧实例
        else:
            # FIXME：根据适当规则选择要断开的实例
            is_selected = False
            for instance, link in links.items():
                if not link.decode_proxy == decode_proxy:
                    prefill_instance = instance
                    is_selected = True
            if not is_selected:
                # 强制模式下无可用实例
                return JSONResponse(content={"message": "No Available Prefill Instances"}, status_code=204)
    # 向D池发送配对指令
    decode_instance = send_decode_pairing_command(prefill_instance, decode_proxy, force)
    if not decode_instance:
        # 获取D实例失败
        return JSONResponse(content={"message": "Failed to Get Decode Instance"}, status_code=500)
    # 向P实例发送请求
    if not send_instance_pairing_command(prefill_instance, prefill_instance, decode_instance, force):
        return JSONResponse(content={"message": "Failed to Pair Prefill Instance"}, status_code=500)
    # 成功 则存入表中
    from store.gateway.link import InstanceLink
    link = InstanceLink(
        prefill_instance=prefill_instance,
        decode_instance=decode_instance,
        prefill_proxy=f"{manager.host}:{manager.port}",
        decode_proxy=decode_proxy
    )
    manager.set_link_table(prefill_instance, link)
    if not network_register():
        return JSONResponse(content={"message": "Failed to Register Network"}, status_code=500)
    # TODO: 如果 Decode 成功了， Prefill 失败了如何告知 Decode ？
    return JSONResponse(content={"prefill": prefill_instance, "decode": decode_instance}, status_code=200)


async def handle_pd_close(raw_request: Request):
    try:
        request_json = await raw_request.json()
        instance = request_json.get('label', "")
        if not instance:
            return JSONResponse(content={"message": "Missing Parameters"}, status_code=400)
        manager = GatewayStoreManager()
        manager.remove_from_link_table(instance)
        return JSONResponse({"message": "OK"}, status_code=status.HTTP_200_OK)
    except Exception as e:
        return JSONResponse(content={"message": str(e)}, status_code=500)
