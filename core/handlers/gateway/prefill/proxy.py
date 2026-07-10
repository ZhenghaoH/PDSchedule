import sys

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from config.ADDRESS_CONFIG import (
    get_nixl_meta_for_instance,
    get_side_channel_addr_for_instance,
)
from core.handlers.gateway.prefill.kdn_proxy import (
    kdn_proxy_chat_completions,
    kdn_proxy_completions,
)
from core.schemas.request import BaseRequest
from proxy.forward import forward_request_json
from proxy.forward import forward_request_trace as forward_request
from proxy.sync import RequestBatcher
from scheduler.gateway.prefill.async_base import AsyncBaseScheduler
from store.gateway.manager import GatewayStoreManager
from utils.nixl_request import (
    build_nixl_prefill_payload,
    extract_kv_transfer_params,
    nixl_headers_for_request,
)


request_batcher = RequestBatcher()
LOG_SEPARATOR = "+" * 80
LOG_ITEM_SEPARATOR = "-" * 80


def _log_prefill_instance_status(storage: GatewayStoreManager, selected_instance: str) -> None:
    candidates = storage.active_prefill_objs
    instances = storage.instance_table
    if not candidates:
        print(
            "\n" + "\n".join([
                LOG_SEPARATOR,
                "[Prefill Schedule] Candidate instances",
                LOG_SEPARATOR,
                "No active instance",
                LOG_SEPARATOR,
                "[Prefill Schedule] Selected instance: ",
                LOG_SEPARATOR,
            ]) + "\n"
        )
        return

    lines = [
        LOG_SEPARATOR,
        "[Prefill Schedule] Candidate instances",
        LOG_SEPARATOR,
    ]
    for index, instance_ip in enumerate(candidates, start=1):
        instance = instances.get(instance_ip)
        prompt_throughput = instance.prompt_throughput if instance else 0.0
        gpu_util_percent = instance.gpu_util_percent if instance else 0.0
        gpu_memory_util_percent = instance.gpu_memory_util_percent if instance else 0.0
        lines.extend([
            f"Instance {index}: {instance_ip}",
            f"GPU util: {gpu_util_percent:.2f}%",
            f"GPU memory util: {gpu_memory_util_percent:.2f}%",
            f"Prompt throughput: {prompt_throughput:.2f} tokens/s",
        ])
        if index != len(candidates):
            lines.append(LOG_ITEM_SEPARATOR)
    lines.extend([
        LOG_SEPARATOR,
        f"[Prefill Schedule] Selected instance: {selected_instance}",
        LOG_SEPARATOR,
    ])
    print("\n" + "\n".join(lines) + "\n")


def _active_prefill_instance(storage: GatewayStoreManager, instance_addr: str) -> bool:
    return bool(instance_addr and instance_addr in storage.active_prefill_objs)


def _select_prefill_instance(storage: GatewayStoreManager, request: BaseRequest) -> str:
    selected_prefill = getattr(request.Task, "prefill_instance", "")
    if _active_prefill_instance(storage, selected_prefill):
        _log_prefill_instance_status(storage, selected_prefill)
        return selected_prefill

    scheduler = AsyncBaseScheduler(storage)
    selected_prefill = scheduler.schedule(request, "prefill")
    if not _active_prefill_instance(storage, selected_prefill):
        raise HTTPException(status_code=503, detail="No active prefill instance")

    request.Task.prefill_instance = selected_prefill
    _log_prefill_instance_status(storage, selected_prefill)
    return selected_prefill


def _bind_prefill_nixl_metadata(request: BaseRequest, selected_prefill: str) -> None:
    meta = get_nixl_meta_for_instance(selected_prefill)
    if not meta:
        raise HTTPException(
            status_code=503,
            detail=f"Missing NIXL metadata for prefill instance {selected_prefill}",
        )
    if meta.get("connector") != "NixlConnector" or meta.get("role") != "prefill":
        raise HTTPException(
            status_code=503,
            detail=f"Invalid prefill NIXL metadata for {selected_prefill}: {meta}",
        )

    request.Task.prefill_tp_size = int(meta.get("tp_size") or 0)
    request.Task.prefill_side_channel_addr = get_side_channel_addr_for_instance(selected_prefill)


