from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, Mapping, Optional

from connectors.adapters import (
    LMCacheAdapter,
    MooncakeP2PHCCLAdapter,
    NixlAdapter,
)
from connectors.models import ConnectorDescriptor, ConnectorSessionState
from connectors.registry import ConnectorRegistry


class ConnectorManager:
    def __init__(self, registry: Optional[ConnectorRegistry] = None):
        self.registry = registry or ConnectorRegistry(
            (NixlAdapter(), LMCacheAdapter(), MooncakeP2PHCCLAdapter())
        )
        self._sessions: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def validate(self, descriptor) -> ConnectorDescriptor:
        return self.registry.validate(descriptor)

    def compatible(self, prefill, decode) -> bool:
        try:
            p = self.validate(prefill)
            d = self.validate(decode)
        except ValueError:
            return False
        if p.name != d.name:
            return False
        return self.registry.get(p.name).compatible(p, d)

    def validate_instance(self, descriptor, pool_descriptor, role):
        instance = self.validate(descriptor)
        pool = self.validate(pool_descriptor)
        if instance.name != pool.name or instance.version != pool.version:
            raise ValueError(
                f"instance connector {instance.name}:{instance.version} "
                f"does not match pool connector {pool.name}:{pool.version}"
            )
        adapter = self.registry.get(instance.name)
        validator = getattr(adapter, "validate_instance", None)
        if validator is None:
            return instance
        return validator(instance, pool, role)

    def compatible_endpoints(self, prefill_endpoint, decode_endpoint) -> bool:
        if not isinstance(prefill_endpoint, Mapping) or not isinstance(
            decode_endpoint, Mapping
        ):
            return False
        try:
            prefill_descriptor = self.validate(prefill_endpoint.get("descriptor"))
            decode_descriptor = self.validate(decode_endpoint.get("descriptor"))
        except (TypeError, ValueError):
            return False
        if prefill_descriptor.name != decode_descriptor.name:
            return False
        adapter = self.registry.get(prefill_descriptor.name)
        checker = getattr(adapter, "compatible_endpoints", None)
        if checker is None:
            return self.compatible(prefill_descriptor, decode_descriptor)
        normalized_prefill = dict(prefill_endpoint)
        normalized_decode = dict(decode_endpoint)
        normalized_prefill["descriptor"] = prefill_descriptor
        normalized_decode["descriptor"] = decode_descriptor
        return bool(checker(normalized_prefill, normalized_decode))

    def instance_conflict(self, candidate, existing) -> bool:
        try:
            candidate_descriptor = self.validate(candidate)
            existing_descriptor = self.validate(existing)
        except (TypeError, ValueError):
            return False
        if (
            candidate_descriptor.name != existing_descriptor.name
            or candidate_descriptor.version != existing_descriptor.version
        ):
            return False
        adapter = self.registry.get(candidate_descriptor.name)
        checker = getattr(adapter, "instance_conflict", None)
        return bool(checker(candidate_descriptor, existing_descriptor)) if checker else False

    def requires_transfer_context(self, connector) -> bool:
        if isinstance(connector, Mapping):
            name = connector.get("name", "")
        else:
            name = connector
        try:
            return bool(self.registry.get(name).requires_transfer_context)
        except (TypeError, ValueError, AttributeError):
            return False

    def create_session(
        self,
        prefill: Mapping[str, Any],
        decode: Mapping[str, Any],
        descriptor,
        session_id: Optional[str] = None,
    ):
        validated = self.validate(descriptor)
        prefill_descriptor = prefill.get("connector", validated.to_dict())
        decode_descriptor = decode.get("connector", validated.to_dict())
        if not self.compatible(validated, prefill_descriptor):
            raise ValueError("selected connector and prefill endpoint are incompatible")
        if not self.compatible(validated, decode_descriptor):
            raise ValueError("prefill and decode connector descriptors are incompatible")
        session_id = session_id or f"{validated.name}-{uuid.uuid4().hex}"
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing and existing.state != ConnectorSessionState.CLOSED:
                return existing
            session = self.registry.get(validated.name).create_session(
                session_id, prefill, decode, validated
            )
            self._sessions[session_id] = session
            return session

    def close_session(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            session.state = ConnectorSessionState.CLOSED
            session.updated_at = time.time()
            return True

    def get_session(self, session_id: str):
        with self._lock:
            return self._sessions.get(session_id)

    def adapter_for_request(self, request):
        name = getattr(getattr(request, "Task", None), "pd_connector", "")
        return self.registry.get(name)

    def headers(self, request):
        return self.adapter_for_request(request).headers(request)

    def build_prefill_payload(self, request):
        return self.adapter_for_request(request).build_prefill_payload(request)

    def bind_prefill_result(self, request, response_json):
        return self.adapter_for_request(request).bind_prefill_result(
            request, response_json
        )

    def build_decode_payload(self, request):
        return self.adapter_for_request(request).build_decode_payload(request)


_default_manager = ConnectorManager()


def get_connector_manager() -> ConnectorManager:
    return _default_manager
