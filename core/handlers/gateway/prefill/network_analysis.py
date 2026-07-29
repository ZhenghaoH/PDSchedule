import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from core.schemas.request import BaseRequest, Prompt, Service, Task
from proxy.forward import forward_request_trace as forward_request
from scheduler.gateway.prefill.async_base import AsyncBaseScheduler
from store.gateway.manager import GatewayStoreManager


def _split_host_port(addr: str) -> Tuple[str, int]:
    host, port = addr.rsplit(":", 1)
    return host, int(port)


def _build_analysis_request(payload: Dict[str, Any], storage: GatewayStoreManager) -> BaseRequest:
    model = str(payload.get("model") or "network-transfer-analysis-model")
    prompt_text = str(payload.get("prompt") or "")
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Missing required field: prompt")

    request_id = str(payload.get("request_id") or f"network-analysis-{int(time.time() * 1000)}")
    token_length = Prompt.extract_prompt_info(model, prompt_text)

    prompt = Prompt(
        model=model,
        user_prompt=prompt_text,
        token_length=token_length,
        bs=1,
        max_tokens=1,
        stream=True,
        temperature=0.0,
        top_p=1.0,
    )
    service = Service(
        Enable_PD_Disaggregation=True,
        Enable_Affinity=False,
        Enable_compress=False,
        Compress_factor=1.0,
        Enable_security=False,
        SLO_TTFT=10_000,
        SLO_E2E=60_000,
        SLO_TPOT=1_000,
        arrival_time=time.time(),
        Endpoint_type="chat/completions",
    )

    decode_targets = storage.decode_objs
    if not decode_targets:
        raise HTTPException(status_code=503, detail="No decode gateway configured.")
    decode_host, decode_port = _split_host_port(decode_targets[0])

    task = Task(
        User_addr="network-transfer-analyzer",
        P_proxy_addr=storage.host,
        P_proxy_port=storage.port,
        D_proxy_addr=decode_host,
        D_proxy_port=decode_port,
        prefill_instance="",
        decode_instance="",
        batch_order=0,
        User_url_path="/analysis/network-transfer",
    )

    return BaseRequest(
        Request_ID=request_id,
        Request_type="analysis",
        Prompt=prompt,
        Service=service,
        Task=task,
    )


async def _consume_stream(generator) -> Dict[str, int]:
    chunk_count = 0
    byte_count = 0
    async for chunk in generator:
        if chunk:
            chunk_count += 1
            byte_count += len(chunk)
    return {"chunks": chunk_count, "bytes": byte_count}


async def analyze_network_transfer(raw_request: Request):
    gateway_receive_time_s = time.time()
    payload = await raw_request.json()
    storage = GatewayStoreManager()
    request = _build_analysis_request(payload, storage)

    timing_events: List[float] = []

    def record_first_chunk(elapsed_s: float, _is_violated: bool, _request_id: Any) -> None:
        timing_events.append(float(elapsed_s))

    scheduler = AsyncBaseScheduler(storage)
    gateway_prefill_schedule_time_s = time.time()
    prefill_instance = scheduler.schedule(request, "prefill")
    if not prefill_instance:
        raise HTTPException(status_code=503, detail="No active prefill instance available.")

    request.Task.prefill_instance = prefill_instance
    arrival_time = time.time()
    request.Service.arrival_time = arrival_time

    prefill_payload = BaseRequest.to_vllm_payload(request)
    gateway_prefill_request_time_s = time.time()
    prefill_generator = forward_request(
        f"http://{prefill_instance}/v1/{request.Service.Endpoint_type}",
        prefill_payload,
        arrival_time=arrival_time,
        request_id=request.Request_ID,
        stats_callback=record_first_chunk,
    )
    prefill_stats = await _consume_stream(prefill_generator)
    gateway_prefill_done_time_s = time.time()
    if not timing_events:
        raise HTTPException(status_code=502, detail="Prefill response produced no observable chunk.")

    gateway_decode_schedule_time_s = time.time()
    decode_target = scheduler.schedule(request, "decode")
    request.Task.decode_instance = decode_target
    decode_payload = BaseRequest.to_decode_payload(request)
    gateway_decode_request_time_s = time.time()
    decode_generator = forward_request(
        f"http://{decode_target}/v1/{request.Service.Endpoint_type}",
        decode_payload,
        arrival_time=arrival_time,
        request_id=request.Request_ID,
        stats_callback=record_first_chunk,
    )
    decode_stats = await _consume_stream(decode_generator)
    gateway_decode_done_time_s = time.time()
    if len(timing_events) < 2:
        raise HTTPException(status_code=502, detail="Decode response produced no observable chunk.")

    prefill_first_chunk_s = timing_events[0]
    decode_first_chunk_s = timing_events[1]
    prefill_first_chunk_abs_time_s = arrival_time + prefill_first_chunk_s
    decode_first_chunk_abs_time_s = arrival_time + decode_first_chunk_s
    observed_transfer_ms = max(0.0, (decode_first_chunk_s - prefill_first_chunk_s) * 1000.0)

    return {
        "success": True,
        "request_id": request.Request_ID,
        "model": request.Prompt.model,
        "prompt_tokens": request.Prompt.token_length,
        "max_tokens": request.Prompt.max_tokens,
        "prefill_instance": prefill_instance,
        "decode_target": decode_target,
        "gateway_receive_time_s": gateway_receive_time_s,
        "gateway_arrival_time_s": arrival_time,
        "gateway_prefill_schedule_time_s": gateway_prefill_schedule_time_s,
        "gateway_prefill_request_time_s": gateway_prefill_request_time_s,
        "gateway_prefill_done_time_s": gateway_prefill_done_time_s,
        "gateway_decode_schedule_time_s": gateway_decode_schedule_time_s,
        "gateway_decode_request_time_s": gateway_decode_request_time_s,
        "gateway_decode_done_time_s": gateway_decode_done_time_s,
        "prefill_first_chunk_time_s": prefill_first_chunk_s,
        "decode_first_chunk_time_s": decode_first_chunk_s,
        "prefill_first_chunk_abs_time_s": prefill_first_chunk_abs_time_s,
        "decode_first_chunk_abs_time_s": decode_first_chunk_abs_time_s,
        "observed_transfer_time_ms": observed_transfer_ms,
        "prefill_stream": prefill_stats,
        "decode_stream": decode_stats,
    }
