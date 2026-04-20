import random
from typing import List, Tuple, Callable

from store.gateway.manager import GatewayStoreManager
from core.schemas.request import BaseRequest


class AsyncBaseScheduler:
    """调度器实现"""
    def __init__(self, storage=None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = GatewayStoreManager()

    def random(self):
        if self.storage.prefill_objs:
            return random.choice(self.storage.prefill_objs)
        else:
            return ""

    def schedule(self, request: BaseRequest, tag: str = "prefill", strategy: str = "random", **kwargs) -> str:
        if tag == "prefill":
            if strategy == "random":
                return self.random()
            else:
                print("Strategy not found.")
                return self.random()
        elif tag == "decode":
            return request.Task.D_proxy_addr + ":" + str(request.Task.D_proxy_port)
        else:
            # TODO: 计划外调度处理
            print("Request_id or Tag not found.")
            return ""
