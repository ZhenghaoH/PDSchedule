import random
from typing import List

from store.gateway.manager import GatewayStoreManager
from core.schemas.request import BaseRequest


class SyncBaseScheduler:
    """调度器实现"""
    def __init__(self, storage=None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = GatewayStoreManager()

    def random(self):
        if self.storage.active_prefill_objs:
            return random.choice(self.storage.active_prefill_objs)
        else:
            return ""

    def schedule(self, batch_requests: List[BaseRequest], strategy: str = "random", **kwargs) -> List[BaseRequest]:
        scheduled_requests = []
        for request in batch_requests:
            decode_proxy = request.Task.D_proxy_addr + ":" + str(request.Task.D_proxy_port)
            if strategy == "random":
                request.Task.prefill_instance = self.random()
            else:
                print("Strategy not found.")
                request.Task.prefill_instance = self.random()
            scheduled_requests.append(request)
        return scheduled_requests
