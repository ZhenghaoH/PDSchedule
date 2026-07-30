from fastapi import HTTPException, Request

from connectors.manager import get_connector_manager
from store.gateway.manager import GatewayStoreManager


async def connector_capabilities():
    storage = GatewayStoreManager()
    descriptor = get_connector_manager().validate(storage.get_connector())
    adapter = get_connector_manager().registry.get(descriptor.name)
    return {
        "connector": descriptor.to_dict(),
        "mode": adapter.mode.value,
        "supported": list(get_connector_manager().registry.names),
    }


async def create_connector_session(raw_request: Request):
    try:
        body = await raw_request.json()
        session = get_connector_manager().create_session(
            prefill=body.get("prefill_endpoint") or {},
            decode=body.get("decode_endpoint") or {},
            descriptor=body.get("connector"),
            session_id=body.get("session_id"),
        )
        return session.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def get_connector_session(session_id: str):
    session = get_connector_manager().get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Connector session not found")
    return session.to_dict()


async def close_connector_session(session_id: str):
    if not get_connector_manager().close_session(session_id):
        raise HTTPException(status_code=404, detail="Connector session not found")
    return {"session_id": session_id, "state": "closed"}
