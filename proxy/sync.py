from __future__ import annotations
import logging
import asyncio
import time
import threading
from copy import deepcopy
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from scheduler.gateway.prefill.manager import SchedulerManager
from proxy.forward import forward_request_json
from proxy.forward import forward_request_trace as forward_request
from core.schemas.request import BaseRequest
from connectors.manager import get_connector_manager
from utils.gateway_observability import get_gateway_observer

from store.gateway.manager import GatewayStoreManager

# 导入配置参数
storage = GatewayStoreManager()
prefill_observer = get_gateway_observer("prefill")
PREFILL_WAITING_TOKEN_BUDGET = 4096


def _rid_key(rid: Any) -> str:
    return str(rid)


def _log_timeline_event(event: str, **payload: Any) -> None:
    return


def _bind_prefill_connector(request: BaseRequest, selected_prefill: str) -> None:
    meta = storage.get_connector_metadata(selected_prefill)
    if not meta:
        raise RuntimeError(
            f"Missing connector metadata for prefill instance {selected_prefill}"
        )
    descriptor = meta.get("descriptor") or {}
    if meta.get("role") != "prefill" or descriptor.get("name") != request.Task.pd_connector:
        raise RuntimeError(
            f"Invalid prefill connector metadata for {selected_prefill}: {meta}"
        )

    request.Task.connector_context["prefill_endpoint"] = meta


# --- Helper: Response Tracker ---
class IterableTracker:
    """
    迭代器跟踪器类，用于跟踪异步生成器的执行状态。
    作为中间层，透传数据同时记录 TTFT 和完成状态。
    """

    def __init__(self, original_iter: AsyncIterator):
        self.original_iter = original_iter

        # 使用 Event 代替 轮询锁，实现更高效的流结束通知
        self.completion_event = asyncio.Event()
        self._iter_finished = False  # 标记迭代是否自然结束或异常结束

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            result = await self.original_iter.__anext__()

            return result
        except StopAsyncIteration:
            self._mark_completed()
            raise
        except Exception:
            self._mark_completed()
            raise

    def _mark_completed(self):
        """标记流结束，唤醒等待的 task"""
        if not self._iter_finished:
            self._iter_finished = True
            self.completion_event.set()

    async def wait_for_completion(self):
        """供外部（Instance Execution Flow）等待流被完全消费"""
        await self.completion_event.wait()


# ==========================================
# 子类 1: 调度引擎 (Scheduling Engine)
# ==========================================
class SchedulingEngine:
    """调度引擎，负责请求的调度决策"""

    def __init__(self, batcher: RequestBatcher):
        self.b = batcher


