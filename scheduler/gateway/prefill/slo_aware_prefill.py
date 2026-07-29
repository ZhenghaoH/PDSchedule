import asyncio
import heapq
import logging
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from core.schemas.request import BaseRequest
from store.gateway.manager import GatewayStoreManager


global_storage = GatewayStoreManager()


@dataclass
class SchedulableRequest:
    req: BaseRequest
    prompt_length: int = field(init=False)
    request_id: str = field(init=False)
    ttft_slo_sec: float = field(init=False)
    deadline: float = field(init=False)
    prefill_deadline_cache: float = field(init=False, default=0.0)
    solo_time: float = field(init=False, default=0.0)
    slack: float = field(init=False, default=0.0)

    def __post_init__(self):
        self.prompt_length = self.req.Prompt.token_length
        self.request_id = self.req.Request_ID
        self.ttft_slo_sec = self.req.Service.SLO_TTFT / 1000.0


class Particle:
    def __init__(
        self,
        dim: int,
        bounds: Tuple[float, float],
        initial_position: Optional[List[float]] = None,
    ):
        self.dim = dim
        self.bounds = bounds
        self.position = (
            list(initial_position)
            if initial_position is not None
            else [random.uniform(bounds[0], bounds[1]) for _ in range(dim)]
        )
        self.velocity = [random.uniform(-1.0, 1.0) for _ in range(dim)]
        self.best_position = list(self.position)
        self.best_score = float("inf")


def calculate_batch_token_goodput(
    batch_reqs: List[SchedulableRequest],
    finish_time: float,
) -> int:
    return sum(
        request.prompt_length
        for request in batch_reqs
        if finish_time <= request.deadline
    )


def calculate_batch_lateness_penalty(
    batch_reqs: List[SchedulableRequest],
    finish_time: float,
) -> float:
    penalty = 0.0
    for request in batch_reqs:
        lateness = max(0.0, finish_time - request.deadline)
        if lateness > 0:
            penalty += lateness / max(0.001, request.ttft_slo_sec)
    return penalty


MAX_PREFILL_WAITING_TOKENS = 4096
LATENESS_WEIGHT = 0.001
LATENCY_WEIGHT = 0.000001


