from __future__ import annotations

import uuid
from typing import Any, Dict

from fastapi import HTTPException


NIXL_HEADER = "X-Request-Id"


def build_nixl_request_id(request_id: Any) -> str:
    return f"nixl-{request_id}-{uuid.uuid4().hex}"


def nixl_headers_for_request(request) -> Dict[str, str]:
    nixl_request_id = getattr(getattr(request, "Task", None), "nixl_request_id", "")
    if not nixl_request_id:
        return {}
    return {NIXL_HEADER: str(nixl_request_id)}


def initial_kv_transfer_params() -> Dict[str, Any]:
    return {
        "do_remote_decode": True,
        "do_remote_prefill": False,
        "remote_engine_id": None,
        "remote_block_ids": None,
        "remote_host": None,
        "remote_port": None,
    }


def build_nixl_prefill_payload(request) -> Dict[str, Any]:
    from core.schemas.request import BaseRequest

    payload = BaseRequest.to_vllm_payload(request)
    payload["stream"] = False
    payload["max_tokens"] = 1
    if "max_completion_tokens" in payload:
        payload["max_completion_tokens"] = 1
    payload.pop("stream_options", None)
    payload["kv_transfer_params"] = initial_kv_transfer_params()
    return payload


def _find_kv_transfer_params(obj: Any):
    if isinstance(obj, dict):
        value = obj.get("kv_transfer_params")
        if isinstance(value, dict):
            return value
        for child in obj.values():
            found = _find_kv_transfer_params(child)
            if isinstance(found, dict):
                return found
    if isinstance(obj, list):
        for child in obj:
            found = _find_kv_transfer_params(child)
            if isinstance(found, dict):
                return found
    return None


def extract_kv_transfer_params(response_json: Dict[str, Any]) -> Dict[str, Any]:
    params = _find_kv_transfer_params(response_json)
    if not isinstance(params, dict) or not params:
        raise HTTPException(
            status_code=502,
            detail="Prefill response does not contain kv_transfer_params",
        )
    return params


def build_nixl_decode_payload(request) -> Dict[str, Any]:
    from core.schemas.request import BaseRequest

    params = getattr(getattr(request, "Task", None), "nixl_kv_transfer_params", None)
    if not isinstance(params, dict) or not params:
        raise HTTPException(
            status_code=503,
            detail="Missing NIXL kv_transfer_params before Decode",
        )

    payload = BaseRequest.to_vllm_payload(request)
    payload["kv_transfer_params"] = params
    return payload
