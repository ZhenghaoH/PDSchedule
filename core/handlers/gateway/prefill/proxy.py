import sys
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from scheduler.gateway.prefill.async_base import AsyncBaseScheduler
from store.gateway.manager import GatewayStoreManager

from proxy.sync import RequestBatcher
from proxy.forward import forward_request_trace as forward_request

from core.schemas.request import BaseRequest

from core.handlers.gateway.prefill.kdn_proxy import kdn_proxy_chat_completions, kdn_proxy_completions


request_batcher = RequestBatcher()


async def handle_completion_request(raw_request: Request, request: BaseRequest, endpoint_type: str):
    try:
        # 提取发送者IP（用户/全局调度器 的IP）
        # sender_ip = raw_request.client.host if raw_request.client else "unknown"
        # sender_port = raw_request.client.port if raw_request.client else "unknown"
        # 转请求格式
        # request_json = await raw_request.json()
        # request = BaseRequest.recover_request(request_json, endpoint_type)

        max_tokens = request.Prompt.max_tokens  # 暂存原需求，因为prefill的max token须为1
        if request.Service.Enable_PD_Disaggregation:
            # 如果启用PD分离,prefill应设置max token为1
            request.Prompt.max_tokens = 1

        storage = GatewayStoreManager()
        if storage.USE_SYN:
            # 使用批处理方式
            batch_result = await request_batcher.add_request(request)
            if batch_result.get("error"):
                raise HTTPException(status_code=500, detail=batch_result["error"])
            if not batch_result.get("success", True):
                raise HTTPException(status_code=500, detail="Batch processing failed")
            # 返回流式响应
            prefill_generator = batch_result["generator"]
            request.Task.prefill_instance = batch_result["instance"]
        else:
            scheduler = AsyncBaseScheduler()
            prefill_instance = scheduler.schedule(request, "prefill")
            request_payload = BaseRequest.to_vllm_payload(request)
            request.Task.prefill_instance = prefill_instance
            try:
                prefill_generator = forward_request(
                    f"http://{prefill_instance}/v1/{endpoint_type}", request_payload)
            except HTTPException as http_exc:
                raise http_exc

        if request.Service.Enable_PD_Disaggregation:
            # 如果启用PD分离, 不需要prefill响应，可以空跑消耗
            async for _ in prefill_generator:
                continue
            request.Prompt.max_tokens = max_tokens
            scheduler = AsyncBaseScheduler()
            decode_proxy = scheduler.schedule(request, "decode")
            request_payload = BaseRequest.to_decode_payload(request)
            try:
                decode_generator = forward_request(
                    f"http://{decode_proxy}/v1/{endpoint_type}",
                    request_payload,
                    arrival_time=request.Service.arrival_time,
                    slo_sec=request.Service.SLO_TTFT / 1000.0,
                    request_id=request.Request_ID,
                    stats_callback=request_batcher.monitor_engine.handle_ttft_stats   # 关键：接入调度器
                )
            except HTTPException as http_exc:
                raise http_exc
            # 启用PD分离，返回Decode响应
            response = StreamingResponse(decode_generator)
            return response
        else:
            # 不启用PD分离，返回Prefill响应
            response = StreamingResponse(prefill_generator)
            return response
    except Exception:
        exc_info = sys.exc_info()
        print("Error occurred in handle_completion_request")
        print(exc_info)


async def create_completion(raw_request: Request):
    """处理 /v1/completions 端点"""
    endpoint_type = "completions"
    request_json = await raw_request.json()
    request = BaseRequest.recover_request(request_json, endpoint_type)

    enable_rag = bool(getattr(request.Service, "Enable_know_injection", False))
    knowledge_ids = getattr(request.Service, "Knowledge_List", []) or []
    if enable_rag and knowledge_ids:
        return await kdn_proxy_completions(raw_request, request)
    else:
        return await handle_completion_request(raw_request, request, endpoint_type)


async def create_chat_completion(raw_request: Request):
    """处理 /v1/chat/completions 端点"""
    endpoint_type = "chat/completions"
    request_json = await raw_request.json()
    request = BaseRequest.recover_request(request_json, endpoint_type)

    enable_rag = bool(getattr(request.Service, "Enable_know_injection", False))
    knowledge_ids = getattr(request.Service, "Knowledge_List", []) or []
    if enable_rag and knowledge_ids:
        return await kdn_proxy_chat_completions(raw_request, request)
    else:
        return await handle_completion_request(raw_request, request, endpoint_type)
