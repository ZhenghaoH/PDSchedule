# scheduler/manager.py

import asyncio
import logging
import time
from typing import List, Dict, Any, Tuple

from core.schemas.request import BaseRequest
from scheduler.gateway.prefill.slo_aware import SLOAwarePrefillScheduler
from scheduler.gateway.prefill.async_xpyd import AsyncBaseScheduler as BasicScheduler
from scheduler.gateway.prefill.exhaustive import ExhaustiveOracleScheduler
# from scheduler.gateway.prefill.slo_sa import SLOAwareSAScheduler
# from scheduler.gateway.prefill.goodput_pso import GoodputPSOScheduler


class SchedulerManager:
    def __init__(self, strategy: str = "pso"):
        self.strategy = strategy
        self.pso_scheduler = SLOAwarePrefillScheduler()
        self.basic_scheduler = BasicScheduler()
        self.oracle_scheduler = ExhaustiveOracleScheduler()
        # self.slo_sa_scheduler = SLOAwareSAScheduler()
        # self.goodput_pso_scheduler = GoodputPSOScheduler()

    async def schedule_batch(self, queue: List[BaseRequest], last_plan: List[BaseRequest] = None) -> Tuple[List[BaseRequest], str]:
        """
        返回: (List[BaseRequest], dispatch_policy)
        dispatch_policy 选项: 'IMMEDIATE', 'IDLE_WAIT', 'PERIODIC'
        """
        if not queue:
            return [], "IMMEDIATE"

        # ================= [策略：轮询 (合并了直接转发)] =================
        if self.strategy == "round_robin":
            for req in queue:
                req.Task.batch_order = 1
                req.Task.prefill_instance = self.basic_scheduler.schedule(req, tag="prefill")
            # 轮询模式下，要求立即分发
            return queue, "IMMEDIATE"

        # ================= [策略：PSO 优化] =================
        elif self.strategy == "pso":
            scheduled = await self.pso_scheduler.schedule(queue, last_plan=last_plan)
            scheduled = await self.pso_scheduler.schedule(queue)
            # scheduled = await self.goodput_pso_scheduler.schedule(queue, last_plan=last_plan)
            # PSO 模式下，通常使用等空闲或周期分发（此处示例使用 PERIODIC）
            return scheduled, "PERIODIC"

        # ================= [策略：穷举优化] =================
        elif self.strategy == "exhaustive":
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
        elif self.strategy == "slo_sa":
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