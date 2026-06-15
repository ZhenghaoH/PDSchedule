import sys

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from scheduler.gateway.decode.async_base import AsyncBaseScheduler
from store.gateway.manager import GatewayStoreManager

from proxy.batcher import RequestBatcher
from proxy.forward import forward_request

from core.schemas.request import BaseRequest


request_batcher = RequestBatcher()
LOG_SEPARATOR = "+" * 80
LOG_ITEM_SEPARATOR = "-" * 80


async def _track_decode_stream(generator, storage: GatewayStoreManager, instance: str, request_id):
    try:
        async for chunk in generator:
            yield chunk
    finally:
        storage.remove_decode_active_request(instance, request_id)


def _log_decode_instance_status(storage: GatewayStoreManager, selected_instance: str) -> None:
    candidates = storage.active_decode_objs
    instances = storage.instance_table
    if not candidates:
        print(
            "\n" + "\n".join([
                LOG_SEPARATOR,
                "[Decode调度] 候选实例负载",
                LOG_SEPARATOR,
                "无可用实例",
                LOG_SEPARATOR,
                "[Decode调度] 已选择实例: ",
                LOG_SEPARATOR,
            ]) + "\n"
        )
        return

    lines = [
        LOG_SEPARATOR,
        "[Decode调度] 候选实例负载",
        LOG_SEPARATOR,
    ]
    for index, instance_ip in enumerate(candidates, start=1):
        instance = instances.get(instance_ip)
        running_reqs = instance.running_reqs if instance else 0
        waiting_reqs = instance.waiting_reqs if instance else 0
        generate_throughput = instance.generate_throughput if instance else 0.0
        kv_cache_usage_percent = instance.kv_cache_usage_percent if instance else 0.0
        gpu_memory_util_percent = instance.gpu_memory_util_percent if instance else 0.0
        lines.extend([
            f"实例 {index}: {instance_ip}",
            f"运行中请求: {running_reqs}",
            f"等待请求: {waiting_reqs}",
            f"KV缓存使用率: {kv_cache_usage_percent:.2f}%",
            f"GPU显存带宽利用率: {gpu_memory_util_percent:.2f}%",
            f"Generate吞吐量: {generate_throughput:.2f} tokens/s",
        ])
        if index != len(candidates):
            lines.append(LOG_ITEM_SEPARATOR)
    lines.extend([
        LOG_SEPARATOR,
        f"[Decode调度] 已选择实例: {selected_instance}",
        LOG_SEPARATOR,
    ])
    print("\n" + "\n".join(lines) + "\n")


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
            strategy = getattr(storage, "DECODE_SCHEDULER_STRATEGY", "slo_tpot_admission")
            decode_instance = await scheduler.schedule_async(
                request,
                strategy=strategy,
            )
            print(f"[Decode调度] strategy={strategy} selected={decode_instance}")
            _log_decode_instance_status(storage, decode_instance)
            request_payload = BaseRequest.to_vllm_payload(request)

            try:
                generator = forward_request(
                    f"http://{decode_instance}/v1/{endpoint_type}", request_payload)
                storage.add_decode_active_request(decode_instance, request)
                generator = _track_decode_stream(
                    generator,
                    storage,
                    decode_instance,
                    request.Request_ID,
                )
            except HTTPException as http_exc:
                storage.remove_decode_active_request(decode_instance, request.Request_ID)
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