class SLOAwarePrefillScheduler:
    """Request-level PSO for the rolling prefill path.

    This strategy is intentionally separated from the legacy ``pso`` path.
    It treats ``inflight_prefill`` as the ground-truth unfinished prefill
    prefix. A request leaves that prefix only after sync.py receives the first
    returned token, whose callback is logged as "Not counting as TTFT".
    """

    def __init__(self, storage=None):
        self.storage = storage if storage else global_storage
        self._prediction_cache: Dict[int, float] = {}
        self.pso_particles = 30
        self.pso_iterations = 20
        self.w = 0.7
        self.c1 = 1.4
        self.c2 = 1.4
        self.max_num_batched_tokens = MAX_PREFILL_WAITING_TOKENS

    def _get_instance_heap(self):
        heap = []
        if not self.storage.active_prefill_objs:
            return heap
        for instance_ip in self.storage.active_prefill_objs:
            if instance_ip:
                heapq.heappush(
                    heap,
                    (
                        self.storage.get_predicted_availability(instance_ip),
                        instance_ip,
                    ),
                )
        return heap

    async def _get_prediction(self, session, batch_size, prompt_length):
        if batch_size == 0:
            return 0.0

        total_tokens = max(0, int(batch_size)) * max(0, int(prompt_length))
        if total_tokens in self._prediction_cache:
            return self._prediction_cache[total_tokens]

        predict_func = self.storage.get_ttft_predictor()
        if predict_func is not None:
            try:
                value = await predict_func(1, total_tokens)
                if value is not None and math.isfinite(value) and value > 0:
                    self._prediction_cache[total_tokens] = value
                    return value
            except Exception as exc:
                logging.warning(
                    "[SLOAwarePrefillScheduler] In-process TTFT predictor failed: %s",
                    exc,
                )

        try:
            async with session.post(
                "http://localhost:9000/predict",
                json={"total_tokens": total_tokens},
            ) as response:
                if response.status == 200:
                    value = (await response.json()).get("predicted_ttft_seconds")
                    if value is not None and math.isfinite(value) and value > 0:
                        self._prediction_cache[total_tokens] = value
                        return value
        except Exception as exc:
            logging.debug(
                "[SLOAwarePrefillScheduler] HTTP TTFT predictor unavailable: %s",
                exc,
            )

        return 0.0002 * total_tokens

    def _inflight_tokens(self, inflight_prefill: Optional[List[Any]]) -> int:
        total = 0
        for meta in inflight_prefill or []:
            req = meta.get("req") if isinstance(meta, dict) else None
            if req is None:
                continue
            total += max(0, int(getattr(req.Prompt, "token_length", 0) or 0))
        return total

    async def _estimate_inflight_finish(
        self,
        session: aiohttp.ClientSession,
        inflight_prefill: Optional[List[Any]],
        now: float,
        storage_availability: float,
    ) -> float:
        """Return a conservative finish time for already dispatched prefill.

        Prediction metadata is used only as a planning estimate. Completion is
        still determined by sync.py removing entries from inflight_prefill after
        the first returned token callback.
        """
        entries = []
        for meta in inflight_prefill or []:
            if not isinstance(meta, dict):
                continue
            req = meta.get("req")
            if req is None:
                continue
            group_id = meta.get("dispatch_group_id")
            if group_id is None:
                group_id = f"rid:{getattr(req, 'Request_ID', id(req))}"
            entries.append(
                (
                    meta.get("sent_time", now),
                    group_id,
                    meta.get("request_order", 0),
                    req,
                    meta,
                )
            )

        if not entries:
            return max(now, storage_availability)

        grouped: Dict[Any, List[Tuple[float, int, BaseRequest, Dict[str, Any]]]] = defaultdict(list)
        for sent_time, group_id, request_order, req, meta in entries:
            grouped[group_id].append((sent_time, request_order, req, meta))

        groups = []
        for group_id, items in grouped.items():
            items.sort(key=lambda x: (x[0], x[1]))
            groups.append((items[0][0], group_id, items))
        groups.sort(key=lambda x: x[0])

        cursor = max(now, storage_availability)
        for _, _, items in groups:
            batch_tokens = sum(
                max(0, int(getattr(req.Prompt, "token_length", 0) or 0))
                for _, _, req, _ in items
            )
            predicted_finishes = []
            for _, _, _, meta in items:
                for key in (
                    "dispatch_batch_predicted_finish",
                    "dispatch_predicted_finish",
                    "planned_finish",
                ):
                    val = meta.get(key)
                    if isinstance(val, (int, float)) and val > 0:
                        predicted_finishes.append(float(val))
            if predicted_finishes:
                cursor = max(cursor, max(predicted_finishes))
            else:
                duration = await self._get_prediction(session, 1, batch_tokens)
                cursor = cursor + max(0.0, duration or 0.0)
        return max(now, cursor)

    def _split_ordered_requests(
        self,
        ordered_requests: List[SchedulableRequest],
        first_batch_budget: Optional[int] = None,
    ) -> List[List[SchedulableRequest]]:
        batches: List[List[SchedulableRequest]] = []
        current: List[SchedulableRequest] = []
        current_tokens = 0
        current_budget = first_batch_budget or self.max_num_batched_tokens
        if current_budget <= 0:
            current_budget = self.max_num_batched_tokens

        for req in ordered_requests:
            req_tokens = max(0, int(req.prompt_length))
            over_budget = current_tokens + req_tokens > current_budget
            if current and over_budget:
                batches.append(current)
                current = []
                current_tokens = 0
                current_budget = self.max_num_batched_tokens

            current.append(req)
            current_tokens += req_tokens

            if current_tokens >= current_budget:
                batches.append(current)
                current = []
                current_tokens = 0
                current_budget = self.max_num_batched_tokens

        if current:
            batches.append(current)
        return batches

    async def _evaluate_particle(
        self,
        session: aiohttp.ClientSession,
        requests: List[SchedulableRequest],
        position: List[float],
        start_time: float,
        first_batch_budget: Optional[int] = None,
        verbose: bool = False,
    ) -> Tuple[float, List[Tuple[List[SchedulableRequest], float, float]]]:
        indexed_requests = [(position[idx], requests[idx]) for idx in range(len(requests))]
        indexed_requests.sort(key=lambda x: x[0])

        batches_map = defaultdict(list)
        for val, req in indexed_requests:
            batches_map[int(val)].append(req)

        ordered: List[SchedulableRequest] = []
        for batch_id in sorted(batches_map.keys()):
            ordered.extend(batches_map[batch_id])

        physical_batches = self._split_ordered_requests(
            ordered,
            first_batch_budget=first_batch_budget,
        )

        schedule_plan: List[Tuple[List[SchedulableRequest], float, float]] = []
        total_goodput_tokens = 0
        lateness_tie_breaker = 0.0
        latency_tie_breaker = 0.0
        current_time = start_time

        for batch in physical_batches:
            batch_tokens = sum(r.prompt_length for r in batch)
            t_pred = await self._get_prediction(session, 1, batch_tokens)
            if t_pred is None:
                t_pred = 0.1
            t_pred = max(0.0, float(t_pred))
            finish_time = current_time + t_pred

            goodput_tokens = calculate_batch_token_goodput(batch, finish_time)
            total_goodput_tokens += goodput_tokens
            lateness_penalty = calculate_batch_lateness_penalty(batch, finish_time)
            lateness_tie_breaker += lateness_penalty
            latency_tie_breaker += len(batch) * max(0.0, finish_time - start_time)
            schedule_plan.append((batch, finish_time, t_pred))

            if verbose:
                logging.info(
                    "[pso_new] batch size=%d tokens=%d goodput=%d dur=%.4fs finish=%.6f ids=%s",
                    len(batch),
                    batch_tokens,
                    goodput_tokens,
                    t_pred,
                    finish_time,
                    [str(r.request_id)[-4:] for r in batch],
                )

            current_time = finish_time

        score = (
            -float(total_goodput_tokens)
            + LATENESS_WEIGHT * lateness_tie_breaker
            + LATENCY_WEIGHT * latency_tie_breaker
        )
        return score, schedule_plan

    async def _generate_heuristic_particle(self, session, requests, sort_key, start_time):
        sorted_indices = sorted(range(len(requests)), key=lambda i: sort_key(requests[i]))
        position = [0.0] * len(requests)
        curr_batch_idx = 0
        curr_batch_tokens = 0

        for i in sorted_indices:
            req = requests[i]
            req_tokens = max(0, int(req.prompt_length))
            if curr_batch_tokens > 0 and curr_batch_tokens + req_tokens > self.max_num_batched_tokens:
                curr_batch_idx += 1
                curr_batch_tokens = 0

            position[i] = float(curr_batch_idx) + 0.5
            curr_batch_tokens += req_tokens
        return position

    def _group_inflight_by_instance(
        self,
        inflight_prefill: Optional[List[Any]],
        active_ips: set[str],
    ) -> Dict[str, List[Any]]:
        grouped: Dict[str, List[Any]] = defaultdict(list)
        for meta in inflight_prefill or []:
            if not isinstance(meta, dict):
                continue
            req = meta.get("req")
            if req is None:
                continue
            ip = getattr(getattr(req, "Task", None), "prefill_instance", "")
            if ip in active_ips:
                grouped[ip].append(meta)
        return grouped

    def _assign_pending_to_instances(
        self,
        queue: List[BaseRequest],
        active_ips: set[str],
        availability_by_ip: Dict[str, float],
        inflight_load_by_ip: Dict[str, int],
        last_plan: Optional[List[BaseRequest]],
    ) -> Dict[str, List[BaseRequest]]:
        last_instance_by_id = {}
        for req in last_plan or []:
            ip = getattr(req.Task, "prefill_instance", "")
            if ip in active_ips:
                last_instance_by_id[str(req.Request_ID)] = ip

        pending_by_instance: Dict[str, List[BaseRequest]] = defaultdict(list)
        virtual_load = {ip: int(inflight_load_by_ip.get(ip, 0)) for ip in active_ips}

        for req in queue:
            rid = str(req.Request_ID)
            sticky_ip = last_instance_by_id.get(rid)
            current_ip = getattr(req.Task, "prefill_instance", "")
            if not sticky_ip and current_ip in active_ips:
                sticky_ip = current_ip

            if sticky_ip in active_ips:
                target_ip = sticky_ip
            else:
                target_ip = min(
                    active_ips,
                    key=lambda ip: (
                        virtual_load.get(ip, 0),
                        availability_by_ip.get(ip, 0.0),
                        ip,
                    ),
                )

            pending_by_instance[target_ip].append(req)
            virtual_load[target_ip] = virtual_load.get(target_ip, 0) + max(
                0,
                int(getattr(req.Prompt, "token_length", 0) or 0),
            )

        return pending_by_instance

    async def _schedule_single_instance(
        self,
        session: aiohttp.ClientSession,
        queue: List[BaseRequest],
        target_ip: str,
        avail_time: float,
        last_plan: Optional[List[BaseRequest]],
        inflight_prefill: Optional[List[Any]],
        t_now: float,
        schedule_id: str,
        plan_position_start: int,
    ) -> Tuple[List[BaseRequest], int, float, int, int, float]:
        if not queue:
            return [], plan_position_start, 0.0, 0, 0, max(t_now, avail_time)

        reqs = [SchedulableRequest(req=r) for r in queue]
        solo_tasks = [self._get_prediction(session, 1, r.prompt_length) for r in reqs]
        compute_times = await asyncio.gather(*solo_tasks)

        for i, r in enumerate(reqs):
            arrival_time = getattr(r.req.Service, "arrival_time", t_now)
            r.deadline = arrival_time + r.ttft_slo_sec
            r.prefill_deadline_cache = r.deadline
            r.solo_time = compute_times[i] if compute_times[i] else 0.1
            r.slack = r.deadline - t_now - r.solo_time

        inflight_finish = await self._estimate_inflight_finish(
            session,
            inflight_prefill,
            t_now,
            avail_time,
        )
        start_time = max(t_now, avail_time, inflight_finish)

        inflight_tokens = self._inflight_tokens(inflight_prefill)
        available_waiting_tokens = max(0, self.max_num_batched_tokens - inflight_tokens)
        first_batch_budget = available_waiting_tokens if available_waiting_tokens > 0 else None
        first_batch_order = 1 if available_waiting_tokens > 0 or not inflight_prefill else 2

        dim = len(reqs)
        bounds = (0.0, float(dim) + 1.0)
        swarm: List[Particle] = []

        if last_plan:
            history_map = {str(r.Request_ID): getattr(r.Task, "batch_order", 0) for r in last_plan}
            valid_batches = [b for b in history_map.values() if b > 0]
            max_prev_batch = max(valid_batches) if valid_batches else 0
            warm_pos = [0.0] * dim
            found_history = False
            new_req_increment = 0
            for i, r in enumerate(reqs):
                rid = str(r.request_id)
                if rid in history_map:
                    warm_pos[i] = float(history_map[rid]) + 0.5
                    found_history = True
                else:
                    warm_pos[i] = float(max_prev_batch + 1) + (new_req_increment * 0.01)
                    new_req_increment += 1
            if found_history:
                swarm.append(Particle(dim, bounds, warm_pos))

        pos_lst = await self._generate_heuristic_particle(session, reqs, lambda r: r.slack, start_time)
        pos_sjf = await self._generate_heuristic_particle(session, reqs, lambda r: r.prompt_length, start_time)
        pos_fcfs = await self._generate_heuristic_particle(session, reqs, lambda r: 0, start_time)
        swarm.extend(
            [
                Particle(dim, bounds, pos_lst),
                Particle(dim, bounds, pos_sjf),
                Particle(dim, bounds, pos_fcfs),
            ]
        )
        while len(swarm) < self.pso_particles:
            swarm.append(Particle(dim, bounds))

        global_best_score = float("inf")
        global_best_plan: List[Tuple[List[SchedulableRequest], float, float]] = []

        for iter_idx in range(self.pso_iterations):
            if iter_idx > 0:
                g_best_pos = min(swarm, key=lambda x: x.best_score).best_position
            else:
                g_best_pos = swarm[0].best_position

            for particle in swarm:
                score, plan = await self._evaluate_particle(
                    session,
                    reqs,
                    particle.position,
                    start_time,
                    first_batch_budget=first_batch_budget,
                )

                if score < particle.best_score:
                    particle.best_score = score
                    particle.best_position = list(particle.position)

                if score < global_best_score:
                    global_best_score = score
                    global_best_plan = plan

                for d in range(dim):
                    r1, r2 = random.random(), random.random()
                    particle.velocity[d] = (
                        self.w * particle.velocity[d]
                        + self.c1 * r1 * (particle.best_position[d] - particle.position[d])
                        + self.c2 * r2 * (g_best_pos[d] - particle.position[d])
                    )
                    particle.velocity[d] = max(-2.0, min(2.0, particle.velocity[d]))
                    particle.position[d] += particle.velocity[d]
                    particle.position[d] = max(bounds[0], min(bounds[1], particle.position[d]))

        if not global_best_plan:
            return [], plan_position_start, global_best_score, inflight_tokens, available_waiting_tokens, start_time

        final_requests: List[BaseRequest] = []
        real_batch_order = first_batch_order
        plan_position = plan_position_start

        for batch_reqs, finish_time, duration in global_best_plan:
            batch_tokens = sum(r.prompt_length for r in batch_reqs)
            batch_start = finish_time - duration
            batch_is_full = batch_tokens >= self.max_num_batched_tokens

            for step_order, sched_req in enumerate(batch_reqs):
                req = sched_req.req
                req.Task.prefill_instance = target_ip
                req.Task.batch_order = real_batch_order
                req.Task.slo_schedule_id = schedule_id
                req.Task.slo_planned_batch_order = real_batch_order
                req.Task.slo_planned_step_order = step_order
                req.Task.slo_planned_start = batch_start
                req.Task.slo_planned_finish = finish_time
                req.Task.slo_planned_batch_finish = finish_time
                req.Task.slo_planned_duration = duration
                req.Task.slo_batch_tokens = batch_tokens
                req.Task.slo_batch_is_full = batch_is_full
                req.Task.slo_ready_to_dispatch = True
                req.Task.slo_plan_position = plan_position
                final_requests.append(req)
                plan_position += 1

            real_batch_order += 1

        return (
            final_requests,
            plan_position,
            global_best_score,
            inflight_tokens,
            available_waiting_tokens,
            start_time,
        )

    async def schedule(
        self,
        queue: List[BaseRequest],
        last_plan: List[BaseRequest] = None,
        inflight_prefill: List[Any] = None,
    ) -> List[BaseRequest]:
        t_now = time.time()
        self._prediction_cache.clear()
        if not queue:
            return []

        instance_heap = self._get_instance_heap()
        if not instance_heap:
            return []

        availability_by_ip = {ip: avail_time for avail_time, ip in instance_heap}
        active_ips = set(availability_by_ip.keys())
        inflight_by_instance = self._group_inflight_by_instance(
            inflight_prefill,
            active_ips,
        )
        inflight_load_by_ip = {
            ip: self._inflight_tokens(inflight_by_instance.get(ip, []))
            for ip in active_ips
        }
        pending_by_instance = self._assign_pending_to_instances(
            queue,
            active_ips,
            availability_by_ip,
            inflight_load_by_ip,
            last_plan,
        )

        last_plan_by_instance: Dict[str, List[BaseRequest]] = defaultdict(list)
        for req in last_plan or []:
            ip = getattr(req.Task, "prefill_instance", "")
            if ip in active_ips:
                last_plan_by_instance[ip].append(req)

        schedule_id = f"pso_new-{int(t_now * 1000)}"
        final_requests: List[BaseRequest] = []
        plan_position = 0

        async with aiohttp.ClientSession() as session:
            for target_ip in sorted(
                pending_by_instance.keys(),
                key=lambda ip: (
                    inflight_load_by_ip.get(ip, 0),
                    availability_by_ip.get(ip, 0.0),
                    ip,
                ),
            ):
                scheduled, plan_position, score, inflight_tokens, available_waiting_tokens, start_time = (
                    await self._schedule_single_instance(
                        session=session,
                        queue=pending_by_instance[target_ip],
                        target_ip=target_ip,
                        avail_time=availability_by_ip.get(target_ip, 0.0),
                        last_plan=last_plan_by_instance.get(target_ip, []),
                        inflight_prefill=inflight_by_instance.get(target_ip, []),
                        t_now=t_now,
                        schedule_id=schedule_id,
                        plan_position_start=plan_position,
                    )
                )
                final_requests.extend(scheduled)
                logging.info(
                    "[pso_new] scheduled instance=%s queue=%d inflight_tokens=%d "
                    "available_waiting_tokens=%d start=%.6f score=%.4f",
                    target_ip,
                    len(pending_by_instance[target_ip]),
                    inflight_tokens,
                    available_waiting_tokens,
                    start_time,
                    score,
                )

        final_requests.sort(
            key=lambda req: (
                getattr(req.Task, "batch_order", 9999),
                getattr(req.Task, "slo_plan_position", 999999),
                getattr(req.Service, "arrival_time", 0.0),
            )
        )
        logging.info(
            "[pso_new] multi-instance scheduled queue=%d instances=%s loads=%s",
            len(queue),
            sorted(pending_by_instance.keys()),
            {ip: inflight_load_by_ip.get(ip, 0) for ip in sorted(active_ips)},
        )
        return final_requests
