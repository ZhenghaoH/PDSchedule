from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import StreamingResponse, JSONResponse

from core.schemas.request import BaseRequest as SchedulerRequest
from proxy.forward import forward_request
from connectors.manager import get_connector_manager

from utils import timing

from store.controller import PoolManager
global_storage = PoolManager()


LOG_SEPARATOR = "+" * 80
LOG_ITEM_SEPARATOR = "-" * 80
GLOBAL_SCHEDULE_LOG_PATH = Path(__file__).resolve().parents[3] / "global_schedule.log"


def _format_ms(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f} ms"


def _format_bool(value: Optional[bool]) -> str:
    if value is None:
        return "N/A"
    return "YES" if value else "NO"


def _append_global_schedule_log(text: str) -> None:
    print(text)
    try:
        with GLOBAL_SCHEDULE_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(text)
            if not text.endswith("\n"):
                log_file.write("\n")
    except Exception as exc:
        print(f"[Scheduler-SLO] 写入SLO追踪日志失败: {exc}")


def _observe_stream_chunk(
    chunk: bytes,
    chunk_times: List[float],
) -> None:
    if chunk:
        chunk_times.append(time.perf_counter())


def _extract_completion_tokens(content: bytes) -> Optional[int]:
    try:
        body = json.loads(content.decode("utf-8", errors="ignore"))
    except Exception:
        return None
    if not isinstance(body, dict):
        return None

    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        completion_tokens = int(usage.get("completion_tokens"))
    except (TypeError, ValueError):
        return None
    if completion_tokens <= 0:
        return None
    return completion_tokens


def _build_slo_trace_log(
    req_obj: SchedulerRequest,
    *,
    mode: str,
    basis: str,
    ttft_ms: Optional[float],
    tpot_ms: Optional[float],
    e2e_ms: Optional[float],
    generated_units: int,
    generated_units_label: str = "生成内容片段数",
    completion_tokens: Optional[int] = None,
) -> str:
    slo_ttft = getattr(req_obj.Service, "SLO_TTFT", None)
    slo_tpot = getattr(req_obj.Service, "SLO_TPOT", None)
    slo_e2e = getattr(req_obj.Service, "SLO_E2E", None)

    ttft_violation = ttft_ms is not None and slo_ttft is not None and ttft_ms > float(slo_ttft)
    tpot_violation = tpot_ms is not None and slo_tpot is not None and tpot_ms > float(slo_tpot)
    e2e_violation = e2e_ms is not None and slo_e2e is not None and e2e_ms > float(slo_e2e)

    lines = [
        "",
        LOG_SEPARATOR,
        "[Controller-SLO] 请求TTFT/TPOT追踪结果",
        LOG_SEPARATOR,
        f"日志时间: {datetime.now().isoformat(timespec='seconds')}",
        f"Request_ID: {req_obj.Request_ID}",
        f"Endpoint: {getattr(req_obj.Service, 'Endpoint_type', 'N/A')}",
        f"响应模式: {mode}",
        f"Prefill代理: {req_obj.Task.P_proxy_addr}:{req_obj.Task.P_proxy_port}",
        f"Decode代理: {req_obj.Task.D_proxy_addr}:{req_obj.Task.D_proxy_port}",
        LOG_ITEM_SEPARATOR,
        f"SLO_TTFT: {slo_ttft} ms",
        f"SLO_TPOT: {slo_tpot} ms",
        f"SLO_E2E: {slo_e2e} ms",
        LOG_ITEM_SEPARATOR,
        f"实测TTFT: {_format_ms(ttft_ms)}",
        f"实测TPOT: {_format_ms(tpot_ms)}",
        f"实测E2E: {_format_ms(e2e_ms)}",
        f"TPOT计算依据: {basis}",
        f"{generated_units_label}: {generated_units}",
        f"Completion tokens: {completion_tokens if completion_tokens is not None else 'N/A'}",
        LOG_ITEM_SEPARATOR,
        f"TTFT违约: {_format_bool(ttft_violation if ttft_ms is not None else None)}",
        f"TPOT违约: {_format_bool(tpot_violation if tpot_ms is not None else None)}",
        f"E2E违约: {_format_bool(e2e_violation if e2e_ms is not None else None)}",
        LOG_SEPARATOR,
        "",
    ]
    return "\n".join(lines)


