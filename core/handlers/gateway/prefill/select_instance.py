from fastapi import HTTPException, Request

from config.ADDRESS_CONFIG import get_nixl_meta_for_instance
from core.schemas.request import BaseRequest
from scheduler.gateway.prefill.async_base import AsyncBaseScheduler
from store.gateway.manager import GatewayStoreManager


def _active_prefill_instance(storage: GatewayStoreManager, instance_addr: str) -> bool:
    return bool(instance_addr and instance_addr in storage.active_prefill_objs)


async def select_instance(raw_request: Request):
    request_json = await raw_request.json()
    request = BaseRequest.recover_request(
        request_json,
        (request_json.get("Service") or {}).get("Endpoint_type", "chat/completions"),
    )
    storage = GatewayStoreManager()

    instance = getattr(request.Task, "prefill_instance", "")
    if not _active_prefill_instance(storage, instance):
        scheduler = AsyncBaseScheduler(storage)
        instance = scheduler.schedule(request, "prefill")

    if not _active_prefill_instance(storage, instance):
        raise HTTPException(status_code=503, detail="No active prefill instance")

    nixl_meta = get_nixl_meta_for_instance(instance)
    if not nixl_meta:
        raise HTTPException(status_code=503, detail=f"Missing NIXL metadata for prefill instance {instance}")
    if nixl_meta.get("connector") != "NixlConnector" or nixl_meta.get("role") != "prefill":
        raise HTTPException(status_code=503, detail=f"Invalid NIXL metadata for prefill instance {instance}: {nixl_meta}")

    return {
        "instance": instance,
        "kv_addr": "",
        "nixl": nixl_meta,
    }
