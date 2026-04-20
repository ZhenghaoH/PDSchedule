from __future__ import annotations

from typing import Any, Dict, List

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import StreamingResponse, JSONResponse

from core.schemas.request import BaseRequest as SchedulerRequest
from proxy.forward import forward_request

from utils import timing

from store.controller import PoolManager, PoolType
global_storage = PoolManager()


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
        embedder=getattr(app.state, "embedding_engine", None),  # type: ignore
        knowledge_table=getattr(app.state, "knowledge_table", None),  # type: ignore
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

    # 定义一个 async 生成器，从下游流式读取，再回给上游
    async def iter_upstream():
        try:
            # forward_request 本身是一个 async generator
            async for chunk in forward_request(
                url=downstream_url,
                data=data_for_downstream,
                use_chunked=True,             # chat/completions 一般是流式
                extra_headers=extra_headers,  # 透传必要头
            ):
                # 这里 chunk 已经是 bytes，直接 yield 出去即可
                yield chunk
        finally:
            decode_pool_id = global_storage.get_id_by_ip_port(
                PoolType.DECODE,
                req_obj.Task.D_proxy_addr,
                req_obj.Task.D_proxy_port,
            )
            if decode_pool_id:
                decode_pool = global_storage.get_pool_by_id(PoolType.DECODE, decode_pool_id)
                decode_mem_bandwidth = getattr(decode_pool, "mem_bandwidth", 0.0) if decode_pool else 0.0
                decode_request_load = (
                    float(req_obj.Prompt.token_length) / float(decode_mem_bandwidth)
                    if decode_mem_bandwidth and decode_mem_bandwidth > 0
                    else 0.0
                )
                global_storage.sub_decode_load(decode_pool_id, decode_request_load)

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

    # 定义一个 async 生成器，从下游流式读取，再回给上游
    async def iter_upstream():
        try:
            # forward_request 本身是一个 async generator
            async for content in forward_request(
                    url=downstream_url,
                    data=data_for_downstream,
                    use_chunked=False,  # chat/completions 一般是流式
                    extra_headers=extra_headers,  # 透传必要头
            ):
                # 这里 chunk 已经是 bytes，直接 yield 出去即可
                yield content
        finally:
            decode_pool_id = global_storage.get_id_by_ip_port(
                PoolType.DECODE,
                req_obj.Task.D_proxy_addr,
                req_obj.Task.D_proxy_port,
            )
            if decode_pool_id:
                decode_pool = global_storage.get_pool_by_id(PoolType.DECODE, decode_pool_id)
                decode_mem_bandwidth = getattr(decode_pool, "mem_bandwidth", 0.0) if decode_pool else 0.0
                decode_request_load = (
                    float(req_obj.Prompt.token_length) / float(decode_mem_bandwidth)
                    if decode_mem_bandwidth and decode_mem_bandwidth > 0
                    else 0.0
                )
                global_storage.sub_decode_load(decode_pool_id, decode_request_load)

    # 用 StreamingResponse 包一层，实现用户侧的流式响应
    return StreamingResponse(iter_upstream(), media_type="application/json")
