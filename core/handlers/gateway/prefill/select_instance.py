from fastapi import HTTPException, Request

from core.schemas.request import BaseRequest
from core.handlers.gateway.prefill.proxy import request_batcher
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
        strategy = storage.get_prefill_scheduler_strategy()
        scheduled, _ = await request_batcher.scheduler_mgr.schedule_batch(
            [request],
            strategy=strategy,
        )
        instance = scheduled[0].Task.prefill_instance if scheduled else ""

    if not _active_prefill_instance(storage, instance):
        raise HTTPException(status_code=503, detail="No active prefill instance")

    try:
        connector = storage.get_connector_metadata(instance)
        descriptor = connector.get("descriptor") or {}
        if connector.get("role") != "prefill" or descriptor.get("name") != request.Task.pd_connector:
            raise HTTPException(status_code=503, detail=f"Invalid connector metadata for {instance}: {connector}")

        return {
            "instance": instance,
            "kv_addr": "",
            "connector": connector,
        }
    finally:
        # This endpoint returns an advisory target and does not own the
        # subsequent request lifetime, so it must not retain JSQ reservations.
        request_batcher.scheduler_mgr.on_request_finished(request)