def _log_stream_slo_trace(req_obj: SchedulerRequest, start_time: float, chunk_times: List[float]) -> None:
    end_time = time.perf_counter()
    ttft_ms: Optional[float] = None
    tpot_ms: Optional[float] = None
    if chunk_times:
        ttft_ms = (chunk_times[0] - start_time) * 1000.0
    if len(chunk_times) > 1:
        tpot_ms = ((chunk_times[-1] - chunk_times[0]) / (len(chunk_times) - 1)) * 1000.0

    log_text = _build_slo_trace_log(
        req_obj,
        mode="stream",
        basis="non_empty_chunks",
        ttft_ms=ttft_ms,
        tpot_ms=tpot_ms,
        e2e_ms=(end_time - start_time) * 1000.0,
        generated_units=len(chunk_times),
        generated_units_label="非空响应chunk数",
    )
    _append_global_schedule_log(log_text)


def _log_non_stream_slo_trace(req_obj: SchedulerRequest, start_time: float, body: bytes) -> None:
    end_time = time.perf_counter()
    e2e_ms = (end_time - start_time) * 1000.0
    completion_tokens = _extract_completion_tokens(body)
    tpot_ms = e2e_ms / completion_tokens if completion_tokens else None

    log_text = _build_slo_trace_log(
        req_obj,
        mode="non-stream",
        basis="usage.completion_tokens" if completion_tokens else "N/A",
        ttft_ms=e2e_ms,
        tpot_ms=tpot_ms,
        e2e_ms=e2e_ms,
        generated_units=completion_tokens or 0,
        completion_tokens=completion_tokens,
    )
    _append_global_schedule_log(log_text)


# ======================= 请求 ID 分配器 =======================

class RequestIdAllocator:
    """
    简单的 16bit 请求 ID 分配器：
      - 有效范围：1 ~ max_id（默认 65535）
      - 超过后从 1 重新开始
    """
    def __init__(self, max_id: int = 65535) -> None:
        self._max_id = max_id
        self._current = 0

    def next_id(self) -> int:
        self._current += 1
        if self._current > self._max_id:
            self._current = 1
        return self._current


id_alloc = RequestIdAllocator()


async def _prepare_pd_request(req_obj: SchedulerRequest) -> None:
    if not getattr(req_obj.Service, "Enable_PD_Disaggregation", False):
        return

    if not req_obj.Task.P_proxy_addr or not req_obj.Task.P_proxy_port:
        raise RuntimeError("Missing selected Prefill gateway")
    if not req_obj.Task.D_proxy_addr or not req_obj.Task.D_proxy_port:
        raise RuntimeError("Missing selected Decode gateway")

    connector = req_obj.Task.pd_connector
    adapter = get_connector_manager().registry.get(connector)
    session_id = f"{connector}-{req_obj.Request_ID}"
    descriptor = req_obj.Task.connector_context.get("descriptor")
    session = get_connector_manager().create_session(
        prefill={
            "address": f"{req_obj.Task.P_proxy_addr}:{req_obj.Task.P_proxy_port}",
            "connector": descriptor,
        },
        decode={
            "address": f"{req_obj.Task.D_proxy_addr}:{req_obj.Task.D_proxy_port}",
            "connector": descriptor,
        },
        descriptor=descriptor,
        session_id=session_id,
    )
    req_obj.Task.connector_session_id = session_id
    req_obj.Task.connector_context.update(session.context)
    req_obj.Task.decode_instance = ""
    req_obj.Task.decode_selection_stage = "after_prefill"
    print(
        f"[Controller-Connector] Req {req_obj.Request_ID} prepared "
        f"P-Gateway={req_obj.Task.P_proxy_addr}:{req_obj.Task.P_proxy_port} "
        f"D-Gateway={req_obj.Task.D_proxy_addr}:{req_obj.Task.D_proxy_port} "
        f"connector={adapter.name} session_id={session_id}"
    )


