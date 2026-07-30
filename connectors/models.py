from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping


class ConnectorMode(str, Enum):
    REQUEST_SCOPED = "request_scoped"
    SHARED_BACKEND = "shared_backend"


class ConnectorSessionState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True)
class ConnectorDescriptor:
    name: str
    version: str = "1"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> "ConnectorDescriptor":
        if isinstance(payload, cls):
            return payload
        if isinstance(payload, str):
            payload = {"name": payload}
        if not isinstance(payload, Mapping):
            raise ValueError("connector must be an object with name, version and metadata")
        name = str(payload.get("name", "")).strip().lower()
        if not name:
            raise ValueError("connector.name is required")
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise ValueError("connector.metadata must be an object")
        return cls(
            name=name,
            version=str(payload.get("version", "1")).strip() or "1",
            metadata=dict(metadata),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "metadata": dict(self.metadata),
        }


@dataclass
class ConnectorSession:
    session_id: str
    connector: ConnectorDescriptor
    prefill_endpoint: Mapping[str, Any]
    decode_endpoint: Mapping[str, Any]
    context: Dict[str, Any] = field(default_factory=dict)
    state: ConnectorSessionState = ConnectorSessionState.CREATED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "connector": self.connector.to_dict(),
            "prefill_endpoint": dict(self.prefill_endpoint),
            "decode_endpoint": dict(self.decode_endpoint),
            "context": dict(self.context),
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
