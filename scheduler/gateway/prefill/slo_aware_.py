import time
import asyncio
from dataclasses import dataclass, field
from typing import List, Set, Optional, Dict
import aiohttp

from store.gateway.instance import Instance
from store.gateway.manager import GatewayStoreManager
from core.schemas.request import BaseRequest
from config.models.default import ModelConfig
from config.network.default import NetworkConfig


@dataclass
class SchedulableRequest:
    """
    Prefill调度过程中需要用到的请求包装类
    """
    req: BaseRequest

    # 调度过程相关属性 (Calculated during scheduling preparation)
    solo_time: float = field(init=False, default=0.0)
    slack: float = field(init=False, default=0.0)
    deadline: float = field(init=False, default=0.0)

    # 调度结果相关属性 (Filled during scheduling decision)
    predicted_start_time: float = field(init=False, default=0.0)
    predicted_finish_time: float = field(init=False, default=0.0)

    @property
    def prompt_length(self):
        return self.req.Prompt.token_length

    @property
    def request_id(self):
        return self.req.Request_ID

    @property
    def kv_cache_compression_factor(self) -> float:
        return self.req.Service.Compress_factor

    def prefill_instance(self) -> str:
        return self.req.Task.prefill_instance

    def batch_order(self) -> int:
        return self.req.Task.batch_order

    @property
    def ttft_slo(self) -> float:
        return self.req.Service.SLO_TTFT

    # 为了让 SchedulableRequest 可以放入 set() 中进行去重和集合运算，实现 hash 和 eq
    def __hash__(self):
        return hash(self.req.Request_ID)

    def __eq__(self, other):
        if not isinstance(other, SchedulableRequest):
            return NotImplemented
        return self.req.Request_ID == other.req.Request_ID


# --- 2. Helper Functions ---

def calculate_kv_cache_size(request: SchedulableRequest, config: ModelConfig) -> float:
    # 注意：这里去掉了 get_...() 的括号，因为使用了 @property
    return 2 * request.prompt_length \
        * config.model_dimension \
        * config.model_layers \
        * config.model_precision_bytes \
        * request.kv_cache_compression_factor


def predict_transfer_time(kv_cache_size: float, config: NetworkConfig) -> float:
    return (kv_cache_size / config.bottleneck_bandwidth) + (
                config.communication_overhead_factor * config.round_trip_time)


async def get_prediction_from_server(batch_size: int, prompt_length: int) -> Optional[float]:
    if batch_size == 0:
        return 0.0
    return 0.05 + (batch_size * 0.2) + (prompt_length * 0.0001)


class SLOAwarePrefillScheduler:
    def __init__(self, storage=None):
        # TODO：模拟的用于参考的数据，以后放入 global_storage
        if storage is None:
            self.storage = GatewayStoreManager()
        else:
            self.storage = storage
        instances = self.storage.instance_table
        self.instances: List[Instance] = instances.values()
        self.model_config = ModelConfig()
        self.network_config = NetworkConfig()
        self.t = 0.0

    async def schedule_batches(self, queue: List[BaseRequest]):
        # TODO：模拟的用于参考的数据，以后从global_storage读取
        t_now = self.t
        model_config = self.model_config
        network_config = self.network_config
        instances = self.instances

        if not queue:
            return []
        # 1. 将原始请求包装为可调度请求，用于计算 Slack
        schedulable_queue: List[SchedulableRequest] = [
            SchedulableRequest(req=req) for req in queue
        ]

        # --- Step 1: 全局紧迫度计算和排序 ---

        solo_tasks = [get_prediction_from_server(1, r.prompt_length) for r in schedulable_queue]
        compute_times = await asyncio.gather(*solo_tasks)

        if any(t is None for t in compute_times):
            print("错误: 一个或多个 solo-time 预测失败。中止调度。")
            return []

        for i, req in enumerate(schedulable_queue):
            kv_size = calculate_kv_cache_size(req, model_config)
            transfer_time = predict_transfer_time(kv_size, network_config)

            req.solo_time = compute_times[i] + transfer_time
            req.slack = req.ttft_slo - req.solo_time
            req.deadline = t_now + req.ttft_slo

        # 按紧迫度升序排序
        sorted_queue_by_urgency = sorted(schedulable_queue, key=lambda r: r.slack)

        # --- Step 2: 状态感知的迭代批次构建 ---

        final_scheduled_requests: List[BaseRequest] = []
        scheduled_request_ids = set()

        # 记录每个实例的 batch 计数
        instance_batch_counts: Dict[int, int] = {i: 0 for i in range(len(instances))}

        print("\n--- Integrated Scheduling Started ---")
        print(f"初始实例可用时间: {[i.predicted_available_time for i in instances]}")

        while len(scheduled_request_ids) < len(queue):
            # 找到最早可用的实例
            earlisest_instance_index, earlisest_available_instance = min(enumerate(instances),
                                                                         key=lambda x: x[1].predicted_available_time)

            effective_start_time = max(t_now, earlisest_available_instance.predicted_available_time)

            unscheduled_requests = [r for r in sorted_queue_by_urgency if r.request_id not in scheduled_request_ids]

            if not unscheduled_requests:
                break

            print(
                f"\n- 尝试为实例 {earlisest_available_instance.instance_ip} (最早可用 @ {earlisest_available_instance.predicted_available_time:.4f}s) 构建批次...")

            best_batch_for_this_instance: Set[SchedulableRequest] = set()

            # 贪心构建批次
            for request_to_try in unscheduled_requests:
                temp_batch = best_batch_for_this_instance.union({request_to_try})

                max_len = max(r.prompt_length for r in temp_batch)
                t_predict = await get_prediction_from_server(len(temp_batch), max_len)

                if t_predict is None:
                    continue

                bottleneck_deadline = min(r.deadline for r in temp_batch)

                if effective_start_time + t_predict <= bottleneck_deadline:
                    best_batch_for_this_instance = temp_batch
                else:
                    break

                    # 强制调度逻辑 (Best-effort)
            if not best_batch_for_this_instance:
                forced_request = unscheduled_requests[0]
                best_batch_for_this_instance = {forced_request}
                print(f"  ! 警告: 剩余请求无法满足SLO。强制调度请求 {forced_request.request_id}。")

            # 确认批次
            max_len_final = max(r.prompt_length for r in best_batch_for_this_instance)
            processing_time = await get_prediction_from_server(len(best_batch_for_this_instance),
                                                               max_len_final)
            finish_time = effective_start_time + processing_time

            # 1. 增加该实例的 batch 计数
            instance_batch_counts[earlisest_instance_index] += 1
            current_batch_order = instance_batch_counts[earlisest_instance_index]

            # 2. 回写信息到 BaseRequest 并收集
            for wrapper in best_batch_for_this_instance:
                wrapper.req.Task.prefill_instance = earlisest_available_instance.instance_ip
                wrapper.req.Task.batch_order = current_batch_order

                # 将原始请求加入结果列表
                final_scheduled_requests.append(wrapper.req)
                scheduled_request_ids.add(wrapper.request_id)

            # 3. 更新实例状态
            instances[earlisest_instance_index].predicted_available_time = finish_time

            print(
                f"--> 确认批次 (包含 {len(best_batch_for_this_instance)} 个请求) -> 实例 {earlisest_available_instance.instance_ip}. "
                f"Batch Order: {current_batch_order}. 完成时间: {finish_time:.4f}s")

        return final_scheduled_requests
