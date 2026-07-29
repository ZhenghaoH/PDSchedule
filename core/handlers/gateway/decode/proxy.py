import asyncio
import sys
import time

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from core.schemas.request import BaseRequest
from proxy.batcher import RequestBatcher
from proxy.forward import forward_request
from scheduler.gateway.decode.manager import SchedulerManager
from store.gateway.manager import GatewayStoreManager
from utils.nixl_request import build_nixl_decode_payload, nixl_headers_for_request
from utils.gateway_observability import (
    build_end_to_end_timing,
    get_gateway_observer,
)


request_batcher = RequestBatcher()
decode_observer = get_gateway_observer("decode")
LOG_SEPARATOR = "+" * 80
LOG_ITEM_SEPARATOR = "-" * 80


async def _track_decode_stream(
    generator,
    storage: GatewayStoreManager,
    instance: str,
    request: BaseRequest,
    decode_arrival_s: float,
):
    request_id = request.Request_ID
    decode_forward_s = time.time()
    decode_first_chunk_s = None
    status = "completed"
    error = None
    decode_observer.record_timing(
        "decode_dispatched",
        request_id=str(request_id),
        timestamp_s=decode_forward_s,
        instance=instance,
    )
    try:
        async for chunk in generator:
            if decode_first_chunk_s is None and chunk:
                decode_first_chunk_s = time.time()
                decode_observer.record_timing(
                    "decode_first_chunk",
                    request_id=str(request_id),
                    timestamp_s=decode_first_chunk_s,
                    instance=instance,
                    first_response_wait_ms=max(
                        0.0, (decode_first_chunk_s - decode_forward_s) * 1000.0
                    ),
                )
            yield chunk
    except GeneratorExit:
        status = "cancelled"
        error = "response_consumer_closed"
        raise
    except asyncio.CancelledError:
        status = "cancelled"
        error = "response_task_cancelled"
        raise
    except Exception as exc:
        status = "failed"
        error = str(exc)
        raise
    finally:
        decode_end_s = time.time()
        metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
        timing = dict(metadata.get("gateway_timing", {}) or {})
        summary = build_end_to_end_timing(
            timing,
            decode_arrival_s=decode_arrival_s,
            decode_forward_s=decode_forward_s,
            decode_first_chunk_s=decode_first_chunk_s,
            decode_end_s=decode_end_s,
        )
        decode_observer.record_timing(
            "request_finished" if status == "completed" else f"request_{status}",
            request_id=str(request_id),
            timestamp_s=decode_end_s,
            status=status,
            error=error,
            prefill_instance=getattr(request.Task, "prefill_instance", ""),
            decode_instance=instance,
            **summary,
        )
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
        instances = storage.instance_table
        candidates = [
            {
                "instance": instance_ip,
                "running_reqs": int(
                    getattr(instances.get(instance_ip), "running_reqs", 0) or 0
                ),
                "waiting_reqs": int(
                    getattr(instances.get(instance_ip), "waiting_reqs", 0) or 0
                ),
                "heartbeat_request_load": int(
                    getattr(instances.get(instance_ip), "running_reqs", 0) or 0
                )
                + int(
                    getattr(instances.get(instance_ip), "waiting_reqs", 0) or 0
                ),
                "generate_throughput": float(
                    getattr(instances.get(instance_ip), "generate_throughput", 0.0)
                    or 0.0
                ),
            }
            for instance_ip in storage.active_decode_objs
        ]
        decision = {
            "strategy": storage.get_decode_scheduler_strategy(),
            "selected_instance": selected_decode,
            "selection_source": "preselected_request",
            "reason": "active_decode_instance_already_present_in_request",
            "candidates": candidates,
        }
        metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
        metadata["gateway_decode_selection"] = decision
        request.Task.scheduler_metadata = metadata
        decode_observer.record_schedule(
            request_id=str(request.Request_ID),
            **decision,
        )
        print(
            "[Decode Schedule] "
            f"source={decision['selection_source']} "
            f"selected={selected_decode} reason={decision['reason']}"
        )
        _log_decode_instance_status(storage, selected_decode)
        return selected_decode

    strategy = storage.get_decode_scheduler_strategy()
    scheduler = SchedulerManager(strategy=strategy, storage=storage)
    selected_decode = await scheduler.schedule(request)
    if not _active_decode_instance(storage, selected_decode):
        raise HTTPException(status_code=503, detail="No active decode instance")

    request.Task.decode_instance = selected_decode
    decision = dict(scheduler.last_decision or {})
    decision.setdefault("strategy", strategy)
    decision.setdefault("selected_instance", selected_decode)
    decision.setdefault("selection_source", "decode_gateway_scheduler")
    decision.setdefault("reason", "strategy_selected_instance")
    metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
    metadata["gateway_decode_selection"] = decision
    request.Task.scheduler_metadata = metadata
    decode_observer.record_schedule(
        request_id=str(request.Request_ID),
        **decision,
    )
    print(
        "[Decode Schedule] "
        f"strategy={strategy} selected={selected_decode} "
        f"reason={decision.get('reason')}"
    )
    _log_decode_instance_status(storage, selected_decode)
    return selected_decode


