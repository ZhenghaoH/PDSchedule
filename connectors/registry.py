from __future__ import annotations

from typing import Dict, Iterable

from connectors.models import ConnectorDescriptor
from connectors.protocol import ConnectorAdapter


class ConnectorRegistry:
    def __init__(self, adapters: Iterable[ConnectorAdapter] = ()):
        self._adapters: Dict[str, ConnectorAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ConnectorAdapter) -> None:
        name = adapter.name.strip().lower()
        if not name:
            raise ValueError("connector adapter name is required")
        if name in self._adapters:
            raise ValueError(f"connector adapter already registered: {name}")
        self._adapters[name] = adapter

    def get(self, name: str) -> ConnectorAdapter:
        canonical = str(name).strip().lower()
        try:
            return self._adapters[canonical]
        except KeyError as exc:
            supported = ", ".join(sorted(self._adapters))
            raise ValueError(
                f"unsupported connector {canonical!r}; supported: {supported}"
            ) from exc

    def validate(self, descriptor) -> ConnectorDescriptor:
        parsed = ConnectorDescriptor.from_payload(descriptor)
        return self.get(parsed.name).validate_descriptor(parsed)

    @property
    def names(self):
        return tuple(sorted(self._adapters))
