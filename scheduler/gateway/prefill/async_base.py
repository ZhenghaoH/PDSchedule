from store.gateway.manager import GatewayStoreManager
from core.schemas.request import BaseRequest


class AsyncBaseScheduler:
    """调度器实现"""
    def __init__(self, storage=None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = GatewayStoreManager()

    def lowest_prompt_throughput(self) -> str:
        prefill_instances = self.storage.active_prefill_objs
        if not prefill_instances:
            return ""

        instances = self.storage.instance_table
        return min(
            prefill_instances,
            key=lambda instance_ip: (
                instances.get(instance_ip).prompt_throughput if instances.get(instance_ip) else 0.0,
                instance_ip,
            ),
        )

    def schedule(self, request: BaseRequest, tag: str = "prefill", strategy: str = "lowest_prompt_throughput", **kwargs) -> str:
        if tag == "prefill":
            if strategy == "lowest_prompt_throughput":
                return self.lowest_prompt_throughput()
            else:
                print("Strategy not found.")
                return self.lowest_prompt_throughput()
        elif tag == "decode":
            return request.Task.D_proxy_addr + ":" + str(request.Task.D_proxy_port)
        else:
            # TODO: 计划外调度处理
            print("Request_id or Tag not found.")
            return ""