async def _handle_pd_request(request: BaseRequest, endpoint_type: str):
    storage = GatewayStoreManager()
    if storage.USE_SYN:
        batch_result = await request_batcher.add_nixl_prefill_request(request)
        if batch_result.get("error"):
            raise HTTPException(status_code=500, detail=batch_result["error"])
        if not batch_result.get("success", True):
            raise HTTPException(status_code=500, detail="NIXL prefill batch failed")

        kv_transfer_params = batch_result.get("kv_transfer_params")
        if not isinstance(kv_transfer_params, dict) or not kv_transfer_params:
            raise HTTPException(
                status_code=502,
                detail="NIXL prefill batch result does not contain kv_transfer_params",
            )
        request.Task.prefill_instance = batch_result.get(
            "instance", request.Task.prefill_instance
        )
        request.Task.nixl_kv_transfer_params = kv_transfer_params
    else:
        selected_prefill = _select_prefill_instance(storage, request)
        _bind_prefill_nixl_metadata(request, selected_prefill)

        prefill_payload = build_nixl_prefill_payload(request)
        prefill_response = await forward_request_json(
            f"http://{selected_prefill}/v1/{endpoint_type}",
            prefill_payload,
            extra_headers=nixl_headers_for_request(request),
        )
        request.Task.nixl_kv_transfer_params = extract_kv_transfer_params(prefill_response)

    scheduler = AsyncBaseScheduler(storage)
    decode_proxy = scheduler.schedule(request, "decode")
    if not decode_proxy:
        raise HTTPException(status_code=503, detail="No selected decode gateway")

    decode_generator = forward_request(
        f"http://{decode_proxy}/v1/{endpoint_type}",
        BaseRequest.to_decode_payload(request),
        arrival_time=request.Service.arrival_time,
        slo_sec=request.Service.SLO_TTFT / 1000.0,
        request_id=request.Request_ID,
        stats_callback=request_batcher.monitor_engine.handle_ttft_stats,
        extra_headers=nixl_headers_for_request(request),
    )
    return StreamingResponse(decode_generator)


async def _handle_non_pd_request(request: BaseRequest, endpoint_type: str):
    storage = GatewayStoreManager()
    if storage.USE_SYN:
        batch_result = await request_batcher.add_request(request)
        if batch_result.get("error"):
            raise HTTPException(status_code=500, detail=batch_result["error"])
        if not batch_result.get("success", True):
            raise HTTPException(status_code=500, detail="Batch processing failed")
        return StreamingResponse(batch_result["generator"])

    selected_prefill = _select_prefill_instance(storage, request)
    request_payload = BaseRequest.to_vllm_payload(request)
    generator = forward_request(
        f"http://{selected_prefill}/v1/{endpoint_type}",
        request_payload,
    )
    return StreamingResponse(generator)


async def handle_completion_request(raw_request: Request, request: BaseRequest, endpoint_type: str):
    try:
        if request.Service.Enable_PD_Disaggregation:
            return await _handle_pd_request(request, endpoint_type)
        return await _handle_non_pd_request(request, endpoint_type)
    except HTTPException:
        raise
    except Exception:
        exc_info = sys.exc_info()
        print("Error occurred in handle_completion_request")
        print(exc_info)
        raise HTTPException(status_code=500, detail=str(exc_info[1]))


async def create_completion(raw_request: Request):
    endpoint_type = "completions"
    request_json = await raw_request.json()
    request = BaseRequest.recover_request(request_json, endpoint_type)

    enable_rag = bool(getattr(request.Service, "Enable_know_injection", False))
    knowledge_ids = getattr(request.Service, "Knowledge_List", []) or []
    if enable_rag and knowledge_ids:
        return await kdn_proxy_completions(raw_request, request)
    return await handle_completion_request(raw_request, request, endpoint_type)


async def create_chat_completion(raw_request: Request):
    endpoint_type = "chat/completions"
    request_json = await raw_request.json()
    request = BaseRequest.recover_request(request_json, endpoint_type)

    enable_rag = bool(getattr(request.Service, "Enable_know_injection", False))
    knowledge_ids = getattr(request.Service, "Knowledge_List", []) or []
    if enable_rag and knowledge_ids:
        return await kdn_proxy_chat_completions(raw_request, request)
    return await handle_completion_request(raw_request, request, endpoint_type)
