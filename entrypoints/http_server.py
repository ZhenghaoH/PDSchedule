from enum import Enum
from time import sleep
from aiohttp import web
import threading
import asyncio
from pathlib import Path
import sys


class KVPoll(Enum):
    """状态枚举，表示服务器操作的状态"""
    Failed = 0
    Bootstrapping = 1
    WaitingForInput = 2
    Transferring = 3
    Success = 4


class KVBootstrapServer:
    """
    一个基于 aiohttp 的轻量级键值存储服务，支持 IPv4 和 IPv6。
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
        self.app = web.Application()
        self.store = dict()  # 存储键值对
        self.lock = asyncio.Lock()  # 异步锁，用于线程安全
        self._setup_routes()

    def run(self):
        """在后台线程中启动服务器"""
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        print(f"HTTP metadata server is running on {self.host} : {self.port}...")
        self.thread.start()

    def _setup_routes(self):
        """设置路由，所有请求都路由到 /metadata"""
        self.app.router.add_route('*', '/metadata', self._handle_metadata)

    async def _handle_metadata(self, request: web.Request):
        """处理元数据请求（GET/PUT/DELETE）"""
        key = request.query.get('key', '')  # 获取查询参数中的 key

        # 获取客户端 IP 和端口
        client_ip = request.remote  # 直接获取客户端 IP（支持 IPv6）
        transport = request.transport
        client_port = None
        if transport:
            peername = transport.get_extra_info('peername')
            if peername:
                # IPv6 的 peername 是 (host, port, flowinfo, scopeid)
                # IPv4 的 peername 是 (host, port)
                client_ip = peername[0]  # 提取 host
                client_port = peername[1]  # 提取 port

        # 构造发送者标识
        sender = f"{client_ip}:{client_port}" if client_ip and client_port else "Unknown"
        print("SENDER:", sender)

        if request.method == 'GET':
            print("METHOD: GET, key:", key)
            return await self._handle_get(key)
        elif request.method == 'PUT':
            print("METHOD: PUT, key:", key)
            return await self._handle_put(key, request)
        elif request.method == 'DELETE':
            print("METHOD: DELETE, key:", key)
            return await self._handle_delete(key)
        return web.Response(text='Method not allowed', status=405,
                            content_type='application/json')

    async def _handle_get(self, key):
        """处理 GET 请求，返回指定 key 的值"""
        async with self.lock:
            value = self.store.get(key)
        if value is None:
            return web.Response(text='metadata not found', status=404,
                                content_type='application/json')
        return web.Response(body=value, status=200,
                            content_type='application/json')

    async def _handle_put(self, key, request):
        """处理 PUT 请求，存储 key-value"""
        data = await request.read()
        async with self.lock:
            self.store[key] = data
            print("    put data:", data.decode("utf-8"))
        return web.Response(text='metadata updated', status=200,
                            content_type='application/json')

    async def _handle_delete(self, key):
        """处理 DELETE 请求，删除指定 key"""
        async with self.lock:
            if key not in self.store:
                return web.Response(text='metadata not found', status=404,
                                    content_type='application/json')
            del self.store[key]
        return web.Response(text='metadata deleted', status=200,
                            content_type='application/json')

    def _run_server(self):
        """启动 aiohttp 服务器（异步线程）"""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._runner = web.AppRunner(self.app)
            self._loop.run_until_complete(self._runner.setup())

            # 使用传入的 host 和 port 启动服务
            site = web.TCPSite(self._runner, host=self.host, port=self.port)
            self._loop.run_until_complete(site.start())
            self._loop.run_forever()
        except Exception as e:
            print(f"Server error: {str(e)}")
        finally:
            # 清理资源
            self._loop.run_until_complete(self._runner.cleanup())
            self._loop.close()

    def close(self):
        """关闭服务器"""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            print("Stopping server loop...")
        if self.thread.is_alive():
            self.thread.join(timeout=2)
            print("Server thread stopped")

    def poll(self) -> KVPoll:
        ...
    # 实现 poll 方法（当前为占位符）


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))


if __name__ == '__main__':
    # 示例：启用 IPv6 支持，绑定地址为 ::，端口 8080
    # 要改回 IPv4，可将 host 参数设为 "0.0.0.0",ipv6:"2004:1::167"
    from config.ADDRESS_CONFIG import CONFIG_METADATA_IP, CONFIG_METADATA_PORT
    server = KVBootstrapServer(host=CONFIG_METADATA_IP, port=CONFIG_METADATA_PORT)
    server.run()
    try:
        threading.Event().wait()  # 保持主线程运行
    except KeyboardInterrupt:
        server.close()
