import random
from store.gateway.manager import GatewayStoreManager
from core.schemas.request import BaseRequest


class AsyncBaseScheduler:
    """异步调度器实现"""
    def __init__(self, storage=None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = GatewayStoreManager()

    def random_schedule(self):
        if self.storage.decode_objs:
            return random.choice(self.storage.decode_objs)
        else:
            return ""

    def schedule(self, request: BaseRequest, strategy: str = "random") -> str:
        """异步调度接口"""
        if strategy == "random":
            return self.random_schedule()
        else:
            return self.random_schedule()

