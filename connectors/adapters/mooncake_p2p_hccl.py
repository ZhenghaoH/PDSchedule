from __future__ import annotations

from typing import Any, Dict, Mapping

from connectors.models import (
    ConnectorDescriptor,
    ConnectorMode,
    ConnectorSession,
    ConnectorSessionState,
)


class MooncakeP2PHCCLAdapter:
    """Adapter for the vLLM-Ascend Mooncake P2P HCCL data path.

    PDSchedule only selects a compatible P/D pair and relays the transfer
    parameters returned by the Prefill worker.  The Mooncake handshake and
    KV transfer remain inside vLLM-Ascend.
    """

    name = "mooncake_p2p_hccl"
    mode = ConnectorMode.REQUEST_SCOPED
    requires_transfer_context = True
    header = "X-Request-Id"

    compatibility_keys = (
        "platform",
        "transport",
        "connector_impl",
        "handshake_model",
        "runtime_fingerprint",
        "model_fingerprint",
        "fabric_id",
        "dp_size",
        "tp_size",
        "pp_size",
    )
    instance_keys = (
        "engine_id",
        "instance_epoch",
        "side_channel_host",
        "side_channel_port",
        "device_count",
        "rank_start",
    )
    transfer_keys = (
        "do_remote_decode",
        "do_remote_prefill",
        "remote_engine_id",
        "remote_block_ids",
        "remote_host",
        "remote_port",
    )

    def validate_descriptor(self, descriptor):
        parsed = ConnectorDescriptor.from_payload(descriptor)
        if parsed.name != self.name:
            raise ValueError(
                f"expected {self.name} connector, got {parsed.name}"
            )

        metadata = dict(parsed.metadata)
        required = (
            "platform",
            "transport",
            "connector_impl",
            "handshake_model",
            "runtime_fingerprint",
            "model_fingerprint",
            "fabric_id",
            "dp_size",
            "tp_size",
            "pp_size",
        )
        missing = [key for key in required if not metadata.get(key)]
        if missing:
            raise ValueError(
                "mooncake_p2p_hccl connector metadata requires "
                + ", ".join(missing)
            )

        if str(metadata["platform"]).strip().lower() != "ascend":
            raise ValueError(
                "mooncake_p2p_hccl platform must be 'ascend'"
            )
        if str(metadata["transport"]).strip().lower() != "hccl":
            raise ValueError(
                "mooncake_p2p_hccl transport must be 'hccl'"
            )
        if str(metadata["handshake_model"]).strip().lower() != "passive_relay":
            raise ValueError(
                "mooncake_p2p_hccl handshake_model must be 'passive_relay'"
            )
        metadata["platform"] = "ascend"
        metadata["transport"] = "hccl"
        metadata["handshake_model"] = "passive_relay"

        for key in ("tp_size", "pp_size"):
            metadata[key] = self._positive_int(metadata[key], key)
        metadata["dp_size"] = self._positive_int(metadata["dp_size"], "dp_size")
        for key in (
            "connector_impl",
            "runtime_fingerprint",
            "model_fingerprint",
            "fabric_id",
        ):
            if not str(metadata[key]).strip():
                raise ValueError(f"mooncake_p2p_hccl {key} is required")

        return ConnectorDescriptor(self.name, parsed.version, metadata)

    @staticmethod
    def _positive_int(value: Any, field: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"mooncake_p2p_hccl {field} must be a positive integer")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"mooncake_p2p_hccl {field} must be a positive integer"
            ) from exc
        if normalized <= 0:
            raise ValueError(
                f"mooncake_p2p_hccl {field} must be a positive integer"
            )
        return normalized

    def compatible(self, prefill, decode):
        if (
            prefill.name != self.name
            or decode.name != self.name
            or prefill.version != decode.version
        ):
            return False
        return all(
            prefill.metadata.get(key) == decode.metadata.get(key)
            for key in self.compatibility_keys
        )

    def validate_instance(self, descriptor, pool_descriptor, role):
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported connector instance role: {role}")
        normalized = self.validate_descriptor(descriptor)
        pool = self.validate_descriptor(pool_descriptor)
        if not self.compatible(normalized, pool):
            raise ValueError(
                "mooncake_p2p_hccl instance connector does not match its pool"
            )

        metadata = dict(normalized.metadata)
        missing = [
            key
            for key in self.instance_keys
            if metadata.get(key) is None or metadata.get(key) == ""
        ]
        if missing:
            raise ValueError(
                "mooncake_p2p_hccl instance metadata requires "
                + ", ".join(missing)
            )
        if not str(metadata["engine_id"]).strip():
            raise ValueError("mooncake_p2p_hccl engine_id is required")
        if not str(metadata["instance_epoch"]).strip():
            raise ValueError("mooncake_p2p_hccl instance_epoch is required")
        if not str(metadata["side_channel_host"]).strip():
            raise ValueError("mooncake_p2p_hccl side_channel_host is required")

        metadata["side_channel_port"] = self._port(
            metadata["side_channel_port"], "side_channel_port"
        )
        metadata["device_count"] = self._positive_int(
            metadata["device_count"], "device_count"
        )
        if isinstance(metadata["rank_start"], bool):
            raise ValueError("mooncake_p2p_hccl rank_start must be non-negative")
        try:
            metadata["rank_start"] = int(metadata["rank_start"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "mooncake_p2p_hccl rank_start must be non-negative"
            ) from exc
        if metadata["rank_start"] < 0:
            raise ValueError("mooncake_p2p_hccl rank_start must be non-negative")

        if "port_span" in metadata:
            metadata["port_span"] = self._positive_int(
                metadata["port_span"], "port_span"
            )
        else:
            metadata["port_span"] = metadata["device_count"]
        self._port_range(metadata)
        return ConnectorDescriptor(self.name, normalized.version, metadata)

    @staticmethod
    def _port(value: Any, field: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"mooncake_p2p_hccl {field} must be between 1 and 65535")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"mooncake_p2p_hccl {field} must be between 1 and 65535"
            ) from exc
        if not 1 <= normalized <= 65535:
            raise ValueError(
                f"mooncake_p2p_hccl {field} must be between 1 and 65535"
            )
        return normalized

    @staticmethod
    def _port_range(metadata: Mapping[str, Any]):
        start = int(metadata["side_channel_port"])
        span = int(metadata.get("port_span", metadata["device_count"]))
        end = start + span
        if end > 65536:
            raise ValueError(
                "mooncake_p2p_hccl side-channel port range exceeds 65535"
            )
        return start, end

    def compatible_endpoints(self, prefill_endpoint, decode_endpoint):
        if (
            not isinstance(prefill_endpoint, Mapping)
            or not isinstance(decode_endpoint, Mapping)
            or prefill_endpoint.get("role") != "prefill"
            or decode_endpoint.get("role") != "decode"
        ):
            return False
        try:
            prefill = self.validate_instance(
                prefill_endpoint.get("descriptor"),
                prefill_endpoint.get("descriptor"),
                "prefill",
            )
            decode = self.validate_instance(
                decode_endpoint.get("descriptor"),
                decode_endpoint.get("descriptor"),
                "decode",
            )
        except (TypeError, ValueError):
            return False
        if not self.compatible(prefill, decode):
            return False
        if prefill.metadata["engine_id"] == decode.metadata["engine_id"]:
            return False
        if not str(prefill.metadata["instance_epoch"]).strip():
            return False
        if not str(decode.metadata["instance_epoch"]).strip():
            return False
        return not self._same_host_port_overlap(prefill.metadata, decode.metadata)

    def instance_conflict(self, candidate, existing):
        try:
            first = self.validate_instance(candidate, candidate, "prefill")
            second = self.validate_instance(existing, existing, "decode")
        except (TypeError, ValueError):
            return False
        if not self.compatible(first, second):
            return False
        if first.metadata["engine_id"] == second.metadata["engine_id"]:
            return True
        return self._same_host_port_overlap(first.metadata, second.metadata)

    def _same_host_port_overlap(self, first, second) -> bool:
        if str(first.get("side_channel_host")) != str(second.get("side_channel_host")):
            return False
        first_start, first_end = self._port_range(first)
        second_start, second_end = self._port_range(second)
        return first_start < second_end and second_start < first_end

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

    def create_session(self, session_id, prefill, decode, descriptor):
        return ConnectorSession(
            session_id=session_id,
            connector=descriptor,
            prefill_endpoint=dict(prefill),
            decode_endpoint=dict(decode),
            context={"request_id": session_id},
            state=ConnectorSessionState.ACTIVE,
        )

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

    def _extract_transfer_params(self, response_json):
        if not isinstance(response_json, dict):
            return None
        params = response_json.get("kv_transfer_params")
        if isinstance(params, dict):
            return params
        choices = response_json.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if isinstance(choice, dict) and isinstance(
                    choice.get("kv_transfer_params"), dict
                ):
                    return choice["kv_transfer_params"]
        return None

    def _validate_transfer_params(self, params):
        if not isinstance(params, dict) or not params:
            raise ValueError("Prefill response does not contain kv_transfer_params")
        missing = [key for key in self.transfer_keys if key not in params]
        if missing:
            raise ValueError(
                "Mooncake Prefill kv_transfer_params missing " + ", ".join(missing)
            )
        if params["do_remote_decode"] is not True:
            raise ValueError("Mooncake kv_transfer_params must enable remote decode")
        if params["do_remote_prefill"] is not False:
            raise ValueError("Mooncake kv_transfer_params must disable remote prefill")
        if not isinstance(params["remote_engine_id"], str) or not params[
            "remote_engine_id"
        ].strip():
            raise ValueError("Mooncake remote_engine_id must be a non-empty string")
        if not isinstance(params["remote_block_ids"], list) or not params[
            "remote_block_ids"
        ]:
            raise ValueError("Mooncake remote_block_ids must be a non-empty list")
        if not isinstance(params["remote_host"], str) or not params[
            "remote_host"
        ].strip():
            raise ValueError("Mooncake remote_host must be a non-empty string")
        params["remote_port"] = self._port(params["remote_port"], "remote_port")
        return dict(params)

    def bind_prefill_result(self, request, response_json):
        from fastapi import HTTPException

        try:
            params = self._validate_transfer_params(
                self._extract_transfer_params(response_json)
            )
            context = self._context(request)
            prefill_endpoint = context.get("prefill_endpoint") or {}
            descriptor = self.validate_instance(
                prefill_endpoint.get("descriptor"),
                prefill_endpoint.get("descriptor"),
                "prefill",
            )
            metadata = descriptor.metadata
            if params["remote_engine_id"] != metadata["engine_id"]:
                raise ValueError(
                    "Mooncake remote_engine_id does not belong to selected Prefill"
                )
            if params["remote_host"] != metadata["side_channel_host"]:
                raise ValueError(
                    "Mooncake remote_host does not belong to selected Prefill"
                )
            start, end = self._port_range(metadata)
            if not start <= params["remote_port"] < end:
                raise ValueError(
                    "Mooncake remote_port is outside selected Prefill port range"
                )
            context["kv_transfer_params"] = params
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    def build_decode_payload(self, request):
        from fastapi import HTTPException
        from core.schemas.request import BaseRequest

        context = self._context(request)
        params = context.get("kv_transfer_params")
        try:
            params = self._validate_transfer_params(params)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        payload = BaseRequest.to_vllm_payload(request)
        payload["kv_transfer_params"] = params
        return payload