# ======================= 公共内部处理函数 =======================
@timing
def _handle_client(
    url_path: str,
    payload: Dict[str, Any],
    client_ip: str,
    scheduler_instance: Any,
    app
) -> SchedulerRequest:
    """
    根据 HTTP 请求信息
    构造内部 Request 对象，分配request_id
      - url_path: 例如 "/v1/chat/completions"
      - payload: 解析后的 JSON 字典
      - client_ip: 客户端 IP 字符串
    """
    # 分配 request_id（1~65535 循环）
    request_id = id_alloc.next_id()

    # # 打印调试信息
    # print("+" * 80)
    # print(f"[Scheduler] 收到 HTTP 请求: path={url_path}, client_ip={client_ip},\n"
    #       f"分配 request_id={request_id},\n"
    #       f"payload={payload}")
    # print("+" * 80)

    # FIXME: 演示用，打印请求解析
    print("\n\n\n")
    print("+" * 80)
    print(f"[Scheduler] 收到 HTTP 请求 | 分配 request_id={request_id} | 进行请求解析 ")
    print("+" * 80)
    print(f"请求路由: {url_path}")
    print(f"用户地址: {client_ip}")
    for k, v in payload.items():
        print(f"请求报文 {k}: {v}")
    print("+" * 80)
    print("\n\n\n")

    # 直接复用你改好的 build_request
    req_obj = SchedulerRequest.build_request(
        url_path=url_path,
        payload=payload,
        user_addr=client_ip,
        request_id=request_id,
        scheduler_instance=scheduler_instance,
        affinity_store=getattr(app.state, "affinity_store", None),  # type: ignore
        affinity_lock=getattr(app.state, "affinity_lock", None),  # type: ignore
    )

    # FIXME: 演示用，添加请求
    global_storage.add_req(req_obj)

    print(f"[Scheduler] 构建内部 Request 成功: Request_ID={req_obj.Request_ID}, "
          f"Endpoint_type={getattr(req_obj.Service, 'Endpoint_type', None)}")

    return req_obj


# ======================= 调度器方法路由 =======================
async def create_chat_completions(request: FastAPIRequest):
    """
    对应：
        curl http://HOST:PORT/v1/chat/completions -H "Content-Type: application/json" -d '{...}'
        payload 结构参考 OpenAI chat 接口：
          {
            "model": "...",
            "messages": [ ... ],
            "max_tokens": ...,
            "temperature": ...,
            "top_p": ...,
            "stream": true/false,
            ...
          }
    """
    # 解析用户原始请求体
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "detail": str(e)},
        )

    # 取客户端 IP 和路径（用于构建内部 Request）
    client_ip = request.client.host if request.client else "unknown"
    url_path = request.url.path

    # 把用户请求头转成 dict[str, str]
    raw_headers = {k.lower(): v for k, v in request.headers.items()}

    scheduler_instance = request.app.state.global_scheduler

    # 构建内部 Request（包含 Prompt/Service/Task 等）
    req_obj = _handle_client(url_path, payload, client_ip, scheduler_instance, request.app)
    try:
        await _prepare_pd_request(req_obj)
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"error": "pd_instance_selection_failed", "detail": str(exc)},
        )

    # 根据调度结果选择下游 URL
    host = req_obj.Task.P_proxy_addr
    port = req_obj.Task.P_proxy_port
    endpoint = getattr(req_obj.Service, "Endpoint_type", "chat/completions")
    downstream_url = f"http://{host}:{port}/v1/{endpoint}"

    # 转化payload
    data_for_downstream = req_obj.to_payload()

    # 处理需要透传给下游的 headers（可选择性过滤）
    extra_headers = {}

    # 把用户的 Authorization 原样传给下游（如果希望由 Proxy 做鉴权）
    if "authorization" in raw_headers:
        extra_headers["authorization"] = raw_headers["authorization"]

    # 带一个 Scheduler分配的Request ID，便于链路追踪
    extra_headers["scheduler-request-id"] = str(req_obj.Request_ID)
    extra_headers.update(get_connector_manager().headers(req_obj))

    # 定义一个 async 生成器，从下游流式读取，再回给上游
    async def iter_upstream():
        start_time = time.perf_counter()
        chunk_times: List[float] = []
        try:
            async for chunk in forward_request(
                url=downstream_url,
                data=data_for_downstream,
                use_chunked=True,
                extra_headers=extra_headers,
            ):
                _observe_stream_chunk(chunk, chunk_times)
                yield chunk
            _log_stream_slo_trace(req_obj, start_time, chunk_times)
        finally:
            get_connector_manager().close_session(
                req_obj.Task.connector_session_id
            )

    # 用 StreamingResponse 包一层，实现用户侧的流式响应
    return StreamingResponse(iter_upstream(), media_type="application/json")


