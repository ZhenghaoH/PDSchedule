from store.gateway.manager import GatewayStoreManager
from core.schemas.request import BaseRequest


class AsyncBaseScheduler:
    """异步调度器实现"""
    def __init__(self, storage=None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = GatewayStoreManager()

    def schedule(self, request: BaseRequest) -> str:
        """异步调度接口"""
        prefill_instance = request.Task.prefill_instance
        link_table = self.storage.link_table
        for k, v in link_table.items():
            if prefill_instance == v.prefill_instance:
                return k
        print("Prefill instance not found.")
        return ""

