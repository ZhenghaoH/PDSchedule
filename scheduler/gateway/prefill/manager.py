# scheduler/manager.py

import asyncio
import logging
import time
from typing import List, Dict, Any, Tuple

from core.schemas.request import BaseRequest
from scheduler.gateway.prefill.slo_aware import SLOAwarePrefillScheduler
from scheduler.gateway.prefill.slo_aware_new import SLOAwareNewPrefillScheduler
from scheduler.gateway.prefill.token_goodput_order import TokenGoodputOrderScheduler
from scheduler.gateway.prefill.async_base import AsyncBaseScheduler
from scheduler.gateway.prefill.exhaustive import ExhaustiveOracleScheduler
# from scheduler.gateway.prefill.slo_sa import SLOAwareSAScheduler
# from scheduler.gateway.prefill.goodput_pso import GoodputPSOScheduler


class SchedulerManager:
    def __init__(self, strategy: str = "pso"):
        self.strategy = strategy
        self.pso_scheduler = SLOAwarePrefillScheduler()
        self.pso_new_scheduler = SLOAwareNewPrefillScheduler()
        self.token_goodput_order_scheduler = TokenGoodputOrderScheduler()
        self.basic_scheduler = AsyncBaseScheduler()
        self.oracle_scheduler = ExhaustiveOracleScheduler()
        # self.slo_sa_scheduler = SLOAwareSAScheduler()
        # self.goodput_pso_scheduler = GoodputPSOScheduler()

    async def schedule_batch(
            self,
            queue: List[BaseRequest],
            last_plan: List[BaseRequest] = None,
            inflight_prefill: List[Any] = None,
            strategy: str = None,
    ) -> Tuple[List[BaseRequest], str]:
        """
        返回: (List[BaseRequest], dispatch_policy)
        dispatch_policy 选项: 'IMMEDIATE', 'IDLE_WAIT', 'PERIODIC'
        """
        if not queue:
            return [], "IMMEDIATE"

        selected_strategy = strategy or self.strategy

        if selected_strategy == "least_load":
            for req in queue:
                req.Task.batch_order = 1
                req.Task.prefill_instance = self.basic_scheduler.lowest_prompt_throughput()
            return queue, "IMMEDIATE"

        # ================= [策略：轮询 (合并了直接转发)] =================
        if selected_strategy == "round_robin":
            for req in queue:
                req.Task.batch_order = 1
                req.Task.prefill_instance = self.basic_scheduler.storage.next_prefill_round_robin()
            # 轮询模式下，要求立即分发
            return queue, "IMMEDIATE"

        if selected_strategy == "distserve":
            fcfs_queue = sorted(
                queue,
                key=lambda request: float(
                    getattr(request.Service, "arrival_time", 0.0) or 0.0
                ),
            )
            token_limit = max(
                1,
                int(getattr(
                    self.basic_scheduler.storage,
                    "DIST_SERVE_PREFILL_BATCH_TOKENS",
                    2048,
                )),
            )
            batch = []
            batch_tokens = 0
            for request in fcfs_queue:
                request_tokens = max(
                    0,
                    int(getattr(request.Prompt, "token_length", 0) or 0),
                )
                if batch and batch_tokens + request_tokens > token_limit:
                    break
                batch.append(request)
                batch_tokens += request_tokens
                if request_tokens >= token_limit or batch_tokens >= token_limit:
                    break

            selected_instance = self.basic_scheduler.reserve_distserve_batch(batch)
            if not selected_instance:
                return [], "IMMEDIATE"

            request_ids = [str(request.Request_ID) for request in batch]
            for position, request in enumerate(batch):
                request.Task.batch_order = 1
                request.Task.prefill_instance = selected_instance
                request.Task.slo_plan_position = position
                metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
                metadata["distserve"] = {
                    "role": "prefill",
                    "lm_tokens": token_limit,
                    "batch_tokens": batch_tokens,
                    "batch_request_ids": request_ids,
                    "selected_instance": selected_instance,
                }
                request.Task.scheduler_metadata = metadata
            return batch, "IMMEDIATE"

        # ================= [策略：PSO 优化] =================
        elif selected_strategy == "pso":
            scheduled = await self.pso_scheduler.schedule(
                queue,
                last_plan=last_plan,
            )
            # scheduled = await self.pso_scheduler.schedule(queue)
            # scheduled = await self.goodput_pso_scheduler.schedule(queue, last_plan=last_plan)
            # PSO 模式下等待实例空闲后再分发，避免周期性强制转发打破已规划批次。
            return scheduled, "PERIODIC"

        # ================= [策略：穷举优化] =================
        elif selected_strategy == "token_goodput_order":
            scheduled = await self.token_goodput_order_scheduler.schedule(queue)
            return scheduled, "PERIODIC"

        elif selected_strategy == "pso_new":
            scheduled = await self.pso_new_scheduler.schedule(
                queue,
                last_plan=last_plan,
                inflight_prefill=inflight_prefill,
            )
            return scheduled, "FULL_OR_IDLE_WAIT"

        elif selected_strategy == "exhaustive":
            result = await self.oracle_scheduler.find_optimal(queue)
            if not result: return queue, "IMMEDIATE"
            
            best_cost, best_plan = result
            final_reqs = []
            instance_heap = self.oracle_scheduler._get_instance_heap()
            target_ip = instance_heap[0][1] if instance_heap else "unknown"
            
            for order, (batch, f_time, dur) in enumerate(best_plan, start=1):
                for s_req in batch:
                    r = s_req.req
                    r.Task.batch_order = order
                    r.Task.prefill_instance = target_ip
                    final_reqs.append(r)
            # 穷举结果通常严格按顺序执行
            return final_reqs, "IDLE_WAIT"

        # ================= [策略：SLO感知的模拟退火优化] =================
        elif selected_strategy == "slo_sa":
            scheduled = await self.slo_sa_scheduler.schedule(queue, last_plan=last_plan)
            # 设置预填充实例，使用轮询方式获取可用实例
            for req in scheduled:
                req.Task.prefill_instance = self.basic_scheduler.schedule(req, tag="prefill")
            # SLO_SA 模式下，通常使用周期分发
            print(f"SLO_SA scheduled requests:")
            for req in scheduled:
                print(f"  Request {req.Request_ID} -> batch_order: {req.Task.batch_order}")
            return scheduled, "IDLE_WAIT"

        else:
            # 默认回退
            return queue, "IMMEDIATE"
