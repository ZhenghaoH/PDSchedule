from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Any, Dict, List
import logging

from core.lifespans.knowledge.kdn_sync import kdn_refresh_once
from store.knowledge import DummyEmbeddingModel, init_knowledge_table, KnowledgeTable, KnowledgeUnit


class PeekPayload(BaseModel):
    kids: List[str]
    need_fields: List[str] = ["length", "avail_kdn_servers", "text_abstract"]


async def debug_status(request: FastAPIRequest) -> Dict[str, Any]:
    """
    Debug endpoint for Scheduler CLI.

    Returns:
      - knowledge_loaded: whether knowledge_table is ready
      - entries: number of knowledge units
      - dim: embedding dim
      - faiss_total: index.ntotal if built (else None)
      - kdn_base_url: where snapshot comes from (if any)
      - last_refresh_ts: unix seconds (if you maintain it), else None
      - unit_fields: what fields a KnowledgeUnit contains (for inspection)
      - sample_kids: first few kids (for quick view)
    """
    scheduler = request.app
    table = getattr(scheduler.state, "knowledge_table", None)   # type: ignore
    kdn_base_url = getattr(scheduler.state, "kdn_base_url", None)   # type: ignore
    last_refresh_ts = getattr(scheduler.state, "last_refresh_ts", None)  # type: ignore

    if table is None:
        return {
            "knowledge_loaded": False,
            "entries": 0,
            "dim": None,
            "faiss_total": None,
            "kdn_base_url": kdn_base_url,
            "last_refresh_ts": last_refresh_ts,
            "unit_fields": [],
            "sample_kids": [],
        }

    units = getattr(table, "_units", {})
    kids = list(units.keys())
    kids_sorted = sorted(kids)[:10]

    # 尽量从 table 拿到 dim
    dim = getattr(table, "dim", None)

    # FAISS
    faiss_total = None
    idx = getattr(table, "_faiss_index", None)
    if idx is not None:
        try:
            faiss_total = int(getattr(idx, "ntotal"))
        except Exception:
            faiss_total = None

    # KnowledgeUnit 字段探测（帮助你看 snapshot 后到底存了什么）
    unit_fields: List[str] = []
    if kids_sorted:
        u0 = units[kids_sorted[0]]
        # dataclass 一般有 __dict__
        if hasattr(u0, "__dict__"):
            unit_fields = sorted(list(u0.__dict__.keys()))
        else:
            # fallback：dir 过滤
            unit_fields = [x for x in dir(u0) if not x.startswith("_")]

    return {
        "knowledge_loaded": True,
        "entries": len(units),
        "dim": dim,
        "faiss_total": faiss_total,
        "kdn_base_url": kdn_base_url,
        "last_refresh_ts": last_refresh_ts,
        "unit_fields": unit_fields,
        "sample_kids": kids_sorted,
    }


async def debug_peek_knowledge(request: FastAPIRequest, payload: PeekPayload) -> Dict[str, Any]:
    """
    Peek knowledge units by kids.
    Default fields are safe (no full embedding).
    """
    scheduler = request.app
    table = getattr(scheduler.state, "knowledge_table", None)   # type: ignore
    if table is None:
        return {"items": [], "miss": payload.kids}

    units = getattr(table, "_units", {})
    items = []
    miss = []

    for kid in payload.kids:
        k = (kid or "").strip().lower()
        u = units.get(k)
        if u is None:
            miss.append(kid)
            continue

        it = {"kid": k}
        allow = set(payload.need_fields or [])

        if "length" in allow:
            it["length"] = getattr(u, "length", None)
        if "avail_kdn_servers" in allow:
            it["avail_kdn_servers"] = getattr(u, "avail_kdn_servers", None)
        if "text_abstract" in allow:
            it["text_abstract"] = getattr(u, "text_abstract", None)

        # 可选：你若在 KnowledgeUnit/metadata 里存了 kv_ready 等，也可以在这里输出
        if "meta" in allow and hasattr(u, "meta"):
            it["meta"] = getattr(u, "meta")
        if "kv_ready" in allow:
            it["kv_ready"] = getattr(u, "kv_ready", 0)
        if "kv_rel_dir" in allow:
            it["kv_rel_dir"] = getattr(u, "kv_rel_dir", None)
        if "kv_dumped_keys" in allow:
            it["kv_dumped_keys"] = getattr(u, "kv_dumped_keys", None)
        if "kv_updated_at" in allow:
            it["kv_updated_at"] = getattr(u, "kv_updated_at", None)

        items.append(it)

    return {"items": items, "miss": miss}


async def admin_refresh_knowledge(request: FastAPIRequest):
    try:
        r = await kdn_refresh_once(request.app)
        return JSONResponse(content=r)
    except Exception as e:
        logging.exception(f"[Scheduler] admin refresh failed: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

# 预留：/knowledge/update 等路由以后再加
# @api.post("/knowledge/update")
# async def knowledge_update(request: FastAPIRequest):
#     payload = await request.json()
#     client_ip = request.client.host if request.client else "unknown"
#     ...
#     return JSONResponse(content={"status": "ok"})
