import sys

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from config.ADDRESS_CONFIG import (
    get_nixl_meta_for_instance,
    get_side_channel_addr_for_instance,
)
from core.schemas.request import BaseRequest
from proxy.batcher import RequestBatcher
from proxy.forward import forward_request
from scheduler.gateway.decode.async_base import AsyncBaseScheduler
from store.gateway.manager import GatewayStoreManager
from utils.nixl_request import build_nixl_decode_payload, nixl_headers_for_request


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
                "[Decode Schedule] Candidate instances",
                LOG_SEPARATOR,
                "No active instance",
                LOG_SEPARATOR,
                "[Decode Schedule] Selected instance: ",
                LOG_SEPARATOR,
            ]) + "\n"
        )
        return

    lines = [
        LOG_SEPARATOR,
        "[Decode Schedule] Candidate instances",
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
            f"Instance {index}: {instance_ip}",
            f"Running requests: {running_reqs}",
            f"Waiting requests: {waiting_reqs}",
            f"KV cache usage: {kv_cache_usage_percent:.2f}%",
            f"GPU memory util: {gpu_memory_util_percent:.2f}%",
            f"Generate throughput: {generate_throughput:.2f} tokens/s",
        ])
        if index != len(candidates):
            lines.append(LOG_ITEM_SEPARATOR)
    lines.extend([
        LOG_SEPARATOR,
        f"[Decode Schedule] Selected instance: {selected_instance}",
        LOG_SEPARATOR,
    ])
    print("\n" + "\n".join(lines) + "\n")


def _active_decode_instance(storage: GatewayStoreManager, instance_addr: str) -> bool:
    return bool(instance_addr and instance_addr in storage.active_decode_objs)


async def _select_decode_instance(storage: GatewayStoreManager, request: BaseRequest) -> str:
    selected_decode = getattr(request.Task, "decode_instance", "")
    if _active_decode_instance(storage, selected_decode):
        _log_decode_instance_status(storage, selected_decode)
        return selected_decode

    scheduler = AsyncBaseScheduler(storage)
    strategy = getattr(storage, "DECODE_SCHEDULER_STRATEGY", "slo_tpot_admission")
    selected_decode = await scheduler.schedule_async(request, strategy=strategy)
    if not _active_decode_instance(storage, selected_decode):
        raise HTTPException(status_code=503, detail="No active decode instance")

    request.Task.decode_instance = selected_decode
    print(f"[Decode Schedule] strategy={strategy} selected={selected_decode}")
    _log_decode_instance_status(storage, selected_decode)
    return selected_decode


def _bind_and_validate_nixl_metadata(request: BaseRequest, selected_decode: str) -> None:
    prefill_instance = getattr(request.Task, "prefill_instance", "")
    prefill_meta = get_nixl_meta_for_instance(prefill_instance)
    decode_meta = get_nixl_meta_for_instance(selected_decode)

    if not prefill_meta:
        raise HTTPException(status_code=503, detail=f"Missing NIXL metadata for prefill instance {prefill_instance}")
    if not decode_meta:
        raise HTTPException(status_code=503, detail=f"Missing NIXL metadata for decode instance {selected_decode}")
    if prefill_meta.get("connector") != "NixlConnector" or prefill_meta.get("role") != "prefill":
        raise HTTPException(status_code=503, detail=f"Invalid prefill NIXL metadata: {prefill_meta}")
    if decode_meta.get("connector") != "NixlConnector" or decode_meta.get("role") != "decode":
        raise HTTPException(status_code=503, detail=f"Invalid decode NIXL metadata: {decode_meta}")

    request.Task.prefill_tp_size = int(prefill_meta.get("tp_size") or 0)
    request.Task.decode_tp_size = int(decode_meta.get("tp_size") or 0)
    request.Task.prefill_side_channel_addr = get_side_channel_addr_for_instance(prefill_instance)
    request.Task.decode_side_channel_addr = get_side_channel_addr_for_instance(selected_decode)


async def handle_completion_request(raw_request: Request, endpoint_type: str):
    try:
        request_json = await raw_request.json()
        request = BaseRequest.recover_request(request_json, endpoint_type)

        storage = GatewayStoreManager()
        if storage.USE_SYN:
            batch_result = await request_batcher.add_request(request)
            if batch_result.get("error"):
                raise HTTPException(status_code=500, detail=batch_result["error"])
            if not batch_result.get("success", True):
                raise HTTPException(status_code=500, detail="Batch processing failed")
            return StreamingResponse(batch_result["generator"], media_type="application/json")

        selected_decode = await _select_decode_instance(storage, request)
        if request.Service.Enable_PD_Disaggregation:
            _bind_and_validate_nixl_metadata(request, selected_decode)
            request_payload = build_nixl_decode_payload(request)
        else:
            request_payload = BaseRequest.to_vllm_payload(request)

        try:
            generator = forward_request(
                f"http://{selected_decode}/v1/{endpoint_type}",
                request_payload,
                extra_headers=nixl_headers_for_request(request),
            )
            storage.add_decode_active_request(selected_decode, request)
            generator = _track_decode_stream(
                generator,
                storage,
                selected_decode,
                request.Request_ID,
            )
        except HTTPException as http_exc:
            storage.remove_decode_active_request(selected_decode, request.Request_ID)
            raise http_exc
        return StreamingResponse(generator)
    except HTTPException:
        raise
    except Exception:
        exc_info = sys.exc_info()
        print("Error occurred in handle_completion_request")
        print(exc_info)
        raise HTTPException(status_code=500, detail=str(exc_info[1]))


async def create_completion(raw_request: Request):
    return await handle_completion_request(raw_request, "completions")


async def create_chat_completion(raw_request: Request):
    return await handle_completion_request(raw_request, "chat/completions")
