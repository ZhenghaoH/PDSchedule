import sys
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from proxy.forward import forward_request
from store.instance.store import InstanceStore
from instance.simulator.mock_resp import mock_chat, mock_completion


async def handle_completion_request(raw_request: Request, endpoint_type: str):
    # try:
    # # 提取发送者IP（代理1的IP）
    # sender_ip = raw_request.client.host if raw_request.client else "unknown"
    # sender_port = raw_request.client.port if raw_request.client else "unknown"
    # 转请求格式
    request_json = await raw_request.json()

    storage = InstanceStore()
    if storage.USE_MOCK:
        if endpoint_type == 'completions':
            return mock_completion(request_json)
        else:
            return mock_chat(request_json)
    else:
        try:
            generator = forward_request(
                f"http://{storage.vllm_addr()}/v1/{endpoint_type}", request_json)
        except HTTPException as http_exc:
            raise http_exc
        response = StreamingResponse(generator)
        return response
    # except Exception:
    #     exc_info = sys.exc_info()
    #     print("Error occurred in handle_completion_request")
    #     print(exc_info)


async def create_completion(raw_request: Request):
    """处理 /v1/completions 端点"""
    return await handle_completion_request(raw_request, "completions")


async def create_chat_completion(raw_request: Request):
    """处理 /v1/chat/completions 端点"""
    return await handle_completion_request(raw_request, "chat/completions")
