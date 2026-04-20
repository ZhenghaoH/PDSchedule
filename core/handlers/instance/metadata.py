from enum import Enum
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response
import asyncio
from typing import Optional

app = FastAPI()

# 存储键值对
store = dict()
# 异步锁，用于线程安全
lock = asyncio.Lock()


class KVPoll(Enum):
    """状态枚举，表示服务器操作的状态"""
    Failed = 0
    Bootstrapping = 1
    WaitingForInput = 2
    Transferring = 3
    Success = 4


async def handle_metadata(request: Request, key: Optional[str] = None):
    """处理元数据请求（GET/PUT/DELETE）"""
    # FastAPI 会自动解析查询参数，这里我们直接从请求中获取
    if key is None:
        # 如果没有提供 key 参数，尝试从查询字符串中获取
        key = request.query_params.get("key", "")

    # 获取客户端 IP 和端口
    client = request.client
    sender = "Unknown"
    if client:
        client_ip, client_port = client
        sender = f"{client_ip}:{client_port}"

    print("SENDER:", sender)

    if request.method == "GET":
        print("METHOD: GET, key:", key)
        return await _handle_get(key)
    elif request.method == "PUT":
        print("METHOD: PUT, key:", key)
        return await _handle_put(key, request)
    elif request.method == "DELETE":
        print("METHOD: DELETE, key:", key)
        return await _handle_delete(key)
    else:
        return Response(content="Method Not Allowed", status_code=405, media_type="application/json")


async def _handle_get(key: str):
    """处理 GET 请求，返回指定 key 的值"""
    global lock
    global store
    async with lock:
        value = store.get(key)
    if value is None:
        return Response(content="metadata not found", status_code=404, media_type="application/json")
    return Response(content=value, status_code=200, media_type="application/json")


async def _handle_put(key: str, request: Request):
    """处理 PUT 请求，存储 key-value"""
    global lock
    global store
    data = await request.body()
    async with lock:
        store[key] = data
        print("    put data:", data.decode("utf-8"))
    return Response(content="metadata updated", status_code=200, media_type="application/json")


async def _handle_delete(key: str):
    """处理 DELETE 请求，删除指定 key"""
    global lock
    global store
    async with lock:
        if key not in store:
            return Response(content="metadata not found", status_code=404, media_type="application/json")
        del store[key]
    return Response(content="metadata deleted", status_code=200, media_type="application/json")
