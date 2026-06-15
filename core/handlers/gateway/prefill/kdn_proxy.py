import json
import random
from typing import List, Tuple, Dict, Any
import os
import logging
from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import JSONResponse, StreamingResponse
from proxy.forward import forward_request
from core.schemas.request import BaseRequest as SchedulerRequest, Prompt, Service, Task
from store.gateway.manager import GatewayStoreManager

storage = GatewayStoreManager()


def _format_retrieved_context(items: List[Dict[str, Any]]) -> str:
    """把 KDN 返回 items 格式化为可注入的检索上下文文本。"""
    lines = []
    for idx, it in enumerate(items, start=1):
        content = (it.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"({idx}) {content}")
    return "\n".join(lines).strip()


async def _fetch_knowledge_from_kdn(knowledge_ids: List[int]) -> Tuple[List[Dict[str, Any]], List[int]]:
    """
    向 KDN 请求知识文本（非流式）。
    返回：(items, miss)
    """
    if not knowledge_ids:
        return [], []

    url = f"{storage.KDN_BASE_URL}/knowledge/search/text"
    body = {
        "knowledge_ids": knowledge_ids,
        "need_fields": ["content", "length", "rel_path"],
    }

    content_bytes = b""
    async for chunk in forward_request(url, data=body, use_chunked=False):
        if chunk:
            content_bytes += chunk

    # forward_request 在 4xx/5xx 会抛 HTTPException，这里只处理 2xx 情况
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = content_bytes.decode("utf-8", errors="ignore")

    try:
        resp = json.loads(text) if text else {}
    except json.JSONDecodeError:
        raise RuntimeError(f"KDN response is not valid JSON: {text[:200]}")

    items = resp.get("items") or []
    miss = resp.get("miss") or []
    if not isinstance(items, list):
        items = []
    if not isinstance(miss, list):
        miss = []

    return items, miss


def _inject_rag_into_instance_body(
    instance_body: Dict[str, Any],
    endpoint_type: str,
    retrieved_context: str,
) -> Dict[str, Any]:
    """
    将 retrieved_context 注入 instance_body（OpenAI 风格）。
    返回新的 body（不修改原 dict，避免副作用）。
    """
    if not retrieved_context:
        return instance_body

    new_body = dict(instance_body)

    if endpoint_type == "chat/completions":
        # messages 头插 system 消息
        msgs = list(new_body.get("messages") or [])
        system_prompt = (
            f"You are a helpful assistant.\n Use the following retrieved context to answer the user. If the context is not relevant, ignore it.\n ### Retrieved Context\n {retrieved_context}\n"
        )
        msgs.insert(0, {"role": "system", "content": system_prompt})
        new_body["messages"] = msgs
        return new_body

    # completions：前缀 prompt
    prompt = (new_body.get("prompt") or "")
    rag_prefix = (
        f"You are a helpful assistant.\n Use the following retrieved context to answer the user. If the context is not relevant, ignore it.\n ### Retrieved Context\n {retrieved_context}\n ### User Prompt\n"
    )
    new_body["prompt"] = rag_prefix + str(prompt)
    return new_body


async def kdn_proxy_chat_completions(request: FastAPIRequest, req_obj: SchedulerRequest):
    """
    接收来自 Scheduler 的 /v1/chat/completions 请求（payload为 Request JSON）。
    转发为 OpenAI chat/completions body 到 Worker（流式）
    """
    # try:
    #     payload: Dict[str, Any] = await request.json()
    # except Exception as e:
    #     logging.exception("[Proxy] chat/completions 解析 JSON 失败")
    #     return JSONResponse(
    #         status_code=400,
    #         content={"error": "invalid_json", "detail": str(e)},
    #     )
    #
    # # 恢复内部 Request
    # try:
    #     req_obj = SchedulerRequest.recover_request(payload)
    # except Exception as e:
    #     logging.exception("[Proxy] 恢复 Request 失败")
    #     return JSONResponse(
    #         status_code=400,
    #         content={"error": "invalid_request_payload", "detail": str(e)},
    #     )

    # 构造 Instance 请求体
    instance_body = SchedulerRequest.to_vllm_payload(req_obj)

    # 判断是否需要知识注入，若需要则先注入再转发
    enable_rag = bool(getattr(req_obj.Service, "Enable_know_injection", False))
    knowledge_ids = getattr(req_obj.Service, "Knowledge_List", []) or []
    if enable_rag and knowledge_ids:
        try:
            logging.info(f"[Proxy] 检测到启用知识注入，向{storage.KDN_BASE_URL}获取知识")
            items, miss = await _fetch_knowledge_from_kdn([str(x) for x in knowledge_ids])
            retrieved_context = _format_retrieved_context(items)
            if miss:
                logging.info("[Proxy] KDN miss ids=%s", miss)

            # 注入到 instance_body
            instance_body = _inject_rag_into_instance_body(
                instance_body=instance_body,
                endpoint_type="chat/completions",
                retrieved_context=retrieved_context,
            )
            logging.info("[Proxy] RAG injected: ids=%s, ctx_len=%s", knowledge_ids, len(retrieved_context))
        except Exception as e:
            # 先跑通：KDN 出错时不阻断推理，退化为不注入直接转发
            logging.exception("[Proxy] KDN fetch failed, fallback to no-rag. err=%s", str(e))

    # host = req_obj.Task.prefill_instance
    # TODO: 调度算法
    active_prefill_objs = storage.active_prefill_objs
    if not active_prefill_objs:
        return JSONResponse(status_code=503, content={"error": "no_active_prefill_instance"})
    host = random.choice(active_prefill_objs)
    instance_url = f"http://{host}/v1/chat/completions"

    logging.info(
        "[Proxy] 转发到 Instance(chat): url=%s, model=%s",
        instance_url,
        json.dumps(instance_body,ensure_ascii=False)[:1200],
        # instance_body.get("model"),
    )

    # 下游流式：直接把 Instance 的 SSE 往上游透传
    try:
        stream_gen = forward_request(instance_url, data=instance_body, use_chunked=True)
        return StreamingResponse(stream_gen, media_type="text/event-stream")
    except Exception as e:
        logging.exception("[Proxy] 调用 Worker(chat) 失败")
        return JSONResponse(
            status_code=502,
            content={"error": "worker_chat_failed", "detail": str(e)},
        )