# ==========================================
# 子类 1: 调度引擎 (Scheduling Engine)
# ==========================================
class SchedulingEngine:
    """调度引擎，负责请求的调度决策"""

    def __init__(self, batcher: RequestBatcher):
        self.b = batcher

    def _check_yield_condition(self) -> bool:
        """
        [辅助函数] 检查调度器是否应该"让路"给分发器。

        判定逻辑：
        1. 队列首部的请求已经就绪 (batch_order == 1)。
        2. 当前分发策略允许发送 (IMMEDIATE, 空闲的 IDLE_WAIT, 或触发间隔的 PERIODIC)。

        Returns:
            bool: True 表示应该跳过本次调度，直接触发分发; False 表示继续执行调度算法。
        """
        # 1. 基础数据完整性检查
        if not self.b.pending_queue:
            return False

        # 只有当队首已经是"已调度"状态时，才考虑让路
        first_req = self.b.pending_queue[0]
        if first_req.Task.batch_order != 1:
            return False

        # 2. 根据策略判定
        policy = self.b.dispatch_policy
        is_busy = self.b.instance_busy

        if policy == "IMMEDIATE":
            return True

        elif policy == "IDLE_WAIT":
            # 仅当实例空闲时才让路
            return not is_busy

        elif policy == "PERIODIC":
            de = self.b.dispatch_engine
            # 获取分发引擎的时间状态
            de = self.b.dispatch_engine
            now = time.time()
            interval_reached = (now - de.last_dispatch_time >= de.dispatch_interval)

            # PERIODIC 逻辑增强：
            # - 如果实例空闲：立即分发 (Yield)
            # - 如果实例忙碌但周期已到：强制分发 (Yield)
            if interval_reached:
                return True

        elif policy == "FULL_OR_IDLE_WAIT":
            decision = self.b.dispatch_engine._front_dispatch_decision(time.time())
            if decision.should_wait_for_instance:
                _log_timeline_event(
                    "SCHEDULER_KEEP_COLLECTING",
                    dispatch_policy=policy,
                    reason=decision.reason,
                    first_batch_ids=decision.front_ids,
                    first_batch_tokens=decision.front_tokens,
                    first_batch_total_tokens=decision.front_total_tokens,
                    first_batch_token_shortfall=decision.token_shortfall,
                    instance_busy=decision.instance_busy,
                    active_instance_flows=decision.active_instance_flows,
                    pending_queue_size=len(self.b.pending_queue),
                )
            return decision.should_dispatch_now

        return False

    async def run(self):
        """调度引擎主循环"""
        while True:
            # 等待触发：有新请求到达，或者被其他组件唤醒
            await self.b.schedule_event.wait()

            async with self.b.lock:
                self.b.schedule_event.clear()

                if not self.b.pending_queue:
                    continue

                # 优先分发检查
                if self._check_yield_condition():
                    logging.debug("⚡ [Scheduler] Yielding to Dispatcher (Requests ready & Policy met).")
                    first_order_reqs = []
                    for req in self.b.pending_queue:
                        if req.Task.batch_order != 1:
                            break
                        first_order_reqs.append(req)
                    first_tokens = [
                        getattr(req.Prompt, "token_length", 0)
                        for req in first_order_reqs
                    ]
                    _log_timeline_event(
                        "SCHEDULER_YIELD_READY",
                        dispatch_policy=self.b.dispatch_policy,
                        pending_queue_size=len(self.b.pending_queue),
                        first_batch_ids=[str(req.Request_ID) for req in first_order_reqs],
                        first_batch_tokens=first_tokens,
                        first_batch_total_tokens=sum(first_tokens),
                        first_batch_token_shortfall=max(0, PREFILL_WAITING_TOKEN_BUDGET - sum(first_tokens)),
                        first_batch_is_full=sum(first_tokens) >= PREFILL_WAITING_TOKEN_BUDGET,
                        first_batch_planned_full=any(
                            getattr(req.Task, "slo_batch_is_full", False)
                            for req in first_order_reqs
                        ),
                        first_schedule_id=getattr(
                            first_order_reqs[0].Task,
                            "slo_schedule_id",
                            None,
                        ) if first_order_reqs else None,
                    )
                    self.b.dispatch_event.set()
                    continue  # <--- 直接跳过本次调度，让分发器先工作

                # 使用深拷贝隔离调度计算；切换策略时旧计算结果不会污染真实队列。
                candidates = deepcopy(self.b.pending_queue)
                last_plan = getattr(self.b, "last_plan", None)
                schedule_strategy = self.b.scheduler_mgr.strategy
                schedule_version = self.b.strategy_version
                inflight_prefill = []
                for rid, req in self.b.inflight_prefill_reqs.items():
                    meta = dict(self.b.inflight_prefill_meta.get(rid, {}))
                    sent_time = self.b.inflight_prefill_sent_time.get(
                        rid,
                        getattr(req.Task, "slo_dispatch_time", time.time()),
                    )
                    meta.setdefault("sent_time", sent_time)
                    meta["req"] = req
                    inflight_prefill.append(meta)

            # 执行调度算法 (耗时操作，释放锁)
            try:
                # 注意：此处释放了锁，分发器可能会在计算期间运行
                # 但由于上面的"让路"逻辑，如果能分发，我们不会走到这里。
                scheduled_plan, new_policy = await self.b.scheduler_mgr.schedule_batch(
                    candidates,
                    last_plan=last_plan,
                    inflight_prefill=inflight_prefill,
                    strategy=schedule_strategy,
                )
                _log_timeline_event(
                    "SCHEDULE_RETURNED",
                    strategy=self.b.scheduler_mgr.strategy,
                    new_policy=new_policy,
                    candidate_count=len(candidates),
                    candidate_ids=[str(r.Request_ID) for r in candidates],
                    scheduled_count=len(scheduled_plan),
                    scheduled_ids=[str(r.Request_ID) for r in scheduled_plan],
                )
            except Exception as e:
                logging.error(f"Scheduler Error: {e}", exc_info=True)
                _log_timeline_event(
                    "SCHEDULE_ERROR",
                    strategy=self.b.scheduler_mgr.strategy,
                    error=repr(e),
                    candidate_count=len(candidates),
                    candidate_ids=[str(r.Request_ID) for r in candidates],
                )
                continue

            # 应用调度结果
            async with self.b.lock:
                if schedule_version != self.b.strategy_version:
                    self.b.scheduler_mgr.on_schedule_discarded(
                        scheduled_plan,
                        strategy=schedule_strategy,
                    )
                    _log_timeline_event(
                        "STALE_SCHEDULE_DISCARDED",
                        scheduled_strategy=schedule_strategy,
                        current_strategy=self.b.scheduler_mgr.strategy,
                        scheduled_version=schedule_version,
                        current_version=self.b.strategy_version,
                    )
                    self.b.schedule_event.set()
                    continue

                self.b.dispatch_policy = new_policy

                # 构建映射以便快速更新
                id_map = {_rid_key(r.Request_ID): r for r in scheduled_plan}

                # 更新队列中请求的调度信息
                # 再次检查：防止在计算期间请求被取消或被异常移除
                valid_reqs = []
                for r in self.b.pending_queue:
                    rid_key = _rid_key(r.Request_ID)
                    if rid_key in id_map:
                        obj = id_map[rid_key]
                        r.Task.batch_order = obj.Task.batch_order
                        r.Task.prefill_instance = obj.Task.prefill_instance
                        r.Task.scheduler_metadata = deepcopy(
                            getattr(obj.Task, "scheduler_metadata", {}) or {}
                        )
                        for attr in (
                            "slo_schedule_id",
                            "slo_planned_batch_order",
                            "slo_planned_step_order",
                            "slo_planned_start",
                            "slo_planned_finish",
                            "slo_planned_batch_finish",
                            "slo_planned_duration",
                            "slo_batch_tokens",
                            "slo_batch_is_full",
                            "slo_ready_to_dispatch",
                            "slo_plan_position",
                        ):
                            if hasattr(obj.Task, attr):
                                setattr(r.Task, attr, getattr(obj.Task, attr))
                        valid_reqs.append(r)

                for request in valid_reqs:
                    selection = dict(
                        getattr(request.Task, "scheduler_metadata", {}).get(
                            "gateway_prefill_selection", {}
                        )
                    )
                    prefill_observer.record_schedule(
                        request_id=str(request.Request_ID),
                        **selection,
                    )
                    logging.info(
                        "[Prefill Schedule] request=%s strategy=%s selected=%s reason=%s",
                        request.Request_ID,
                        selection.get("strategy"),
                        selection.get("selected_instance"),
                        selection.get("reason"),
                    )

                valid_request_ids = {_rid_key(request.Request_ID) for request in valid_reqs}
                for scheduled_request in scheduled_plan:
                    if _rid_key(scheduled_request.Request_ID) not in valid_request_ids:
                        self.b.scheduler_mgr.on_schedule_discarded(
                            [scheduled_request]
                        )

                # 排序：batch_order > 0 的优先，同批次按到达时间
                self.b.pending_queue.sort(key=lambda x: (
                    x.Task.batch_order if x.Task.batch_order > 0 else 9999,
                    getattr(x.Task, "slo_plan_position", 999999),
                    x.Service.arrival_time
                ))
                self.b.last_plan = scheduled_plan
                _log_timeline_event(
                    "SCHEDULE_APPLY_RESULT",
                    dispatch_policy=self.b.dispatch_policy,
                    pending_queue_size=len(self.b.pending_queue),
                    scheduled_ids=[str(r.Request_ID) for r in scheduled_plan],
                    applied_ids=[str(r.Request_ID) for r in valid_reqs],
                    applied_count=len(valid_reqs),
                    pending_ids=[str(r.Request_ID) for r in self.b.pending_queue],
                    pending_batch_orders=[
                        getattr(r.Task, "batch_order", None)
                        for r in self.b.pending_queue
                    ],
                )

                # 如果有请求就绪，唤醒分发器
                if self.b.pending_queue and self.b.pending_queue[0].Task.batch_order == 1:
                    first_order_reqs = []
                    for req in self.b.pending_queue:
                        if req.Task.batch_order != 1:
                            break
                        first_order_reqs.append(req)
                    first_tokens = [
                        getattr(req.Prompt, "token_length", 0)
                        for req in first_order_reqs
                    ]
                    _log_timeline_event(
                        "SCHEDULE_APPLIED",
                        dispatch_policy=self.b.dispatch_policy,
                        pending_queue_size=len(self.b.pending_queue),
                        first_batch_ids=[str(req.Request_ID) for req in first_order_reqs],
                        first_batch_tokens=first_tokens,
                        first_batch_total_tokens=sum(first_tokens),
                        first_batch_token_shortfall=max(0, PREFILL_WAITING_TOKEN_BUDGET - sum(first_tokens)),
                        first_batch_is_full=sum(first_tokens) >= PREFILL_WAITING_TOKEN_BUDGET,
                        first_batch_planned_full=any(
                            getattr(req.Task, "slo_batch_is_full", False)
                            for req in first_order_reqs
                        ),
                        first_schedule_id=getattr(
                            first_order_reqs[0].Task,
                            "slo_schedule_id",
                            None,
                        ) if first_order_reqs else None,
                    )
                    self.b.dispatch_event.set()


