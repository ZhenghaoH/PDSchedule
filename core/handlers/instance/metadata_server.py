from enum import Enum
from time import sleep
from contextlib import asynccontextmanager
import threading
import asyncio
from fastapi import FastAPI, Request, Response, HTTPException, status, APIRouter
from fastapi.responses import PlainTextResponse
import uvicorn
from typing import Optional

store = dict()
lock = asyncio.Lock()


class KVPoll(Enum):
    """状态枚举，表示服务器操作的状态"""
    Failed = 0
    Bootstrapping = 1
    WaitingForInput = 2
    Transferring = 3
    Success = 4


class KVBootstrapServer:
    """
    一个基于 FastAPI 的轻量级键值存储服务，支持 IPv4 和 IPv6。
    可通过构造函数自定义绑定地址和端口。
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        """
        初始化服务器实例
        - host: 绑定地址，支持 IPv4 (如 0.0.0.0) 和 IPv6 (::)
        - port: 绑定端口
        """
        self.host = host
        self.port = port
        # self.store = dict()  # 存储键值对
        # self.lock = asyncio.Lock()  # 异步锁，用于线程安全
        self._setup_app()

    def _setup_app(self):
        """设置 FastAPI 应用"""
        self.app = FastAPI()
        self._setup_routes()

    def _setup_routes(self):
        """设置路由"""
        router = APIRouter()
        router.get("/metadata")(self.handle_metadata)
        router.put("/metadata")(self.handle_metadata)
        router.delete("/metadata")(self.handle_metadata)
        self.app.include_router(router)

    def run(self):
        """在后台线程中启动服务器"""
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()

    async def handle_metadata(self, request: Request, key: Optional[str] = None):
        """处理元数据请求（GET/PUT/DELETE）"""
        # FastAPI 会自动解析查询参数，这里我们直接从请求中获取
        if key is None:
            # 如果没有提供 key 参数，尝试从查询字符串中获取
            key = request.query_params.get("key", "")

        # 获取客户端信息
        client_ip = request.client.host if request.client else "Unknown"
        client_port = request.client.port if request.client else None
        sender = f"{client_ip}:{client_port}" if client_ip and client_port else "Unknown"
        print("SENDER:", sender)

        if request.method == "GET":
            print("METHOD: GET, key:", key)
            return await self._handle_get(key)
        elif request.method == "PUT":
            print("METHOD: PUT, key:", key)
            return await self._handle_put(key, request)
        elif request.method == "DELETE":
            print("METHOD: DELETE, key:", key)
            return await self._handle_delete(key)
        else:
            return Response(content="Method Not Allowed", status_code=405, media_type="application/json")

    async def _handle_get(self, key):
        """处理 GET 请求，返回指定 key 的值"""
        global lock
        global store
        async with lock:
            value = store.get(key)
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="metadata not found"
            )
        return Response(
            content=value,
            media_type="application/json",
            status_code=status.HTTP_200_OK
        )

    async def _handle_put(self, key, request: Request):
        """处理 PUT 请求，存储 key-value"""
        global lock
        global store
        data = await request.body()
        async with lock:
            store[key] = data
            print("    put data:", data.decode("utf-8"))
        return Response(
            content="metadata updated",
            status_code=status.HTTP_200_OK
        )

    async def _handle_delete(self, key):
        """处理 DELETE 请求，删除指定 key"""
        global lock
        global store
        async with lock:
            if key not in store:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="metadata not found"
                )
            del store[key]
        return PlainTextResponse(
            content="metadata deleted",
            status_code=status.HTTP_200_OK
        )

    def _run_server(self):
        """启动 uvicorn 服务器（异步线程）"""
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="uvloop"
        )
        self.server = uvicorn.Server(config)

        try:
            self.server.run()
        except Exception as e:
            print(f"Server error: {str(e)}")

    def close(self):
        """关闭服务器"""
        if hasattr(self, 'server') and self.server:
            self.server.should_exit = True
            print("Stopping server...")
        if hasattr(self, 'thread') and self.thread.is_alive():
            self.thread.join(timeout=2)
            print("Server thread stopped")

    def poll(self) -> KVPoll:
        """检查服务器状态"""
        # 这里可以根据实际需求实现状态检查逻辑
        if hasattr(self, 'thread') and self.thread.is_alive():
            return KVPoll.Success
        return KVPoll.Failed


if __name__ == '__main__':
    # 示例：启用 IPv6 支持，绑定地址为 ::，端口 8080
    # 要改回 IPv4，可将 host 参数设为 "0.0.0.0",ipv6:"2004:1::167"
    server = KVBootstrapServer(host="172.18.0.169", port=8080)
    server.run()
    try:
        threading.Event().wait()  # 保持主线程运行
    except KeyboardInterrupt:
        server.close()
