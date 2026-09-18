from __future__ import annotations

from typing import Any, Dict, Mapping

from connectors.models import (
    ConnectorDescriptor,
    ConnectorMode,
    ConnectorSession,
    ConnectorSessionState,
)


class NixlAdapter:
    name = "nixl"
    mode = ConnectorMode.REQUEST_SCOPED
    requires_transfer_context = True
    header = "X-Request-Id"

    def validate_descriptor(self, descriptor):
        metadata = dict(descriptor.metadata)
        if metadata:
            for key in ("side_channel_host", "side_channel_port", "tp_size"):
                if key not in metadata:
                    raise ValueError(f"nixl connector metadata requires {key}")
            if not str(metadata["side_channel_host"]).strip():
                raise ValueError("nixl side_channel_host is required")
            metadata["side_channel_port"] = int(metadata["side_channel_port"])
            metadata["tp_size"] = int(metadata["tp_size"])
            if metadata["side_channel_port"] <= 0 or metadata["tp_size"] <= 0:
                raise ValueError("nixl side_channel_port and tp_size must be positive")
        return ConnectorDescriptor(self.name, descriptor.version, metadata)

    def compatible(self, prefill, decode):
        return prefill.name == decode.name == self.name and prefill.version == decode.version

    def validate_instance(self, descriptor, pool_descriptor, role):
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported connector instance role: {role}")
        normalized = self.validate_descriptor(descriptor)
        pool = self.validate_descriptor(pool_descriptor)
        if not self.compatible(normalized, pool):
            raise ValueError("nixl instance connector does not match its pool")
        if not normalized.metadata:
            raise ValueError("nixl instances require side-channel connector metadata")
        return normalized

    def compatible_endpoints(self, prefill_endpoint, decode_endpoint):
        if (
            not isinstance(prefill_endpoint, Mapping)
            or not isinstance(decode_endpoint, Mapping)
            or prefill_endpoint.get("role") != "prefill"
            or decode_endpoint.get("role") != "decode"
        ):
            return False
        try:
            prefill = self.validate_descriptor(prefill_endpoint.get("descriptor"))
            decode = self.validate_descriptor(decode_endpoint.get("descriptor"))
        except (TypeError, ValueError):
            return False
        return self.compatible(prefill, decode)

    def instance_conflict(self, candidate, existing):
        return False

    def create_session(self, session_id, prefill, decode, descriptor):
        return ConnectorSession(
            session_id=session_id,
            connector=descriptor,
            prefill_endpoint=dict(prefill),
            decode_endpoint=dict(decode),
            context={"request_id": session_id},
            state=ConnectorSessionState.ACTIVE,
        )

    @staticmethod
    def _context(request) -> Dict[str, Any]:
        from fastapi import HTTPException
        context = getattr(getattr(request, "Task", None), "connector_context", None)
        if not isinstance(context, dict):
            raise HTTPException(status_code=503, detail="Missing connector context")
        return context

    def headers(self, request):
        request_id = self._context(request).get("request_id")
        return {self.header: str(request_id)} if request_id else {}

    def forward_headers(self, headers):
        request_id = headers.get(self.header)
        return {self.header: request_id} if request_id else {}

    def build_prefill_payload(self, request):
        from core.schemas.request import BaseRequest
        payload = BaseRequest.to_vllm_payload(request)
        payload["stream"] = False
        payload["max_tokens"] = 1
        if "max_completion_tokens" in payload:
            payload["max_completion_tokens"] = 1
        payload.pop("stream_options", None)
        payload["kv_transfer_params"] = {
            "do_remote_decode": True,
            "do_remote_prefill": False,
            "remote_engine_id": None,
            "remote_block_ids": None,
            "remote_host": None,
            "remote_port": None,
        }
        return payload

    def bind_prefill_result(self, request, response_json):
        from fastapi import HTTPException
        def find(value):
            if isinstance(value, dict):
                params = value.get("kv_transfer_params")
                if isinstance(params, dict):
                    return params
                for child in value.values():
                    found = find(child)
                    if found:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find(child)
                    if found:
                        return found
            return None

        params = find(response_json)
        if not params:
            raise HTTPException(
                status_code=502,
                detail="Prefill response does not contain kv_transfer_params",
            )
        self._context(request)["kv_transfer_params"] = params

    def build_decode_payload(self, request):
        from fastapi import HTTPException
        from core.schemas.request import BaseRequest
        params = self._context(request).get("kv_transfer_params")
        if not isinstance(params, dict) or not params:
            raise HTTPException(
                status_code=503,
                detail="Missing NIXL kv_transfer_params before Decode",
            )
        payload = BaseRequest.to_vllm_payload(request)
        payload["kv_transfer_params"] = params
        return payload