# ==========================================
# 子类 2: 分发引擎 (Dispatch Engine)
# ==========================================
class DispatchEngine:
    """
    分发引擎，负责将已调度的请求发送到相应实例。
    修复了 IDLE_WAIT 模式下“死锁”不发送后续请求的 Bug。
    """

    def __init__(self, batcher: RequestBatcher):
        self.b = batcher
        self.last_dispatch_time = time.time()
        self.dispatch_interval = 0.1

    def configure_from_storage(self):
        pass

    def _front_batch(self):
        if not self.b.pending_queue or self.b.pending_queue[0].Task.batch_order != 1:
            return []
        batch = []
        for req in self.b.pending_queue:
            if req.Task.batch_order != 1:
                break
            batch.append(req)
        return batch

    def _inflight_prefill_tokens(self) -> int:
        total = 0
        for req in self.b.inflight_prefill_reqs.values():
            total += max(0, int(getattr(req.Prompt, "token_length", 0) or 0))
        return total

    def _inflight_prefill_tokens_by_instance(self) -> Dict[str, int]:
        totals: Dict[str, int] = defaultdict(int)
        for req in self.b.inflight_prefill_reqs.values():
            ip = getattr(req.Task, "prefill_instance", "")
            totals[ip] += max(0, int(getattr(req.Prompt, "token_length", 0) or 0))
        return totals

    def _front_dispatch_decision(self, now: float):
        class _Decision:
            pass

        decision = _Decision()
        decision.should_dispatch_now = False
        decision.should_wait_for_instance = False
        decision.reason = "not_ready"
        decision.delay_sec = 0.0
        decision.front_ids = []
        decision.front_tokens = []
        decision.front_total_tokens = 0
        decision.dispatch_request_ids = []
        decision.dispatch_instance_ids = []
        decision.blocked_instance_ids = []
        decision.instance_details = {}
        decision.token_shortfall = PREFILL_WAITING_TOKEN_BUDGET
        decision.inflight_prefill_tokens = self._inflight_prefill_tokens()
        decision.waiting_token_budget = PREFILL_WAITING_TOKEN_BUDGET
        decision.ready = False
        decision.planned_full = False
        decision.is_full = False
        if self.b.instance_busy and self.b.active_instance_flows <= 0:
            _log_timeline_event(
                "STALE_INSTANCE_BUSY_RESET",
                reason="instance_busy_true_but_no_active_flows",
            )
            self.b.instance_busy = False
            self.b.active_instance_flows_by_ip.clear()

        decision.instance_busy = self.b.instance_busy
        decision.active_instance_flows = self.b.active_instance_flows

        batch = self._front_batch()
        if not batch:
            decision.reason = "empty_or_unscheduled"
            return decision

        inflight_by_instance = self._inflight_prefill_tokens_by_instance()
        grouped: Dict[str, List[BaseRequest]] = defaultdict(list)
        for req in batch:
            grouped[getattr(req.Task, "prefill_instance", "")].append(req)

        ready = all(getattr(req.Task, "slo_ready_to_dispatch", True) for req in batch)
        batch_tokens = sum(getattr(req.Prompt, "token_length", 0) for req in batch)
        planned_full = any(getattr(req.Task, "slo_batch_is_full", False) for req in batch)
        is_full = planned_full or batch_tokens >= PREFILL_WAITING_TOKEN_BUDGET
        inflight_tokens = self._inflight_prefill_tokens()
        decision.front_ids = [str(req.Request_ID) for req in batch]
        decision.front_tokens = [getattr(req.Prompt, "token_length", 0) for req in batch]
        decision.front_total_tokens = batch_tokens
        decision.token_shortfall = max(0, PREFILL_WAITING_TOKEN_BUDGET - batch_tokens)
        decision.inflight_prefill_tokens = inflight_tokens
        decision.ready = ready
        decision.planned_full = planned_full
        decision.is_full = is_full
        decision.instance_busy = self.b.instance_busy
        decision.active_instance_flows = self.b.active_instance_flows

        for ip, reqs in grouped.items():
            group_ready = all(getattr(req.Task, "slo_ready_to_dispatch", True) for req in reqs)
            group_tokens = sum(getattr(req.Prompt, "token_length", 0) for req in reqs)
            group_planned_full = any(getattr(req.Task, "slo_batch_is_full", False) for req in reqs)
            group_is_full = group_planned_full or group_tokens >= PREFILL_WAITING_TOKEN_BUDGET
            instance_flows = self.b.active_instance_flows_by_ip.get(ip, 0)
            instance_busy = instance_flows > 0
            instance_inflight_tokens = inflight_by_instance.get(ip, 0)
            can_dispatch = bool(
                group_ready
                and (
                    group_is_full
                    or not instance_busy
                    or instance_inflight_tokens < PREFILL_WAITING_TOKEN_BUDGET
                )
            )

            decision.instance_details[ip] = {
                "ready": group_ready,
                "tokens": group_tokens,
                "planned_full": group_planned_full,
                "is_full": group_is_full,
                "busy": instance_busy,
                "active_flows": instance_flows,
                "inflight_tokens": instance_inflight_tokens,
                "request_ids": [str(req.Request_ID) for req in reqs],
                "can_dispatch": can_dispatch,
            }

            if can_dispatch:
                decision.dispatch_instance_ids.append(ip)
                decision.dispatch_request_ids.extend(str(req.Request_ID) for req in reqs)
            else:
                decision.blocked_instance_ids.append(ip)

        if decision.dispatch_request_ids:
            decision.should_dispatch_now = True
            if decision.blocked_instance_ids:
                decision.reason = "partial_instance_dispatch"
            elif is_full:
                decision.reason = "full_batch_dispatch"
            elif not self.b.instance_busy:
                decision.reason = "idle_dispatch"
            else:
                decision.reason = "instance_watermark_dispatch"
            return decision

        if not ready:
            decision.reason = "not_ready_to_dispatch"
            return decision

        decision.should_wait_for_instance = True
        decision.reason = "all_instances_waiting"
        return decision

    def _compute_periodic_decision(self, now: float):
        class _FixedDecision:
            pass

        decision = _FixedDecision()
        decision.delay_sec = max(0.0, self.dispatch_interval - (now - self.last_dispatch_time))
        decision.should_dispatch_now = decision.delay_sec <= 0.0
        decision.reason = "fixed_interval"
        return decision

    async def run(self):
        while True:
            # -------------------------------------------------------
            # 阶段 1: 等待有事可做 (Dispatch Event)
            # -------------------------------------------------------
            try:
                # 只要 dispatch_event 没设置，就一直在这里等
                await self.b.dispatch_event.wait()
            except asyncio.CancelledError:
                break

            # -------------------------------------------------------
            # 阶段 2: 检查是否满足分发条件 (Policy Check)
            # -------------------------------------------------------
            # 我们需要检查锁内的状态，但不要在这里长时间持有锁
            should_wait_for_instance = False
            periodic_wait_sec = 0.0
            dispatch_reason = "unknown"
            dispatch_request_ids = None

            async with self.b.lock:
                # 基本检查：队列空 或 队首未调度
                if not self.b.pending_queue or self.b.pending_queue[0].Task.batch_order != 1:
                    self.b.dispatch_event.clear()
                    continue

                policy = self.b.dispatch_policy
                now = time.time()

                can_dispatch = False

                if policy == "IMMEDIATE":
                    can_dispatch = True
                    dispatch_reason = "immediate"

                elif policy == "IDLE_WAIT":
                    if not self.b.instance_busy:
                        can_dispatch = True
                    else:
                        should_wait_for_instance = True
                        # 关键修复：如果忙，标记我们需要等待实例释放
                        should_wait_for_instance = True

                elif policy == "PERIODIC":
                    timing_decision = self._compute_periodic_decision(now)
                    periodic_wait_sec = timing_decision.delay_sec
                    if timing_decision.should_dispatch_now:
                        can_dispatch = True
                    else:
                        # 周期模式下如果没到时间且忙碌，稍微睡一会或者等实例
                        # 简单起见，这里选择等待超时
                        pass

                elif policy == "FULL_OR_IDLE_WAIT":
                    timing_decision = self._front_dispatch_decision(now)
                    periodic_wait_sec = timing_decision.delay_sec
                    dispatch_reason = timing_decision.reason
                    if timing_decision.should_dispatch_now:
                        can_dispatch = True
                        dispatch_request_ids = set(timing_decision.dispatch_request_ids)
                    elif timing_decision.should_wait_for_instance:
                        should_wait_for_instance = True
                    _log_timeline_event(
                        "DISPATCH_GATE_DECISION",
                        policy=policy,
                        action=(
                            "dispatch"
                            if can_dispatch
                            else "wait_for_instance"
                            if should_wait_for_instance
                            else "blocked"
                        ),
                        reason=dispatch_reason,
                        now=now,
                        front_ids=timing_decision.front_ids,
                        front_tokens=timing_decision.front_tokens,
                        front_total_tokens=timing_decision.front_total_tokens,
                        token_shortfall=timing_decision.token_shortfall,
                        ready=timing_decision.ready,
                        planned_full=timing_decision.planned_full,
                        is_full=timing_decision.is_full,
                        instance_busy=timing_decision.instance_busy,
                        active_instance_flows=timing_decision.active_instance_flows,
                        dispatch_instance_ids=timing_decision.dispatch_instance_ids,
                        blocked_instance_ids=timing_decision.blocked_instance_ids,
                        instance_details=timing_decision.instance_details,
                        inflight_prefill_tokens=timing_decision.inflight_prefill_tokens,
                        waiting_token_budget=timing_decision.waiting_token_budget,
                        dispatch_interval=self.dispatch_interval,
                        since_last_dispatch_ms=(now - self.last_dispatch_time) * 1000.0,
                    )

                if not can_dispatch:
                    if should_wait_for_instance:
                        # 不要清除 dispatch_event，因为队列里确实有东西要发。
                        # 只是当前资源不足。
                        pass
                    elif policy == "PERIODIC":
                        # 周期模式没满足条件，暂时不清除 event，但在外部我们要 wait_for timeout
                        pass
                    else:
                        # 其他情况清除事件
                        self.b.dispatch_event.clear()

                    # 退出锁，进入下面的等待逻辑

                else:
                    # ---> 执行分发提取逻辑
                    batch_to_send = []
                    if dispatch_request_ids is None:
                        while self.b.pending_queue and self.b.pending_queue[0].Task.batch_order == 1:
                            batch_to_send.append(self.b.pending_queue.pop(0))
                    else:
                        remaining_queue = []
                        for req in self.b.pending_queue:
                            if (
                                req.Task.batch_order == 1
                                and str(req.Request_ID) in dispatch_request_ids
                            ):
                                batch_to_send.append(req)
                            else:
                                remaining_queue.append(req)
                        self.b.pending_queue = remaining_queue

                    if batch_to_send:
                        self.b.instance_busy = True  # 标记繁忙
                        # 注意：这里不清空 dispatch_event，或者清空都行。
                        # 如果清空，下一次调度会重新 Set。
                        # 建议清空，防止空循环，除非还有剩余的 batch=1
                        if not (self.b.pending_queue and self.b.pending_queue[0].Task.batch_order == 1):
                            self.b.dispatch_event.clear()

                        self.last_dispatch_time = time.time()
                        dispatch_offset = self.last_dispatch_time - self.b.timeline_origin
                        rid_strs = [str(r.Request_ID)[-4:] for r in batch_to_send]
                        batch_orders = sorted({r.Task.batch_order for r in batch_to_send})
                        if dispatch_reason == "unknown":
                            dispatch_reason = policy.lower()
                        self.b.dispatch_group_seq += 1
                        dispatch_group_id = self.b.dispatch_group_seq
                        for request_order, r in enumerate(batch_to_send):
                            rid_key = _rid_key(r.Request_ID)
                            planned_start = getattr(r.Task, "slo_planned_start", None)
                            planned_finish = getattr(r.Task, "slo_planned_finish", None)
                            planned_batch_finish = getattr(r.Task, "slo_planned_batch_finish", None)
                            request_duration = 0.0
                            if planned_start is not None and planned_finish is not None:
                                request_duration = max(0.0, planned_finish - planned_start)
                            batch_duration = request_duration
                            if planned_start is not None and planned_batch_finish is not None:
                                batch_duration = max(0.0, planned_batch_finish - planned_start)
                            dispatch_predicted_finish = self.last_dispatch_time + request_duration
                            dispatch_batch_predicted_finish = self.last_dispatch_time + batch_duration
                            r.Task.slo_dispatch_time = self.last_dispatch_time
                            r.Task.slo_dispatch_group_id = dispatch_group_id
                            r.Task.slo_dispatch_request_order = request_order
                            r.Task.slo_dispatch_predicted_finish = dispatch_predicted_finish
                            r.Task.slo_dispatch_batch_predicted_finish = dispatch_batch_predicted_finish
                            self.b.inflight_prefill_reqs[rid_key] = r
                            self.b.inflight_prefill_sent_time[rid_key] = self.last_dispatch_time
                            self.b.inflight_prefill_meta[rid_key] = {
                                "dispatch_group_id": dispatch_group_id,
                                "sent_time": self.last_dispatch_time,
                                "request_order": request_order,
                                "schedule_id": getattr(r.Task, "slo_schedule_id", None),
                                "planned_finish": getattr(r.Task, "slo_planned_finish", None),
                                "planned_batch_order": getattr(r.Task, "slo_planned_batch_order", None),
                                "planned_step_order": getattr(r.Task, "slo_planned_step_order", None),
                                "dispatch_predicted_finish": dispatch_predicted_finish,
                                "dispatch_batch_predicted_finish": dispatch_batch_predicted_finish,
                            }
                        logging.info(
                            "[RealTimeline] dispatch t=%.6f offset=%.3fms batch_orders=%s size=%d ids=%s reason=%s",
                            self.last_dispatch_time,
                            dispatch_offset * 1000.0,
                            batch_orders,
                            len(batch_to_send),
                            rid_strs,
                            dispatch_reason,
                        )
                        _log_timeline_event(
                            "DISPATCH",
                            schedule_id=getattr(batch_to_send[0].Task, "slo_schedule_id", None),
                            dispatch_group_id=dispatch_group_id,
                            dispatch_time=self.last_dispatch_time,
                            dispatch_offset_ms=dispatch_offset * 1000.0,
                            batch_orders=batch_orders,
                            ids=[str(r.Request_ID) for r in batch_to_send],
                            tokens=[getattr(r.Prompt, "token_length", 0) for r in batch_to_send],
                            reason=dispatch_reason,
                            planned_start=getattr(batch_to_send[0].Task, "slo_planned_start", None),
                            planned_finish=getattr(batch_to_send[0].Task, "slo_planned_batch_finish", None),
                            dispatch_predicted_finish=max(
                                getattr(r.Task, "slo_dispatch_batch_predicted_finish", self.last_dispatch_time)
                                for r in batch_to_send
                            ),
                        )

                        # 启动执行任务
                        # 按实例分组 (支持多实例扩展)
                        groups = defaultdict(list)
                        for r in batch_to_send:
                            groups[r.Task.prefill_instance].append(r)

                        for ip, reqs in groups.items():
                            planned_reqs = [
                                r for r in reqs
                                if getattr(r.Task, "slo_schedule_id", None) is not None
                            ]
                            if planned_reqs:
                                group_predicted_finish = max(
                                    getattr(
                                        r.Task,
                                        "slo_dispatch_batch_predicted_finish",
                                        self.last_dispatch_time,
                                    )
                                    for r in planned_reqs
                                )
                                self.b.active_instance_reservations[ip][dispatch_group_id] = group_predicted_finish
                                storage.update_predicted_availability(
                                    ip,
                                    max(self.b.active_instance_reservations[ip].values()),
                                )
                            self.b.active_instance_flows += 1
                            self.b.active_instance_flows_by_ip[ip] += 1
                            asyncio.create_task(
                                self.b._instance_execution_flow(ip, reqs, dispatch_group_id)
                            )

                        if self.b.pending_queue:
                            self.b.schedule_event.set()

                        # 已分发，直接进入下一次循环
                        continue

            # -------------------------------------------------------
            # 阶段 3: 等待资源释放 (Wait for Resource)
            # -------------------------------------------------------
            if should_wait_for_instance:
                # 关键修复：当 IDLE_WAIT 且 实例忙 时，等待 instance_release_event
                # 而不是直接 continue 并回到 dispatch_event.wait() (因为事件可能没清空导致死循环，或者被清空导致死锁)
                # 这里的逻辑是：我知道有东西要发，但我得等实例空闲。
                logging.debug("DispatchEngine: Waiting for instance to become idle...")
                await self.b.instance_release_event.wait()
                # 醒来后，不需要 clear instance_event，由 execution_flow 负责 set，
                # 我们只需要再次循环去 check 即可。
                self.b.instance_release_event.clear()

            elif self.b.dispatch_policy == "PERIODIC":
                # 周期模式的等待
                timing_decision = self._compute_periodic_decision(time.time())
                remaining = timing_decision.delay_sec
                if remaining > 0:
                    try:
                        # 等待超时，或者等 dispatch_event 被再次触发（可能有新请求插入前面）
                        # 或者等实例释放（提前发送）
                        wait_tasks = [
                            asyncio.create_task(asyncio.sleep(remaining)),
                            asyncio.create_task(self.b.instance_release_event.wait())
                        ]
                        done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
                        for t in pending: t.cancel()
                    except:
                        pass

