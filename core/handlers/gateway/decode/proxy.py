import sys

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from scheduler.gateway.decode.async_base import AsyncBaseScheduler
from store.gateway.manager import GatewayStoreManager

from proxy.batcher import RequestBatcher
from proxy.forward import forward_request

from core.schemas.request import BaseRequest


request_batcher = RequestBatcher()


async def handle_completion_request(raw_request: Request, endpoint_type: str):
    try:
        # # 提取发送者IP（代理1的IP）
        # sender_ip = raw_request.client.host if raw_request.client else "unknown"
        # sender_port = raw_request.client.port if raw_request.client else "unknown"
        # 转请求格式
        request_json = await raw_request.json()
        request = BaseRequest.recover_request(request_json, endpoint_type)

        storage = GatewayStoreManager()
        if storage.USE_SYN:
            # 使用批处理方式
            batch_result = await request_batcher.add_request(request)
            if batch_result.get("error"):
                raise HTTPException(status_code=500, detail=batch_result["error"])
            if not batch_result.get("success", True):
                raise HTTPException(status_code=500, detail="Batch processing failed")
            # 返回流式响应
            response = StreamingResponse(
                batch_result["generator"],
                media_type="application/json"
            )
            return response
        else:
            scheduler = AsyncBaseScheduler()
            decode_instance = scheduler.schedule(request)
            request_payload = BaseRequest.to_vllm_payload(request)

            try:
                generator = forward_request(
                    f"http://{decode_instance}/v1/{endpoint_type}", request_payload)
            except HTTPException as http_exc:
                raise http_exc
            response = StreamingResponse(generator)
            return response
    except Exception:
        exc_info = sys.exc_info()
        print("Error occurred in handle_completion_request")
        print(exc_info)


async def create_completion(raw_request: Request):
    """处理 /v1/completions 端点"""
    return await handle_completion_request(raw_request, "completions")


async def create_chat_completion(raw_request: Request):
    """处理 /v1/chat/completions 端点"""
    return await handle_completion_request(raw_request, "chat/completions")