def _bind_and_validate_nixl_metadata(
    storage: GatewayStoreManager, request: BaseRequest, selected_decode: str
) -> None:
    prefill_instance = getattr(request.Task, "prefill_instance", "")
    prefill_side_channel_addr = getattr(request.Task, "prefill_side_channel_addr", "")
    prefill_tp_size = int(getattr(request.Task, "prefill_tp_size", 0) or 0)
    decode_meta = storage.get_nixl_metadata(selected_decode)

    if not prefill_instance or not prefill_side_channel_addr or prefill_tp_size <= 0:
        raise HTTPException(status_code=503, detail=f"Missing NIXL metadata for prefill instance {prefill_instance}")
    if not decode_meta:
        raise HTTPException(status_code=503, detail=f"Missing NIXL metadata for decode instance {selected_decode}")
    if decode_meta.get("connector") != "NixlConnector" or decode_meta.get("role") != "decode":
        raise HTTPException(status_code=503, detail=f"Invalid decode NIXL metadata: {decode_meta}")

    request.Task.decode_tp_size = int(decode_meta.get("tp_size") or 0)
    request.Task.decode_side_channel_addr = storage.get_side_channel_addr(selected_decode)


async def handle_completion_request(raw_request: Request, endpoint_type: str):
    storage = GatewayStoreManager()
    selected_decode = ""
    request = None
    decode_arrival_s = None
    try:
        request_json = await raw_request.json()
        request = BaseRequest.recover_request(request_json, endpoint_type)
        decode_arrival_s = time.time()
        decode_observer.record_timing(
            "decode_arrived",
            request_id=str(request.Request_ID),
            timestamp_s=decode_arrival_s,
            prefill_instance=getattr(request.Task, "prefill_instance", ""),
        )

        if storage.USE_SYN:
            batch_result = await request_batcher.add_request(request)
            if batch_result.get("error"):
                raise HTTPException(status_code=500, detail=batch_result["error"])
            if not batch_result.get("success", True):
                raise HTTPException(status_code=500, detail="Batch processing failed")
            return StreamingResponse(batch_result["generator"], media_type="application/json")

        selected_decode = await _select_decode_instance(storage, request)
        if request.Service.Enable_PD_Disaggregation:
            _bind_and_validate_nixl_metadata(storage, request, selected_decode)
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
                request,
                decode_arrival_s,
            )
        except HTTPException as http_exc:
            storage.remove_decode_active_request(selected_decode, request.Request_ID)
            raise http_exc
        return StreamingResponse(generator)
    except HTTPException:
        if request is not None:
            decode_observer.record_timing(
                "request_failed",
                request_id=str(request.Request_ID),
                timestamp_s=time.time(),
                status="failed_before_decode_stream",
                decode_instance=selected_decode,
            )
        raise
    except Exception:
        exc_info = sys.exc_info()
        if request is not None:
            decode_observer.record_timing(
                "request_failed",
                request_id=str(request.Request_ID),
                timestamp_s=time.time(),
                status="failed_before_decode_stream",
                error=str(exc_info[1]),
                decode_instance=selected_decode,
            )
        print("Error occurred in handle_completion_request")
        print(exc_info)
        raise HTTPException(status_code=500, detail=str(exc_info[1]))


async def create_completion(raw_request: Request):
    return await handle_completion_request(raw_request, "completions")


async def create_chat_completion(raw_request: Request):
    return await handle_completion_request(raw_request, "chat/completions")
