from fastapi import Response, Request, status
from fastapi.responses import JSONResponse
import random

from store.gateway.manager import GatewayStoreManager
from coord.gateway.down_pair import send_instance_pairing_command


async def handle_pd_pair(raw_request: Request):
    request_json = await raw_request.json()
    prefill_proxy = request_json.get('proxy', "")
    prefill_instance = request_json.get('instance', "")
    force = request_json.get('force', False)
    if not prefill_instance:
        return JSONResponse({'message': 'Missing Parameters'}, status_code=400)

    manager = GatewayStoreManager()
    links = manager.link_table
    decode_instance = ""
    # 非强制模式（检查p实例是否已和本池配对，未配对优先选择空闲实例）
    if not force:
        for instance, link in links.items():
            if link.prefill_instance == prefill_instance:
                decode_instance = link.decode_instance
                return JSONResponse({'message': 'InstanceLink Already Exist'}, status_code=409)
        # 没有已匹配好的，看看是否还有未配对的实例
        free_instances = list(set(manager.decode_objs) - set(links.keys()))
        if free_instances:
            # FIXME: 根据一些规则选择未配对实例
            decode_instance = random.choice(free_instances)
        else:
            # 非强制模式下无可用实例
            return JSONResponse(content={"message": "No Available Decode Instances"}, status_code=204)
    # 强制模式（优先选择空闲实例，无可用实例则断开已有连接）
    else:
        free_instances = list(set(manager.decode_objs) - set(links.keys()))
        if free_instances:
            # FIXME: 根据一些规则选择未配对实例
            decode_instance = random.choice(free_instances)
        else:
            # FIXME：根据适当规则选择要断开的实例
            is_selected = False
            for instance, link in links.items():
                if not link.prefill_instance == prefill_instance:
                    decode_instance = instance
                    is_selected = True
            if not is_selected:
                # 强制模式下无可用实例
                return JSONResponse(content={"message": "No Available Decode Instances"}, status_code=204)
    # 向D实例发送配对指令
    if not send_instance_pairing_command(decode_instance, prefill_instance, decode_instance, force):
        return JSONResponse(content={"message": "Failed to Pair Decode Instance"}, status_code=500)
    # 成功 则存入表中
    from store.gateway.link import InstanceLink
    link = InstanceLink(
        prefill_instance=prefill_instance,
        decode_instance=decode_instance,
        prefill_proxy=prefill_proxy,
        decode_proxy=f"{manager.host}:{manager.port}"
    )
    manager.set_link_table(decode_instance, link)
    return JSONResponse(content={"instance": decode_instance}, status_code=200)


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