# ==========================================
# 子类 3: 监控引擎 (Monitoring Engine)
# ==========================================
class MonitoringEngine:
    """监控引擎，负责监控TTFT统计信息并动态调整调度策略"""

    def __init__(self, batcher: RequestBatcher):
        self.b = batcher
        self.prefill_completed_requests = set()
        self.prefill_done_chunk_order = {}
        self.prefill_done_plan_meta = {}

    async def run(self):
        """监控引擎主循环"""
        while True:
            await asyncio.sleep(self.b.syn_timeout)
            if self.b.trigger_mode == "TIMEOUT":
                async with self.b.lock:
                    if self.b.pending_queue:
                        self.b.schedule_event.set()

    def handle_ttft_stats(self, ttft: float, is_violated: bool, rid: Any):
        """
        处理TTFT统计信息
        逻辑变更：
        1. 第一次收到 (RID不在集合中): 视为 Prefill 耗时，仅记录日志，不影响策略。
        2. 第二次收到 (RID在集合中): 视为真实 TTFT，记录日志并用于策略调整。
        """
        rid_key = _rid_key(rid)
        rid_str = rid_key

        # --- 逻辑分支开始 ---
        if rid_key not in self.prefill_completed_requests:
            # [情况1] 第一次收到反馈 -> 视为 Prefill 计算完成时间
            logging.info(f"[Time Log] Req: {rid_str[-4:]} | Prefill Done: {ttft:.4f}s | (Not counting as TTFT)")
            req = self.b.inflight_prefill_reqs.pop(rid_key, None)
            sent_time = self.b.inflight_prefill_sent_time.pop(rid_key, None)
            inflight_meta = self.b.inflight_prefill_meta.pop(rid_key, None) or {}
            batch_order = None
            plan_meta = dict(inflight_meta)
            if req is not None:
                batch_order = getattr(req.Task, "batch_order", None)
                self.prefill_done_chunk_order[rid_key] = batch_order
                plan_meta.update(
                    {
                        "schedule_id": getattr(req.Task, "slo_schedule_id", plan_meta.get("schedule_id")),
                        "planned_finish": getattr(req.Task, "slo_planned_finish", plan_meta.get("planned_finish")),
                        "planned_batch_order": getattr(req.Task, "slo_planned_batch_order", batch_order),
                        "planned_step_order": getattr(req.Task, "slo_planned_step_order", None),
                    }
                )
                self.prefill_done_plan_meta[rid_key] = plan_meta
            now = time.time()
            logging.info(
                "[RealTimeline] prefill_done rid=%s t=%.6f offset=%.3fms sent_offset=%s batch_order=%s not_counted_as_ttft=True",
                rid_str[-4:],
                now,
                (now - self.b.timeline_origin) * 1000.0,
                "" if sent_time is None else f"{(sent_time - self.b.timeline_origin) * 1000.0:.3f}ms",
                batch_order,
            )
            planned_finish = plan_meta.get("planned_finish")
            dispatch_predicted_finish = plan_meta.get("dispatch_predicted_finish")
            diff_predicted_finish = dispatch_predicted_finish
            if diff_predicted_finish is None:
                diff_predicted_finish = planned_finish
            actual_minus_predicted_ms = None
            if diff_predicted_finish is not None:
                actual_minus_predicted_ms = (now - diff_predicted_finish) * 1000.0
            _log_timeline_event(
                "REQUEST_DONE_DIFF",
                schedule_id=plan_meta.get("schedule_id"),
                request_id=rid_key,
                short_request_id=rid_str[-4:],
                batch_order=batch_order,
                step_order=plan_meta.get("planned_step_order"),
                sent_time=sent_time,
                actual_finish=now,
                actual_finish_offset_ms=(now - self.b.timeline_origin) * 1000.0,
                planned_finish=planned_finish,
                dispatch_predicted_finish=dispatch_predicted_finish,
                actual_minus_predicted_ms=actual_minus_predicted_ms,
                delta_ms=actual_minus_predicted_ms,
                kind="prefill_done",
            )

            # 标记该请求已完成 Prefill，下一次该 ID 进来就是 TTFT
            self.prefill_completed_requests.add(rid_key)
            return  # 直接返回，不执行下方的策略更新逻辑

        else:
            # [情况2] 第二次收到反馈 -> 视为真实 TTFT
            # 既然已经收到了 TTFT，从集合中移除以释放内存 (假设不会有第三次首字回调)
            try:
                self.prefill_completed_requests.remove(rid_key)
            except KeyError:
                pass

            logging.info(
                f"[Time Log] Req: {rid_str[-4:]} | Real TTFT: {ttft:.4f}s | Mode: {self.b.scheduler_mgr.strategy}")
            batch_order = self.prefill_done_chunk_order.pop(rid_key, None)
            plan_meta = self.prefill_done_plan_meta.pop(rid_key, {})
            now = time.time()
            logging.info(
                "[RealTimeline] real_ttft rid=%s t=%.6f offset=%.3fms batch_order=%s ttft=%.4f",
                rid_str[-4:],
                now,
                (now - self.b.timeline_origin) * 1000.0,
                batch_order,
                ttft,
            )
            planned_finish = plan_meta.get("planned_finish")
            dispatch_predicted_finish = plan_meta.get("dispatch_predicted_finish")
            diff_predicted_finish = dispatch_predicted_finish
            if diff_predicted_finish is None:
                diff_predicted_finish = planned_finish
            actual_minus_predicted_ms = None
            if diff_predicted_finish is not None:
                actual_minus_predicted_ms = (now - diff_predicted_finish) * 1000.0
            _log_timeline_event(
                "REQUEST_DONE_DIFF",
                schedule_id=plan_meta.get("schedule_id"),
                request_id=rid_key,
                short_request_id=rid_str[-4:],
                batch_order=batch_order,
                step_order=plan_meta.get("planned_step_order"),
                actual_finish=now,
                actual_finish_offset_ms=(now - self.b.timeline_origin) * 1000.0,
                planned_finish=planned_finish,
                dispatch_predicted_finish=dispatch_predicted_finish,
                actual_minus_predicted_ms=actual_minus_predicted_ms,
                delta_ms=actual_minus_predicted_ms,
                kind="real_ttft",
                ttft=ttft,
            )

            # 以下是原有的策略调整逻辑，仅在此处执行
            if not getattr(storage, "ENABLE_ADAPTIVE_SWITCH", self.b.adaptive_switch):
                return
            if self.b.profile_mode: return

            self.b.stats_window.append(is_violated)
            if len(self.b.stats_window) < 10: return

            v_rate = sum(self.b.stats_window) / len(self.b.stats_window)
            current_strategy = self.b.scheduler_mgr.strategy

            if not self.b.scheduler_mgr.get_capabilities(
                current_strategy
            ).allow_adaptive_switch:
                return

            # 策略切换逻辑
            if current_strategy == "round_robin" and v_rate > self.b.violation_threshold:
                self.b.scheduler_mgr.strategy = self.b.optimized_strategy
                storage.set_prefill_scheduler_strategy(self.b.optimized_strategy)
                self.b.strategy_version += 1
                self.b.stats_window.clear()
                logging.warning(f"!!! High Violation ({v_rate:.1%}): Switching to {self.b.optimized_strategy}")
                asyncio.create_task(self.b.preempt_heavy_requests())

            elif current_strategy != "round_robin" and v_rate < self.b.recovery_threshold:
                self.b.scheduler_mgr.strategy = "round_robin"
                storage.set_prefill_scheduler_strategy("round_robin")
                self.b.strategy_version += 1
                self.b.stats_window.clear()
                logging.info(f"!!! Low Violation: Recovering to round_robin")


