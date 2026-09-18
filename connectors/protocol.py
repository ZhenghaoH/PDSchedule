from __future__ import annotations

from typing import Any, Dict, Mapping, Protocol

from connectors.models import ConnectorDescriptor, ConnectorMode, ConnectorSession


class ConnectorAdapter(Protocol):
    name: str
    mode: ConnectorMode
    requires_transfer_context: bool

    def validate_descriptor(
        self, descriptor: ConnectorDescriptor
    ) -> ConnectorDescriptor: ...

    def compatible(
        self,
        prefill: ConnectorDescriptor,
        decode: ConnectorDescriptor,
    ) -> bool: ...

    def validate_instance(
        self,
        descriptor: ConnectorDescriptor,
        pool_descriptor: ConnectorDescriptor,
        role: str,
    ) -> ConnectorDescriptor: ...

    def compatible_endpoints(
        self,
        prefill_endpoint: Mapping[str, Any],
        decode_endpoint: Mapping[str, Any],
    ) -> bool: ...

    def instance_conflict(
        self,
        candidate: ConnectorDescriptor,
        existing: ConnectorDescriptor,
    ) -> bool: ...

    def create_session(
        self,
        session_id: str,
        prefill: Mapping[str, Any],
        decode: Mapping[str, Any],
        descriptor: ConnectorDescriptor,
    ) -> ConnectorSession: ...

    def headers(self, request: Any) -> Dict[str, str]: ...

    def forward_headers(self, headers: Mapping[str, str]) -> Dict[str, str]: ...

    def build_prefill_payload(self, request: Any) -> Dict[str, Any]: ...

    def bind_prefill_result(
        self, request: Any, response_json: Dict[str, Any]
    ) -> None: ...

    def build_decode_payload(self, request: Any) -> Dict[str, Any]: ...
