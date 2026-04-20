from store.gateway.manager import GatewayStoreManager
from core.schemas.request import BaseRequest
from typing import List


class SyncBaseScheduler:
    """同步调度器实现"""
    def __init__(self, storage=None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = GatewayStoreManager()

    def schedule(self, batch_requests: List[BaseRequest]) -> List[BaseRequest]:
        """同步调度接口"""
        scheduled_requests = []
        for request in batch_requests:
            prefill_instance = request.Task.prefill_instance
            link_table = self.storage.link_table
            for k, v in link_table.items():
                if prefill_instance == v.prefill_instance:
                    request.Task.decode_instance = k
                    scheduled_requests.append(request)
        return scheduled_requests