async def create_completions(request: FastAPIRequest):
    """
    对应：
      curl http://HOST:PORT/v1/completions -H "Content-Type: application/json" -d '{...}'
    payload 结构类似 OpenAI completions 接口：
      {
        "model": "...",
        "prompt": "...." 或 ["..."],
        "max_tokens": ...,
        "temperature": ...,
        "top_p": ...,
        "stream": true/false,
        ...
      }
    """
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "detail": str(e)},
        )

    # 取客户端 IP 和路径（用于构建内部 Request）
    client_ip = request.client.host if request.client else "unknown"
    url_path = request.url.path

    # 把用户请求头转成 dict[str, str]
    raw_headers = {k.lower(): v for k, v in request.headers.items()}

    scheduler_instance = request.app.state.global_scheduler

    # 构建内部 Request（包含 Prompt/Service/Task 等）
    req_obj = _handle_client(url_path, payload, client_ip, scheduler_instance, request.app)
    try:
        await _prepare_pd_request(req_obj)
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"error": "pd_instance_selection_failed", "detail": str(exc)},
        )

    # 根据调度结果选择下游 URL
    host = req_obj.Task.P_proxy_addr
    port = req_obj.Task.P_proxy_port
    endpoint = getattr(req_obj.Service, "Endpoint_type", "completions")
    downstream_url = f"http://{host}:{port}/v1/{endpoint}"

    # 转化payload
    data_for_downstream = req_obj.to_payload()

    # 处理需要透传给下游的 headers（可选择性过滤）
    extra_headers = {}

    # 把用户的 Authorization 原样传给下游（如果希望由 Proxy 做鉴权）
    if "authorization" in raw_headers:
        extra_headers["authorization"] = raw_headers["authorization"]

    # 带一个 Scheduler分配的Request ID，便于链路追踪
    extra_headers["scheduler-request-id"] = str(req_obj.Request_ID)
    extra_headers.update(get_connector_manager().headers(req_obj))

    # 定义一个 async 生成器，从下游流式读取，再回给上游
    async def iter_upstream():
        start_time = time.perf_counter()
        response_body = b""
        try:
            async for content in forward_request(
                    url=downstream_url,
                    data=data_for_downstream,
                    use_chunked=False,
                    extra_headers=extra_headers,
            ):
                response_body += content
                yield content
            _log_non_stream_slo_trace(req_obj, start_time, response_body)
        finally:
            get_connector_manager().close_session(
                req_obj.Task.connector_session_id
            )

    # 用 StreamingResponse 包一层，实现用户侧的流式响应
    return StreamingResponse(iter_upstream(), media_type="application/json")
