from __future__ import annotations

from connectors.models import (
    ConnectorDescriptor,
    ConnectorMode,
    ConnectorSession,
    ConnectorSessionState,
)


class LMCacheAdapter:
    name = "lmcache"
    mode = ConnectorMode.SHARED_BACKEND
    compatibility_keys = ("backend", "namespace", "config_fingerprint")

    def validate_descriptor(self, descriptor):
        metadata = dict(descriptor.metadata)
        missing = [key for key in self.compatibility_keys if not metadata.get(key)]
        if missing:
            raise ValueError(
                "lmcache connector metadata requires " + ", ".join(missing)
            )
        return ConnectorDescriptor(self.name, descriptor.version, metadata)

    def compatible(self, prefill, decode):
        if prefill.name != self.name or decode.name != self.name:
            return False
        if prefill.version != decode.version:
            return False
        return all(
            prefill.metadata.get(key) == decode.metadata.get(key)
            for key in self.compatibility_keys
        )

    def create_session(self, session_id, prefill, decode, descriptor):
        return ConnectorSession(
            session_id=session_id,
            connector=descriptor,
            prefill_endpoint=dict(prefill),
            decode_endpoint=dict(decode),
            context={
                key: descriptor.metadata[key] for key in self.compatibility_keys
            },
            state=ConnectorSessionState.ACTIVE,
        )

    def headers(self, request):
        return {}

    def forward_headers(self, headers):
        return {}

    def build_prefill_payload(self, request):
        from core.schemas.request import BaseRequest
        payload = BaseRequest.to_vllm_payload(request)
        payload["stream"] = False
        payload["max_tokens"] = 1
        if "max_completion_tokens" in payload:
            payload["max_completion_tokens"] = 1
        payload.pop("stream_options", None)
        return payload

    def bind_prefill_result(self, request, response_json):
        return None

    def build_decode_payload(self, request):
        from core.schemas.request import BaseRequest
        return BaseRequest.to_vllm_payload(request)
