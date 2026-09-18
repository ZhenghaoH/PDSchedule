import sys

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from core.schemas.request import BaseRequest
from proxy.forward import forward_request_json
from proxy.forward import forward_request_trace as forward_request
from proxy.sync import RequestBatcher
from store.gateway.manager import GatewayStoreManager
from connectors.manager import get_connector_manager


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


async def _select_prefill_instance(
    storage: GatewayStoreManager,
    request: BaseRequest,
) -> str:
    selected_prefill = getattr(request.Task, "prefill_instance", "")
    if _active_prefill_instance(storage, selected_prefill):
        _log_prefill_instance_status(storage, selected_prefill)
        return selected_prefill

    scheduled, _ = await request_batcher.scheduler_mgr.schedule_batch(
        [request],
        strategy=storage.get_prefill_scheduler_strategy(),
    )
    selected_prefill = (
        scheduled[0].Task.prefill_instance if scheduled else ""
    )
    if not _active_prefill_instance(storage, selected_prefill):
        request_batcher.scheduler_mgr.on_request_finished(request)
        raise HTTPException(status_code=503, detail="No active prefill instance")

    request.Task.prefill_instance = selected_prefill
    _log_prefill_instance_status(storage, selected_prefill)
    return selected_prefill


def _decode_proxy_for_request(request: BaseRequest) -> str:
    host = str(getattr(request.Task, "D_proxy_addr", "") or "")
    port = int(getattr(request.Task, "D_proxy_port", 0) or 0)
    return f"{host}:{port}" if host and port > 0 else ""


async def _release_prefill_when_done(generator, request: BaseRequest):
    try:
        async for chunk in generator:
            yield chunk
    finally:
        request_batcher.scheduler_mgr.on_request_finished(request)


def _bind_prefill_connector(
    storage: GatewayStoreManager, request: BaseRequest, selected_prefill: str
) -> None:
    meta = storage.get_connector_metadata(selected_prefill)
    if not meta:
        raise HTTPException(
            status_code=503,
            detail=f"Missing connector metadata for prefill instance {selected_prefill}",
        )
    descriptor = meta.get("descriptor") or {}
    if (
        meta.get("role") != "prefill"
        or descriptor.get("name") != request.Task.pd_connector
    ):
        raise HTTPException(
            status_code=503,
            detail=f"Invalid prefill connector metadata for {selected_prefill}: {meta}",
        )

    connector_manager = get_connector_manager()
    try:
        connector_manager.validate_instance(
            descriptor,
            storage.get_connector(),
            "prefill",
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Invalid prefill connector metadata for {selected_prefill}: {exc}",
        ) from exc

    request.Task.connector_context["prefill_endpoint"] = meta


async def _handle_pd_request(request: BaseRequest, endpoint_type: str):
    storage = GatewayStoreManager()
    if storage.USE_SYN:
        batch_result = await request_batcher.add_connector_prefill_request(request)
        if batch_result.get("error"):
            raise HTTPException(
                status_code=batch_result.get("status_code", 500),
                detail=batch_result["error"],
            )
        if not batch_result.get("success", True):
            raise HTTPException(status_code=500, detail="Connector prefill batch failed")

        kv_transfer_params = batch_result.get("kv_transfer_params")
        connector_manager = get_connector_manager()
        if connector_manager.requires_transfer_context(request.Task.pd_connector):
            if not isinstance(kv_transfer_params, dict) or not kv_transfer_params:
                raise HTTPException(
                    status_code=502,
                    detail="Connector prefill batch result does not contain transfer context",
                )
            connector_manager.bind_prefill_result(
                request,
                {"kv_transfer_params": kv_transfer_params},
            )
        else:
            request.Task.connector_context["kv_transfer_params"] = kv_transfer_params
        request.Task.prefill_instance = batch_result.get(
            "instance", request.Task.prefill_instance
        )
    else:
        selected_prefill = await _select_prefill_instance(storage, request)
        try:
            _bind_prefill_connector(storage, request, selected_prefill)

            connector_manager = get_connector_manager()
            prefill_payload = connector_manager.build_prefill_payload(request)
            prefill_response = await forward_request_json(
                f"http://{selected_prefill}/v1/{endpoint_type}",
                prefill_payload,
                extra_headers=connector_manager.headers(request),
            )
            connector_manager.bind_prefill_result(request, prefill_response)
        finally:
            request_batcher.scheduler_mgr.on_request_finished(request)

    decode_proxy = _decode_proxy_for_request(request)
    if not decode_proxy:
        raise HTTPException(status_code=503, detail="No selected decode gateway")

    decode_generator = forward_request(
        f"http://{decode_proxy}/v1/{endpoint_type}",
        BaseRequest.to_decode_payload(request),
        arrival_time=request.Service.arrival_time,
        slo_sec=request.Service.SLO_TTFT / 1000.0,
        request_id=request.Request_ID,
        stats_callback=request_batcher.monitor_engine.handle_ttft_stats,
        extra_headers=get_connector_manager().headers(request),
    )
    return StreamingResponse(decode_generator)


async def _handle_non_pd_request(request: BaseRequest, endpoint_type: str):
    storage = GatewayStoreManager()
    if storage.USE_SYN:
        batch_result = await request_batcher.add_request(request)
        if batch_result.get("error"):
            raise HTTPException(
                status_code=batch_result.get("status_code", 500),
                detail=batch_result["error"],
            )
        if not batch_result.get("success", True):
            raise HTTPException(status_code=500, detail="Batch processing failed")
        return StreamingResponse(batch_result["generator"])

    selected_prefill = await _select_prefill_instance(storage, request)
    request_payload = BaseRequest.to_vllm_payload(request)
    try:
        forwarded = forward_request(
            f"http://{selected_prefill}/v1/{endpoint_type}",
            request_payload,
        )
    except Exception:
        request_batcher.scheduler_mgr.on_request_finished(request)
        raise
    generator = _release_prefill_when_done(
        forwarded,
        request,
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
    return await handle_completion_request(raw_request, request, endpoint_type)


async def create_chat_completion(raw_request: Request):
    endpoint_type = "chat/completions"
    request_json = await raw_request.json()
    request = BaseRequest.recover_request(request_json, endpoint_type)
    return await handle_completion_request(raw_request, request, endpoint_type)
