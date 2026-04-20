from store.gateway.manager import GatewayStoreManager
from core.schemas.request import BaseRequest
from typing import List
import random


class SyncBaseScheduler:
    """同步调度器实现"""
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

    def schedule(self, batch_requests: List[BaseRequest], strategy: str = "random") -> List[BaseRequest]:
        """同步调度接口"""
        scheduled_requests = []
        for request in batch_requests:
            if strategy == "random":
                decode_instance = self.random_schedule()
            else:
                decode_instance = self.random_schedule()
            request.Task.decode_instance = decode_instance
            scheduled_requests.append(request)
        return scheduled_requests
