# scheduler/manager.py

from typing import List, Dict, Any, Tuple

from core.schemas.request import BaseRequest
from scheduler.gateway.prefill.async_base import AsyncBaseScheduler
from scheduler.gateway.prefill.slo_aware_prefill import SLOAwarePrefillScheduler


class SchedulerManager:
    def __init__(self, strategy: str = "pso_new"):
        self.strategy = strategy
        self.pso_new_scheduler = SLOAwarePrefillScheduler()
        self.basic_scheduler = AsyncBaseScheduler()

    def _candidate_snapshot(self) -> List[Dict[str, Any]]:
        storage = self.basic_scheduler.storage
        instances = storage.instance_table
        snapshot = []
        for instance_ip in storage.active_prefill_objs:
            instance = instances.get(instance_ip)
            snapshot.append(
                {
                    "instance": instance_ip,
                    "prompt_throughput": float(
                        getattr(instance, "prompt_throughput", 0.0) or 0.0
                    ),
                    "running_reqs": int(
                        getattr(instance, "running_reqs", 0) or 0
                    ),
                    "waiting_reqs": int(
                        getattr(instance, "waiting_reqs", 0) or 0
                    ),
                    "unfinished_prompt_tokens": storage.get_distserve_effective_load(
                        "prefill", instance_ip
                    ),
                }
            )
        return snapshot

    @staticmethod
    def _selection_reason(strategy: str) -> str:
        return {
            "round_robin": "next_round_robin_instance_load_ignored",
            "jsq": "minimum_unfinished_prompt_tokens",
            "pso_new": "pso_new_planned_assignment",
        }.get(strategy, "strategy_assignment")

    def _annotate_selection(
        self,
        requests: List[BaseRequest],
        strategy: str,
        candidates: List[Dict[str, Any]],
    ) -> None:
        reason = self._selection_reason(strategy)
        for request in requests:
            metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
            metadata["gateway_prefill_selection"] = {
                "strategy": strategy,
                "selected_instance": getattr(request.Task, "prefill_instance", ""),
                "selection_source": "prefill_gateway_scheduler",
                "reason": reason,
                "candidates": candidates,
                "planned_batch_order": getattr(request.Task, "batch_order", None),
                "planned_start_s": getattr(request.Task, "slo_planned_start", None),
                "planned_finish_s": getattr(request.Task, "slo_planned_finish", None),
            }
            request.Task.scheduler_metadata = metadata

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

        # ================= [策略：轮询 (合并了直接转发)] =================
        if selected_strategy == "round_robin":
            candidates = self._candidate_snapshot()
            for req in queue:
                req.Task.batch_order = 1
                req.Task.prefill_instance = self.basic_scheduler.storage.next_prefill_round_robin()
            self._annotate_selection(queue, selected_strategy, candidates)
            # 轮询模式下，要求立即分发
            return queue, "IMMEDIATE"

        if selected_strategy == "jsq":
            scheduled = []
            for position, request in enumerate(queue):
                candidates = self._candidate_snapshot()
                # Reserve each request independently. The reservation is visible
                # to the next iteration immediately, so this is request-level JSQ
                # rather than assigning one gateway batch to a single instance.
                selected_instance = self.basic_scheduler.reserve_jsq_batch([request])
                if not selected_instance:
                    continue
                request.Task.batch_order = 1
                request.Task.prefill_instance = selected_instance
                request.Task.slo_plan_position = position
                try:
                    prompt_tokens = int(
                        getattr(request.Prompt, "token_length", 0) or 0
                    )
                except (TypeError, ValueError):
                    prompt_tokens = 0
                prompt_tokens = max(1, prompt_tokens)
                metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
                metadata["jsq"] = {
                    "role": "prefill",
                    "dispatch_mode": "direct_jsq",
                    "load_metric": "unfinished_prompt_tokens",
                    "request_id": str(request.Request_ID),
                    "prompt_tokens": prompt_tokens,
                    "selected_instance": selected_instance,
                    "selected_unfinished_tokens": (
                        self.basic_scheduler.storage.get_distserve_effective_load(
                            "prefill", selected_instance
                        )
                    ),
                }
                request.Task.scheduler_metadata = metadata
                self._annotate_selection([request], selected_strategy, candidates)
                scheduled.append(request)
            return scheduled, "IMMEDIATE"

        if selected_strategy == "pso_new":
            scheduled = await self.pso_new_scheduler.schedule(
                queue,
                last_plan=last_plan,
                inflight_prefill=inflight_prefill,
            )
            self._annotate_selection(scheduled, selected_strategy, self._candidate_snapshot())
            return scheduled, "FULL_OR_IDLE_WAIT"

        raise ValueError(f"Unsupported Prefill scheduler strategy: {selected_strategy}")
