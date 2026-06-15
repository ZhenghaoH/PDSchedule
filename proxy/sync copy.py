from __future__ import annotations
import logging
import asyncio
import time
import threading
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from scheduler.gateway.prefill.manager import SchedulerManager
from proxy.forward import forward_request_trace as forward_request
from core.schemas.request import BaseRequest

from store.gateway.manager import GatewayStoreManager

# 导入配置参数
storage = GatewayStoreManager()


def _rid_key(rid: Any) -> str:
    return str(rid)


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
                    self.b.dispatch_event.set()
                    continue  # <--- 直接跳过本次调度，让分发器先工作

                # 获取待调度的候选请求（浅拷贝）
                candidates = list(self.b.pending_queue)
                last_plan = getattr(self.b, "last_plan", None)
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
                )
            except Exception as e:
                logging.error(f"Scheduler Error: {e}", exc_info=True)
                continue

            # 应用调度结果
            async with self.b.lock:
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
                        valid_reqs.append(r)

                # 排序：batch_order > 0 的优先，同批次按到达时间
                self.b.pending_queue.sort(key=lambda x: (
                    x.Task.batch_order if x.Task.batch_order > 0 else 9999,
                    x.Service.arrival_time
                ))
                self.b.last_plan = scheduled_plan

                # 如果有请求就绪，唤醒分发器
                if self.b.pending_queue and self.b.pending_queue[0].Task.batch_order == 1:
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
                    while self.b.pending_queue and self.b.pending_queue[0].Task.batch_order == 1:
                        batch_to_send.append(self.b.pending_queue.pop(0))

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
                        self.b.dispatch_group_seq += 1
                        dispatch_group_id = self.b.dispatch_group_seq
                        for request_order, r in enumerate(batch_to_send):
                            rid_key = _rid_key(r.Request_ID)
                            r.Task.slo_dispatch_time = self.last_dispatch_time
                            r.Task.slo_dispatch_group_id = dispatch_group_id
                            r.Task.slo_dispatch_request_order = request_order
                            self.b.inflight_prefill_reqs[rid_key] = r
                            self.b.inflight_prefill_sent_time[rid_key] = self.last_dispatch_time
                            self.b.inflight_prefill_meta[rid_key] = {
                                "dispatch_group_id": dispatch_group_id,
                                "sent_time": self.last_dispatch_time,
                                "request_order": request_order,
                            }
                        logging.info(
                            "[RealTimeline] dispatch t=%.6f offset=%.3fms batch_orders=%s size=%d ids=%s",
                            self.last_dispatch_time,
                            dispatch_offset * 1000.0,
                            batch_orders,
                            len(batch_to_send),
                            rid_strs,
                        )

                        # 启动执行任务
                        # 按实例分组 (支持多实例扩展)
                        groups = defaultdict(list)
                        for r in batch_to_send:
                            groups[r.Task.prefill_instance].append(r)

                        for ip, reqs in groups.items():
                            asyncio.create_task(self.b._instance_execution_flow(ip, reqs))

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
            self.b.inflight_prefill_meta.pop(rid_key, None)
            batch_order = None
            if req is not None:
                batch_order = getattr(req.Task, "batch_order", None)
                self.prefill_done_chunk_order[rid_key] = batch_order
            now = time.time()
            logging.info(
                "[RealTimeline] prefill_done rid=%s t=%.6f offset=%.3fms sent_offset=%s batch_order=%s not_counted_as_ttft=True",
                rid_str[-4:],
                now,
                (now - self.b.timeline_origin) * 1000.0,
                "" if sent_time is None else f"{(sent_time - self.b.timeline_origin) * 1000.0:.3f}ms",
                batch_order,
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
            now = time.time()
            logging.info(
                "[RealTimeline] real_ttft rid=%s t=%.6f offset=%.3fms batch_order=%s ttft=%.4f",
                rid_str[-4:],
                now,
                (now - self.b.timeline_origin) * 1000.0,
                batch_order,
                ttft,
            )

            # 以下是原有的策略调整逻辑，仅在此处执行
            if not getattr(storage, "ENABLE_ADAPTIVE_SWITCH", self.b.adaptive_switch):
                return
            if self.b.profile_mode: return

            self.b.stats_window.append(is_violated)
            if len(self.b.stats_window) < 10: return

            v_rate = sum(self.b.stats_window) / len(self.b.stats_window)
            current_strategy = self.b.scheduler_mgr.strategy

            # 策略切换逻辑
            if current_strategy == "round_robin" and v_rate > self.b.violation_threshold:
                self.b.scheduler_mgr.strategy = self.b.optimized_strategy
                self.b.stats_window.clear()
                logging.warning(f"!!! High Violation ({v_rate:.1%}): Switching to {self.b.optimized_strategy}")
                asyncio.create_task(self.b.preempt_heavy_requests())

            elif current_strategy != "round_robin" and v_rate < self.b.recovery_threshold:
                self.b.scheduler_mgr.strategy = "round_robin"
                self.b.stats_window.clear()
                logging.info(f"!!! Low Violation: Recovering to round_robin")


# ==========================================
# 主类: 请求批处理器 (Orchestrator)
# ==========================================
class RequestBatcher:
    """请求批处理器主类，协调调度、分发和监控引擎"""

    def __init__(self, default_strategy: str = "pso"):
        self.lock = asyncio.Lock()
        self.pending_queue: List[BaseRequest] = []
        self.batch_manager: Dict[str, dict] = {}
        self.timeline_origin = time.time()

        # 状态标志
        self.instance_busy: bool = False
        self.is_shutting_down: bool = False

        # 调度记忆
        self.last_plan: List[BaseRequest] = []

        # 句柄追踪：{request_id: (Task, BaseRequest, start_time)}
        self.running_tasks_map: Dict[str, Tuple[asyncio.Task, BaseRequest, float]] = {}
        self.inflight_prefill_reqs: Dict[str, BaseRequest] = {}
        self.inflight_prefill_sent_time: Dict[str, float] = {}
        self.inflight_prefill_meta: Dict[str, dict] = {}
        self.dispatch_group_seq = 0

        # 策略配置
        self.scheduler_mgr = SchedulerManager(strategy=default_strategy)
        self.optimized_strategy = default_strategy
        self.dispatch_policy = "IMMEDIATE"
        self.syn_batch_size = 1
        self.syn_timeout = 1
        self.trigger_mode = "ARRIVAL"
        self.profile_mode = False
        self.adaptive_switch = False

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

    async def start(self):
        self.syn_batch_size = int(getattr(storage, "SYN_BATCH_SIZE", self.syn_batch_size))
        self.syn_timeout = float(getattr(storage, "SYN_TIMEOUT", self.syn_timeout))
        self.trigger_mode = getattr(storage, "TRIGGER_MODE", self.trigger_mode)
        self.profile_mode = bool(getattr(storage, "PROFILE_MODE", self.profile_mode))
        self.adaptive_switch = bool(getattr(storage, "ENABLE_ADAPTIVE_SWITCH", self.adaptive_switch))
        self.optimized_strategy = getattr(storage, "SCHEDULER_STRATEGY", self.optimized_strategy)
        self.scheduler_mgr.strategy = "round_robin" if self.profile_mode else self.optimized_strategy
        self.dispatch_engine.configure_from_storage()

        self.worker_tasks.append(asyncio.create_task(self.sched_engine.run()))
        self.worker_tasks.append(asyncio.create_task(self.dispatch_engine.run()))
        self.worker_tasks.append(asyncio.create_task(self.monitor_engine.run()))
        logging.info("RequestBatcher Engines Started.")

    async def preempt_heavy_requests(self):
        """抢占逻辑"""
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
        if (self.trigger_mode == "ARRIVAL" and is_new_arrival) or \
                (self.trigger_mode == "BATCH_SIZE" and q_len >= self.syn_batch_size):
            self.schedule_event.set()

    async def add_request(self, request: BaseRequest) -> Dict[str, Any]:
        """前端调用入口"""
        request.Service.arrival_time = time.time()
        rid = request.Request_ID
        rid_key = _rid_key(rid)
        event = asyncio.Event()

        async with self.lock:
            self.batch_manager[rid_key] = {"event": event, "result": {}}
            request.Task.batch_order = -1
            self.pending_queue.append(request)
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

    async def _instance_execution_flow(self, instance: str, reqs: List[BaseRequest]):
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
                self.instance_busy = False

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
        try:
            target_url = f"http://{instance}/v1/{request.Service.Endpoint_type}"
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

        except asyncio.CancelledError:
            # 处理抢占或关闭
            if getattr(request.Service, "is_preempted", False) and not self.is_shutting_down:
                logging.info(f"🔄 [Bounced] Req {str(rid)[-4:]} preempted...")
            else:
                logging.debug(f"Req {rid} cancelled.")

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
            # 清理全局运行映射
            async with self.lock:
                self.running_tasks_map.pop(rid_key, None)

    async def shutdown(self):
        """优雅关闭"""
        self.is_shutting_down = True
        for task in self.worker_tasks:
            task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
