from fastapi import Response, Request, status
from fastapi.responses import JSONResponse

from store.instance.store import InstanceStore
from coord.instance.down_mooncake_v0 import close_pair, update_pair, close_peer
from coord.instance.up_store import close_instance_pair


async def handle_pd_pair(raw_request: Request):
    request_json = await raw_request.json()
    prefill_instance = request_json.get('prefill', "")
    decode_instance = request_json.get('decode', "")
    force = request_json.get('force', False)
    if not (prefill_instance and decode_instance):
        return JSONResponse({"message": "Missing Parameters"}, status_code=status.HTTP_400_BAD_REQUEST)
    storage = InstanceStore()
    rank = storage.rank
    # 不用MOCK的时候才发配对指令
    if (not storage.USE_MOCK) and storage.KV_CONNECTOR.lower() == "mooncake":
        if storage.peer:
            # 已有连接，先断开
            if not close_pair() or not close_peer():
                return JSONResponse({"message": "Pair Close Failed."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            storage.set_peer("")  # 重置
        if not update_pair(prefill_instance, decode_instance):
            return JSONResponse({"message": "Pair Update Failed."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    else:
        if storage.peer:
            if not close_peer():
                return JSONResponse({"message": "Pair Close Failed."},
                                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if rank == 0:  # 自己是prefill，对端是decode
        storage.set_peer(decode_instance)
    else:  # 自己是decode，对端是prefill
        storage.set_peer(prefill_instance)
    return JSONResponse({"message": "OK"}, status_code=status.HTTP_200_OK)


async def handle_pd_close(raw_request: Request):
    request_json = await raw_request.json()
    peer = request_json.get('peer', "")
    if not peer:
        return JSONResponse({"message": "Missing Parameters"}, status_code=status.HTTP_400_BAD_REQUEST)
    storage = InstanceStore()
    if not peer == storage.peer:
        return JSONResponse({"message": "Peer Not Matched."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,)
    if (not storage.USE_MOCK) and storage.KV_CONNECTOR.lower() == "mooncake":
        if not close_pair():
            return JSONResponse({"message": "Pair Close Failed."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        storage.set_peer("")  # 重置
    close_instance_pair()
    return JSONResponse({"message": "OK"}, status_code=status.HTTP_200_OK)