# ==========================================
# 主类: 请求批处理器 (Orchestrator)
# ==========================================
class RequestBatcher:
    """请求批处理器主类，协调调度、分发和监控引擎"""

    def __init__(self, default_strategy: str = "slo_aware_prefill"):
        self.lock = asyncio.Lock()
        self.pending_queue: List[BaseRequest] = []
        self.batch_manager: Dict[str, dict] = {}
        self.timeline_origin = time.time()

        # 状态标志
        self.instance_busy: bool = False
        self.active_instance_flows: int = 0
        self.active_instance_flows_by_ip: Dict[str, int] = defaultdict(int)
        self.is_shutting_down: bool = False

        # 调度记忆
        self.last_plan: List[BaseRequest] = []

        # 句柄追踪：{request_id: (Task, BaseRequest, start_time)}
        self.running_tasks_map: Dict[str, Tuple[asyncio.Task, BaseRequest, float]] = {}
        self.inflight_prefill_reqs: Dict[str, BaseRequest] = {}
        self.inflight_prefill_sent_time: Dict[str, float] = {}
        self.inflight_prefill_meta: Dict[str, dict] = {}
        self.dispatch_group_seq = 0
        self.active_instance_reservations: Dict[str, Dict[int, float]] = defaultdict(dict)

        # 策略配置
        self.scheduler_mgr = SchedulerManager(
            strategy=default_strategy,
            storage=storage,
        )
        self.optimized_strategy = default_strategy
        self.dispatch_policy = "IMMEDIATE"
        self.syn_batch_size = 1
        self.syn_timeout = 1
        self.trigger_mode = "ARRIVAL"
        self.profile_mode = False
        self.adaptive_switch = False
        self.strategy_version = 0
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None

        if self.profile_mode:
            self.scheduler_mgr.strategy = "round_robin"
        else:
            self.scheduler_mgr.strategy = default_strategy

            # 统计窗口
        self.stats_window = deque(maxlen=20)
        self.violation_threshold = 0.1
        self.recovery_threshold = 0.05

        # 事件系统
        self.schedule_event = asyncio.Event()
        self.dispatch_event = asyncio.Event()
        self.instance_release_event = asyncio.Event()  # 新增：实例释放事件

        # 子引擎
        self.sched_engine = SchedulingEngine(self)
        self.dispatch_engine = DispatchEngine(self)
        self.monitor_engine = MonitoringEngine(self)
        self.worker_tasks = []

    def _create_engine_task(self, name: str, coro_factory):
        async def _runner():
            while not self.is_shutting_down:
                try:
                    await coro_factory()
                    break
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logging.error("%s crashed: %s", name, exc, exc_info=True)
                    _log_timeline_event(
                        "ENGINE_CRASH",
                        engine=name,
                        error=repr(exc),
                    )
                    await asyncio.sleep(0.1)

        return asyncio.create_task(_runner())

    async def start(self):
        self.event_loop = asyncio.get_running_loop()
        self.syn_batch_size = int(getattr(storage, "SYN_BATCH_SIZE", self.syn_batch_size))
        self.syn_timeout = float(getattr(storage, "SYN_TIMEOUT", self.syn_timeout))
        self.trigger_mode = getattr(storage, "TRIGGER_MODE", self.trigger_mode)
        self.profile_mode = bool(getattr(storage, "PROFILE_MODE", self.profile_mode))
        self.adaptive_switch = bool(getattr(storage, "ENABLE_ADAPTIVE_SWITCH", self.adaptive_switch))
        self.optimized_strategy = getattr(storage, "SCHEDULER_STRATEGY", self.optimized_strategy)
        self.scheduler_mgr.strategy = "round_robin" if self.profile_mode else self.optimized_strategy
        self.dispatch_engine.configure_from_storage()

        self.worker_tasks.append(
            self._create_engine_task("SchedulingEngine", self.sched_engine.run)
        )
        self.worker_tasks.append(
            self._create_engine_task("DispatchEngine", self.dispatch_engine.run)
        )
        self.worker_tasks.append(
            self._create_engine_task("MonitoringEngine", self.monitor_engine.run)
        )
        logging.info("RequestBatcher Engines Started.")

    async def switch_strategy(self, strategy: str) -> Dict[str, Any]:
        """Switch the Prefill strategy and reschedule requests not yet dispatched."""
        if strategy not in storage.PREFILL_SCHEDULER_STRATEGIES:
            raise ValueError(f"Unsupported Prefill scheduler strategy: {strategy}")

        async with self.lock:
            previous = self.scheduler_mgr.strategy
            self.scheduler_mgr.on_switch_out(previous, self.pending_queue)
            storage.set_prefill_scheduler_strategy(strategy)
            self.scheduler_mgr.strategy = strategy
            self.optimized_strategy = strategy
            self.strategy_version += 1
            self.last_plan = []

            for request in self.pending_queue:
                request.Task.batch_order = -1
                request.Task.prefill_instance = ""

            capabilities = self.scheduler_mgr.get_capabilities(strategy)
            if capabilities.trigger_on_arrival:
                self.pending_queue.sort(
                    key=lambda request: float(
                        getattr(request.Service, "arrival_time", 0.0) or 0.0
                    )
                )

            pending_count = len(self.pending_queue)
            if pending_count:
                self.schedule_event.set()

            return {
                "previous": previous,
                "current": strategy,
                "pending_count": pending_count,
                "adaptive_switch": (
                    self.adaptive_switch
                    and capabilities.allow_adaptive_switch
                ),
            }

    def switch_strategy_threadsafe(self, strategy: str, timeout: float = 10.0) -> Dict[str, Any]:
        """Submit a strategy switch from the foreground CLI thread."""
        if self.event_loop is None or not self.event_loop.is_running():
            raise RuntimeError("RequestBatcher is not running")
        future = asyncio.run_coroutine_threadsafe(self.switch_strategy(strategy), self.event_loop)
        return future.result(timeout=timeout)

    async def preempt_heavy_requests(self):
        """抢占逻辑"""
        if not self.scheduler_mgr.get_capabilities().allow_preemption:
            return
        async with self.lock:
            now = time.time()
            targets = []
            for rid, (task, req, start_t) in self.running_tasks_map.items():
                run_dur = now - start_t
                if req.Prompt.token_length > 500 and run_dur < 1.0 and not getattr(req.Service, "is_preempted", False):
                    targets.append(rid)

            if not targets: return

            logging.warning(f"⚔️ [Preemption] Aborting {len(targets)} heavy requests.")
            for rid in targets:
                task, req, _ = self.running_tasks_map[rid]
                req.Service.is_preempted = True
                req.Task.batch_order = -1
                task.cancel()
                self.inflight_prefill_reqs.pop(rid, None)
                self.inflight_prefill_sent_time.pop(rid, None)
                self.inflight_prefill_meta.pop(rid, None)
                self.pending_queue.append(req)

            self.schedule_event.set()

    def _unified_trigger(self, is_new_arrival: bool):
        q_len = len(self.pending_queue)
        if q_len == 0: return
        if (
            self.scheduler_mgr.get_capabilities().trigger_on_arrival
            and is_new_arrival
        ):
            self.schedule_event.set()
            return
        if (self.trigger_mode == "ARRIVAL" and is_new_arrival) or \
                (self.trigger_mode == "BATCH_SIZE" and q_len >= self.syn_batch_size):
            self.schedule_event.set()

    async def add_request(self, request: BaseRequest) -> Dict[str, Any]:
        """普通同步 batching 前端调用入口，返回 StreamingResponse 所需 generator。"""
        return await self._add_request(request, mode="stream")

    async def add_connector_prefill_request(self, request: BaseRequest) -> Dict[str, Any]:
        """Connector-aware PD Prefill batching entrypoint."""
        return await self._add_request(request, mode="connector_prefill")

    async def _add_request(self, request: BaseRequest, mode: str) -> Dict[str, Any]:
        """前端调用入口"""
        if not storage.active_prefill_objs:
            return {
                "success": False,
                "error": "No active prefill instance",
                "status_code": 503,
            }
        request.Service.arrival_time = time.time()
        rid = request.Request_ID
        rid_key = _rid_key(rid)
        event = asyncio.Event()

        metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
        timing = dict(metadata.get("gateway_timing", {}) or {})
        timing["prefill_arrival_s"] = request.Service.arrival_time
        metadata["gateway_timing"] = timing
        request.Task.scheduler_metadata = metadata
        prefill_observer.record_timing(
            "request_arrived",
            request_id=rid_key,
            timestamp_s=request.Service.arrival_time,
            prompt_tokens=getattr(request.Prompt, "token_length", None),
            mode=mode,
        )

        async with self.lock:
            self.batch_manager[rid_key] = {
                "event": event,
                "result": {},
                "mode": mode,
            }
            request.Task.batch_order = -1
            self.pending_queue.append(request)
            _log_timeline_event(
                "REQUEST_ENQUEUED",
                request_id=rid_key,
                prompt_tokens=getattr(request.Prompt, "token_length", None),
                mode=mode,
                pending_queue_size=len(self.pending_queue),
                trigger_mode=self.trigger_mode,
                schedule_event_set=self.schedule_event.is_set(),
            )
            self._unified_trigger(is_new_arrival=True)

        try:
            await event.wait()
        except asyncio.CancelledError:
            async with self.lock:
                self.batch_manager.pop(rid_key, None)
            raise

        async with self.lock:
            res = self.batch_manager.pop(rid_key, {}).get("result", None)
            return res if res else {"success": False, "error": "lost", "instance": "none"}

    async def _instance_execution_flow(
            self,
            instance: str,
            reqs: List[BaseRequest],
            dispatch_group_id: int = None,
    ):
        """
        实例执行生命周期管理。
        使用 asyncio.gather 并行启动转发，并等待所有流完成。
        """
        now = time.time()
        tasks = []

        # 1. 启动所有请求的转发任务
        try:
            for r in reqs:
                t = asyncio.create_task(self._forward_wrapper(instance, r))
                tasks.append(t)
                async with self.lock:
                    self.running_tasks_map[_rid_key(r.Request_ID)] = (t, r, now)

            # 2. 等待所有任务完成（无论成功还是异常）
            # 使用 return_exceptions=True 确保个别失败不影响整体流程
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # 3. 资源释放与后续触发
            storage.update_real_availability(instance, time.time())
        except Exception as e:
            logging.error(f"Critical error in execution flow: {e}", exc_info=True)

        finally:
            async with self.lock:
                self.active_instance_flows = max(0, self.active_instance_flows - 1)
                if instance in self.active_instance_flows_by_ip:
                    self.active_instance_flows_by_ip[instance] = max(
                        0,
                        self.active_instance_flows_by_ip[instance] - 1,
                    )
                    if self.active_instance_flows_by_ip[instance] <= 0:
                        self.active_instance_flows_by_ip.pop(instance, None)
                self.instance_busy = self.active_instance_flows > 0
                actual_finish_time = time.time()
                reservations = self.active_instance_reservations.get(instance, {})
                if dispatch_group_id is not None:
                    reservations.pop(dispatch_group_id, None)
                if reservations:
                    storage.update_predicted_availability(instance, max(reservations.values()))
                else:
                    self.active_instance_reservations.pop(instance, None)
                    storage.update_predicted_availability(instance, actual_finish_time)

            logging.debug(f"Instance {instance} freed. Waking up engines.")

            # 1. 唤醒分发器 (如果它在等 IDLE)
            self.instance_release_event.set()
            self.dispatch_event.set()

            # 2. 【核心】唤醒调度器处理剩下的请求
            # 只有这里触发，才能把剩下的 batch_order=-1 的请求变成 batch_order=1
            if self.pending_queue:
                self.schedule_event.set()

    async def _forward_wrapper(self, instance: str, request: BaseRequest):
        """
        单个请求的转发包装器。
        """
        rid = request.Request_ID
        rid_key = _rid_key(rid)
        tracker = None
        prefill_started_s = time.time()
        prefill_finished_s = None
        final_status = "failed"
        final_error = None
        metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
        timing = dict(metadata.get("gateway_timing", {}) or {})
        timing["prefill_start_s"] = prefill_started_s
        timing["prefill_instance"] = instance
        metadata["gateway_timing"] = timing
        request.Task.scheduler_metadata = metadata
        prefill_observer.record_timing(
            "prefill_dispatched",
            request_id=rid_key,
            timestamp_s=prefill_started_s,
            instance=instance,
        )
        try:
            async with self.lock:
                batch_entry = self.batch_manager.get(rid_key, {})
                mode = batch_entry.get("mode", "stream")

            target_url = f"http://{instance}/v1/{request.Service.Endpoint_type}"
            if mode == "connector_prefill":
                _bind_prefill_connector(request, instance)
                connector_manager = get_connector_manager()
                payload = connector_manager.build_prefill_payload(request)

                print("\n\n\n")
                print("+" * 80)
                print("[Gateway] Connector Prefill 同步调度状态")
                print("+" * 80)
                print(f"转发实例: {instance}")
                for k, v in payload.items():
                    print(f"请求报文 {k}: {v}")
                print("+" * 80)
                print("\n\n\n")

                prefill_response = await forward_request_json(
                    url=target_url,
                    data=payload,
                    extra_headers=connector_manager.headers(request),
                )
                connector_manager.bind_prefill_result(request, prefill_response)
                kv_transfer_params = request.Task.connector_context.get(
                    "kv_transfer_params", {}
                )
                request.Task.prefill_instance = instance
                prefill_finished_s = time.time()
                metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
                timing = dict(metadata.get("gateway_timing", {}) or {})
                timing["prefill_end_s"] = prefill_finished_s
                metadata["gateway_timing"] = timing
                request.Task.scheduler_metadata = metadata
                final_status = "completed"
                prefill_observer.record_timing(
                    "prefill_finished",
                    request_id=rid_key,
                    timestamp_s=prefill_finished_s,
                    instance=instance,
                    prefill_ms=max(0.0, (prefill_finished_s - prefill_started_s) * 1000.0),
                    prefill_queue_ms=max(
                        0.0,
                        (
                            prefill_started_s
                            - float(timing.get("prefill_arrival_s", prefill_started_s))
                        )
                        * 1000.0,
                    ),
                )

                async with self.lock:
                    if rid_key in self.batch_manager:
                        self.batch_manager[rid_key]["result"] = {
                            "success": True,
                            "instance": instance,
                            "prefill_response": prefill_response,
                            "kv_transfer_params": kv_transfer_params,
                        }
                        self.batch_manager[rid_key]["event"].set()
                    self.inflight_prefill_reqs.pop(rid_key, None)
                    self.inflight_prefill_sent_time.pop(rid_key, None)
                    self.inflight_prefill_meta.pop(rid_key, None)
                    if self.pending_queue:
                        self.schedule_event.set()

                logging.info(
                    "Connector prefill req %s finished on %s.",
                    str(rid)[-4:],
                    instance,
                )
                return

            payload = BaseRequest.to_vllm_payload(request)

            # FIXME: 演示用，打印池调度状态
            print("\n\n\n")
            print("+" * 80)
            print(f"[Gateway] 调度状态 ")
            print("+" * 80)
            print(f"转发实例: {instance}")
            for k, v in payload.items():
                print(f"请求报文 {k}: {v}")
            print("+" * 80)
            print("\n\n\n")

            # 将统计所需的参数传入 forward_request
            gen = forward_request(
                url=target_url,
                data=payload,
                arrival_time=request.Service.arrival_time,
                slo_sec=request.Service.SLO_TTFT / 1000.0,
                request_id=rid_key,
                stats_callback=self.monitor_engine.handle_ttft_stats  # 回调下沉到 fwd
            )

            # 创建追踪器 (不再需要统计参数)
            tracker = IterableTracker(gen)

            # 将结果传回给 add_request 的等待者
            async with self.lock:
                if rid_key in self.batch_manager:
                    self.batch_manager[rid_key]["result"] = {
                        "success": True,
                        "generator": tracker,
                        "instance": instance
                    }
                    self.batch_manager[rid_key]["event"].set()

            # 挂起等待，直到流被完全消费（或客户端断开）
            # 只有这里 await 结束，_instance_execution_flow 才会认为该 slot 释放
            await tracker.wait_for_completion()

            logging.info(f"Req {str(rid)[-4:]} finished on {instance}.")
            prefill_finished_s = time.time()
            metadata = dict(getattr(request.Task, "scheduler_metadata", {}) or {})
            timing = dict(metadata.get("gateway_timing", {}) or {})
            timing["prefill_end_s"] = prefill_finished_s
            metadata["gateway_timing"] = timing
            request.Task.scheduler_metadata = metadata
            prefill_observer.record_timing(
                "prefill_finished",
                request_id=rid_key,
                timestamp_s=prefill_finished_s,
                instance=instance,
                prefill_ms=max(0.0, (prefill_finished_s - prefill_started_s) * 1000.0),
                prefill_queue_ms=max(
                    0.0,
                    (
                        prefill_started_s
                        - float(timing.get("prefill_arrival_s", prefill_started_s))
                    )
                    * 1000.0,
                ),
            )
            final_status = "completed"

        except asyncio.CancelledError:
            # 处理抢占或关闭
            if getattr(request.Service, "is_preempted", False) and not self.is_shutting_down:
                logging.info(f"🔄 [Bounced] Req {str(rid)[-4:]} preempted...")
            else:
                logging.debug(f"Req {rid} cancelled.")
            final_status = "cancelled"
            final_error = "request_cancelled"

            # 确保通知 add_request，防止死锁
            async with self.lock:
                if rid_key in self.batch_manager:
                    self.batch_manager[rid_key]["result"] = {
                        "success": False,
                        "error": "request_cancelled",
                        "instance": instance
                    }
                    self.batch_manager[rid_key]["event"].set()
                self.inflight_prefill_reqs.pop(rid_key, None)
                self.inflight_prefill_sent_time.pop(rid_key, None)
                self.inflight_prefill_meta.pop(rid_key, None)

        except Exception as e:
            # 异常处理
            logging.error(f"Req {rid} failed: {e}")
            final_status = "failed"
            final_error = str(e)
            self.monitor_engine.handle_ttft_stats(999, True, rid_key)
            async with self.lock:
                if rid_key in self.batch_manager:
                    self.batch_manager[rid_key]["result"] = {
                        "success": False,
                        "error": str(e),
                        "instance": instance
                    }
                    self.batch_manager[rid_key]["event"].set()
                self.inflight_prefill_reqs.pop(rid_key, None)
                self.inflight_prefill_sent_time.pop(rid_key, None)
                self.inflight_prefill_meta.pop(rid_key, None)
        finally:
            if final_status != "completed":
                prefill_observer.record_timing(
                    "prefill_failed" if final_status == "failed" else "prefill_cancelled",
                    request_id=rid_key,
                    timestamp_s=time.time(),
                    instance=instance,
                    status=final_status,
                    error=final_error,
                )
            self.scheduler_mgr.on_request_finished(request)
            # 清理全局运行映射
            async with self.lock:
                self.running_tasks_map.pop(rid_key, None)

    async def shutdown(self):
        """优雅关闭"""
        self.is_shutting_down = True
        for task in self.worker_tasks:
            task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.event_loop = None
