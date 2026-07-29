from fastapi import HTTPException, Request

from core.schemas.request import BaseRequest
from scheduler.gateway.decode.manager import SchedulerManager
from store.gateway.manager import GatewayStoreManager


def _active_decode_instance(storage: GatewayStoreManager, instance_addr: str) -> bool:
    return bool(instance_addr and instance_addr in storage.active_decode_objs)


async def select_instance(raw_request: Request):
    request_json = await raw_request.json()
    request = BaseRequest.recover_request(
        request_json,
        (request_json.get("Service") or {}).get("Endpoint_type", "chat/completions"),
    )
    storage = GatewayStoreManager()

    instance = getattr(request.Task, "decode_instance", "")
    if not _active_decode_instance(storage, instance):
        strategy = storage.get_decode_scheduler_strategy()
        scheduler = SchedulerManager(strategy=strategy, storage=storage)
        instance = await scheduler.schedule(request)

    if not _active_decode_instance(storage, instance):
        raise HTTPException(status_code=503, detail="No active decode instance")

    nixl_meta = storage.get_nixl_metadata(instance)
    if not nixl_meta:
        raise HTTPException(status_code=503, detail=f"Missing NIXL metadata for decode instance {instance}")
    if nixl_meta.get("connector") != "NixlConnector" or nixl_meta.get("role") != "decode":
        raise HTTPException(status_code=503, detail=f"Invalid NIXL metadata for decode instance {instance}: {nixl_meta}")

    return {
        "instance": instance,
        "kv_addr": "",
        "nixl": nixl_meta,
    }