async def kdn_proxy_completions(request: FastAPIRequest, req_obj: SchedulerRequest):
    """
    接收来自 Scheduler 的 /v1/completions 请求。
    Demo 里逻辑与 chat/completions 相同，只是留出扩展空间。
    """
    # try:
    #     payload: Dict[str, Any] = await request.json()
    # except Exception as e:
    #     logging.exception("[Proxy] completions 解析 JSON 失败")
    #     return JSONResponse(
    #         status_code=400,
    #         content={"error": "invalid_json", "detail": str(e)},
    #     )
    #
    # # 恢复内部 Request
    # try:
    #     req_obj = SchedulerRequest.recover_request(payload)
    # except Exception as e:
    #     logging.exception("[Proxy] 恢复 Request 失败")
    #     return JSONResponse(
    #         status_code=400,
    #         content={"error": "invalid_request_payload", "detail": str(e)},
    #     )

    # 构造 Instance 请求体
    instance_body = SchedulerRequest.to_vllm_payload(req_obj)

    enable_rag = bool(getattr(req_obj.Service, "Enable_know_injection", False))
    knowledge_ids = getattr(req_obj.Service, "Knowledge_List", []) or []
    if enable_rag and knowledge_ids:
        try:
            logging.info(f"[Proxy] 检测到启用知识注入，向{storage.KDN_BASE_URL}获取知识")
            items, miss = await _fetch_knowledge_from_kdn(list(knowledge_ids))
            retrieved_context = _format_retrieved_context(items)
            instance_body = _inject_rag_into_instance_body(
                instance_body=instance_body,
                endpoint_type="completions",
                retrieved_context=retrieved_context,
            )
            logging.info("[Proxy] RAG injected: ids=%s, ctx_len=%s", knowledge_ids, len(retrieved_context))
        except Exception as e:
            logging.exception("[Proxy] KDN fetch failed, fallback to no-rag. err=%s", str(e))

    # host = req_obj.Task.prefill_instance
    # TODO: 调度算法
    active_prefill_objs = storage.active_prefill_objs
    if not active_prefill_objs:
        return JSONResponse(status_code=503, content={"error": "no_active_prefill_instance"})
    host = random.choice(active_prefill_objs)
    instance_url = f"http://{host}/v1/completions"

    logging.info(
        "[Proxy] 转发到 Instance(completions): url=%s, model=%s",
        instance_url,
        json.dumps(instance_body, ensure_ascii=False)[:1200],
        # instance_body.get("model"),
    )

    # 下游非流式：forward_request 仍然返回一个 async 生成器，但只会 yield 一次 bytes
    try:
        content_bytes = b""
        async for chunk in forward_request(instance_url, data=instance_body, use_chunked=False):
            if chunk:
                content_bytes += chunk
    except Exception as e:
        logging.exception("[Proxy] 调用 Worker(completions) 失败")
        return JSONResponse(
            status_code=502,
            content={"error": "worker_completions_failed", "detail": str(e)},
        )

    # 解析 Worker 返回内容
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    try:
        resp_json = json.loads(text) if text else {}
        return JSONResponse(content=resp_json)
    except json.JSONDecodeError:
        # Worker 如果返回的不是 JSON，就直接把文本透传上去
        return JSONResponse(
            content={
                "raw_text": text,
                "parse_warning": "Worker 返回内容不是合法 JSON，已以文本形式透传。",
            }
        )
