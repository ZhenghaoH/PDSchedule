import time
import asyncio
import heapq
import logging
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Set, Optional, Dict, Tuple
import aiohttp

from core.schemas.request import BaseRequest
from store.gateway.manager import GatewayStoreManager
global_storage = GatewayStoreManager()


# --- 数据包装类：用于存储调度计算的中间状态 ---
@dataclass
class SchedulableRequest:
    req: BaseRequest
    prompt_length: int = field(init=False)  # 输入提示词长度
    request_id: str = field(init=False)  # 请求唯一标识
    ttft_slo_sec: float = field(init=False)  # SLO要求（秒）
    deadline: float = field(init=False)  # 绝对截止时间戳
    prefill_deadline_cache: float = field(init=False, default=0.0)
    solo_time: float = field(init=False, default=0.0)  # 单独执行的预测耗时
    slack: float = field(init=False, default=0.0)  # 松弛度（越小越紧急）

    def __post_init__(self):
        # 初始化时自动完成单位转换和属性提取
        self.prompt_length = self.req.Prompt.token_length
        self.request_id = self.req.Request_ID
        self.ttft_slo_sec = self.req.Service.SLO_TTFT / 1000.0


# --- PSO粒子类：代表一种潜在的调度方案 ---
@dataclass
class Particle:
    def __init__(self, dim: int, bounds: Tuple[float, float], initial_position: Optional[List[float]] = None):
        self.dim = dim
        self.bounds = bounds
        # 位置向量：每个维度对应一个请求，值代表被分配的 Batch ID
        if initial_position:
            self.position = list(initial_position)
        else:
            self.position = [random.uniform(bounds[0], bounds[1]) for _ in range(dim)]

        # 速度向量：决定粒子在搜索空间中的移动方向
        self.velocity = [random.uniform(-1.0, 1.0) for _ in range(dim)]

        self.best_position = list(self.position)  # 个体历史最优位置
        self.best_score = float('inf')  # 个体历史最低惩罚值
        self.current_score = float('inf')  # 当前位置的惩罚值


# --- 核心指标：计算批次违约惩罚 ---
def calculate_batch_nnvp(batch_reqs: List[SchedulableRequest], finish_time: float, penalty_scale=100.0):
    """
    NNVP (Normalized Non-linear Violation Penalty):
    计算公式：Scale * (max(0, 延迟) / SLO)^2
    """
    total = 0.0
    for r in batch_reqs:
        lateness = max(0.0, finish_time - r.deadline)
        if lateness > 0:
            # 归一化违约程度：延迟时间 / SLO时长
            norm = lateness / max(0.001, r.ttft_slo_sec)
            # 使用平方放大严重超时的惩罚
            total += penalty_scale * (norm ** 2.0)
    return total


def calculate_batch_token_goodput(batch_reqs: List[SchedulableRequest], finish_time: float) -> int:
    """Return prompt tokens completed before each request's TTFT deadline."""
    goodput_tokens = 0
    for r in batch_reqs:
        if finish_time <= r.deadline:
            goodput_tokens += r.prompt_length
    return goodput_tokens


def calculate_batch_lateness_penalty(batch_reqs: List[SchedulableRequest], finish_time: float) -> float:
    """Small tie-breaker for plans with equal token-level goodput."""
    penalty = 0.0
    for r in batch_reqs:
        lateness = max(0.0, finish_time - r.deadline)
        if lateness > 0:
            penalty += lateness / max(0.001, r.ttft_slo_sec)
    return penalty


class SLOAwarePrefillScheduler:
    def __init__(self, storage=None):
        self.storage = storage if storage else global_storage
        self._prediction_cache: Dict[int, float] = {}

        # PSO 算法超参数
        self.pso_particles = 30  # 种群规模
        self.pso_iterations = 20  # 迭代次数
        self.w = 0.7  # 惯性权重
        self.c1 = 1.4  # 自我认知学习因子
        self.c2 = 1.4  # 社会经验学习因子

    def _get_instance_heap(self):
        """获取实例可用时间的最小堆，用于寻找最早空闲的GPU"""
        heap = []
        if not hasattr(self.storage, 'active_prefill_objs') or not self.storage.active_prefill_objs:
            return heap
        for ip in self.storage.active_prefill_objs:
            if not ip: continue
            avail_time = self.storage.get_predicted_availability(ip)
            heapq.heappush(heap, (avail_time, ip))
        return heap

    async def _get_prediction(self, session, batch_size, prompt_length):
        """异步请求预测服务器，带本地缓存以加速PSO搜索过程"""
        if batch_size == 0: return 0.0
        total_tokens = max(0, int(batch_size)) * max(0, int(prompt_length))
        key = total_tokens
        if key in self._prediction_cache: return self._prediction_cache[key]

        predict_func = None
        if hasattr(self.storage, "get_ttft_predictor"):
            predict_func = self.storage.get_ttft_predictor()
        if predict_func is not None:
            try:
                val = await predict_func(1, total_tokens)
                if val is not None and math.isfinite(val) and val > 0:
                    self._prediction_cache[key] = val
                    return val
            except Exception as exc:
                logging.warning(f"[SLOAwarePrefillScheduler] In-process TTFT predictor failed: {exc}")

        try:
            url = "http://localhost:9000/predict"
            async with session.post(url, json={"total_tokens": total_tokens}) as resp:
                if resp.status == 200:
                    val = (await resp.json()).get("predicted_ttft_seconds")
                    if val is not None and math.isfinite(val) and val > 0:
                        self._prediction_cache[key] = val
                        return val
        except Exception as exc:
            logging.debug(f"[SLOAwarePrefillScheduler] HTTP TTFT predictor unavailable: {exc}")
        # 无法连接时使用悲观线性预测作为兜底
        return 0.0002 * total_tokens

    # ==========================================
    # 核心评估逻辑：将PSO位置向量解码为批次方案并评分
    # ==========================================
    async def _evaluate_particle(
            self,
            session: aiohttp.ClientSession,
            requests: List[SchedulableRequest],
            position: List[float],
            start_time: float,
            verbose: bool = False
    ) -> Tuple[float, List[Tuple[List[SchedulableRequest], float, float]]]:
        """
        解码逻辑：
        1. position[i] 的整数部分代表 Batch ID。
        2. 按 Batch ID 从小到大依次送入 GPU 处理。
        """
        # 1. 将请求与其 position 值绑定
        indexed_requests = []
        for idx, val in enumerate(position):
            indexed_requests.append((val, requests[idx]))

        # 2. 严格按照 position 的值（Batch ID + 优先级偏移）进行全局排序
        # 这样 position 越小的请求不仅 Batch ID 靠前，在 Batch 内部也靠前
        indexed_requests.sort(key=lambda x: x[0])

        # 3. 按排序后的结果进行分组
        batches_map = defaultdict(list)
        for val, req in indexed_requests:
            batch_id = int(val)
            batches_map[batch_id].append(req)

        # 4. 保证批次按 ID 升序执行
        sorted_batch_ids = sorted(batches_map.keys())

        schedule_plan = []
        total_goodput_tokens = 0
        lateness_tie_breaker = 0.0
        latency_tie_breaker = 0.0
        current_time = start_time
        MAX_TOKENS = 10240  # vLLM Prefill 显存保护阈值
        LATENESS_WEIGHT = 0.001
        LATENCY_WEIGHT = 0.000001

        for bid in sorted_batch_ids:
            raw_batch = batches_map[bid]
            if not raw_batch: continue

            # --- 物理约束切分：如果PSO把太多请求塞进一个ID，按Token数二次切分 ---
            sub_batches = []
            current_sub = []
            current_sub_tok = 0

            for req in raw_batch:
                if current_sub_tok + req.prompt_length > MAX_TOKENS:
                    if current_sub: sub_batches.append(current_sub)
                    current_sub = [req]
                    current_sub_tok = req.prompt_length
                else:
                    current_sub.append(req)
                    current_sub_tok += req.prompt_length
            if current_sub: sub_batches.append(current_sub)

            # --- 模拟执行并计算代价 ---
            for batch in sub_batches:
                batch_tokens = sum(r.prompt_length for r in batch)
                t_pred = await self._get_prediction(session, 1, batch_tokens)
                if t_pred is None: t_pred = 0.1

                finish_time = current_time + t_pred
                goodput_tokens = calculate_batch_token_goodput(batch, finish_time)
                total_goodput_tokens += goodput_tokens
                lateness_penalty = calculate_batch_lateness_penalty(batch, finish_time)
                lateness_tie_breaker += lateness_penalty
                latency_tie_breaker += len(batch) * max(0.0, finish_time - start_time)

                # 记录该批次的详细情况
                schedule_plan.append((batch, finish_time, t_pred))

                if verbose:
                    ids = [str(r.request_id)[-4:] for r in batch]
                    lens = [r.prompt_length for r in batch]
                    # 获取该请求在原始 queue 中的索引位置，用于验证 FCFS
                    orig_indices = [next(i for i, req in enumerate(requests) if req.request_id == r.request_id) for r in
                                    batch]
                    slacks = [f"{r.slack:.3f}" for r in batch]

                    # print(f"      > Batch: size={len(batch)}, tokens={sum(lens)}, cost={cost:.2f}")
                    # print(f"        [Order Check] IDs:    {ids}")
                    # print(f"        [Order Check] Lens:   {lens}")
                    # print(f"        [Order Check] Slacks: {slacks}")
                    # print(f"        [Order Check] OrigIdx:{orig_indices}")
                    print(
                        f"      > Batch: size={len(batch)}, tokens={batch_tokens}, goodput={goodput_tokens}, "
                        f"dur={t_pred:.4f}s, lateness_penalty={lateness_penalty:.4f}, prompt_lens={lens}, IDs={ids}")

                current_time = finish_time  # 串行执行批次，更新时间轴

        score = (
            -float(total_goodput_tokens)
            + LATENESS_WEIGHT * lateness_tie_breaker
            + LATENCY_WEIGHT * latency_tie_breaker
        )
        return score, schedule_plan

    # --- 启发式解生成：给PSO一个高质量的起点 ---
    async def _generate_heuristic_particle(self, session, requests, sort_key, start_time):
        """模拟按某种排序贪心组批，生成对应的 Batch ID 分布"""
        sorted_indices = sorted(range(len(requests)), key=lambda i: sort_key(requests[i]))
        position = [0.0] * len(requests)
        curr_batch_idx = 0
        curr_batch_tokens = 0
        temp_batch = []

        for i in sorted_indices:
            req = requests[i]
            # 简单的贪心规则：每 batch 满 8 个请求或 32k tokens 就切分
            if curr_batch_tokens + req.prompt_length > 32000 or len(temp_batch) >= 100:
                curr_batch_idx += 1
                curr_batch_tokens = 0
                temp_batch = []

            position[i] = float(curr_batch_idx) + 0.5
            temp_batch.append(req)
            curr_batch_tokens += req.prompt_length
        return position

    # ==========================================
    # 主调度流程
    # ==========================================
    async def schedule(self, queue: List[BaseRequest], last_plan: List[BaseRequest] = None) -> List[BaseRequest]:
        t_now = time.time()
        self._prediction_cache.clear()

        if not queue: return []

        async with aiohttp.ClientSession() as session:
            print(f"\n[{'=' * 20} PSO-LST Scheduling Start (Q={len(queue)}) T={t_now:.3f} {'=' * 20}]")

            # 1. 预处理与请求包装
            reqs = [SchedulableRequest(req=r) for r in queue]

            # 并发获取所有请求单独运行的时间（Solo Time），用于计算 Slack
            solo_tasks = [self._get_prediction(session, 1, r.prompt_length) for r in reqs]
            compute_times = await asyncio.gather(*solo_tasks)

            for i, r in enumerate(reqs):
                # 记录到达时间，计算绝对截止线
                arrival_time = getattr(r.req.Service, "arrival_time", t_now)
                r.deadline = arrival_time + r.ttft_slo_sec
                r.prefill_deadline_cache = r.deadline
                r.solo_time = compute_times[i] if compute_times[i] else 0.1
                # Slack: 距离截止还有多久（减去预测计算耗时）
                r.slack = arrival_time + r.ttft_slo_sec - t_now - r.solo_time

            # 确定当前可用的 GPU 资源
            instance_heap = self._get_instance_heap()
            if not instance_heap: return []
            avail_time, target_ip = heapq.heappop(instance_heap)
            start_time = max(t_now, avail_time)
            print(f"[Env] Instance: {target_ip} | Available at: {avail_time:.3f} | Actual Start: {start_time:.3f}")

            # 2. 启发式种子注入
            print("\n--- 1. Evaluating Initial Heuristic Baselines ---")
            dim = len(reqs)
            bounds = (0.0, float(dim) + 1.0)
            swarm: List[Particle] = []

            # 专家 0: 上一次调度结果
            if last_plan:
                # 1. 建立 ID -> BatchOrder 的映射
                history_map = {r.Request_ID: r.Task.batch_order for r in last_plan}

                # 2. 找到上一次计划中最大的批次编号
                # 如果旧请求都处理完了，则从 0 开始
                max_prev_batch = 0
                if history_map:
                    # 过滤掉可能存在的无效值，取最大批次
                    valid_batches = [b for b in history_map.values() if b > 0]
                    max_prev_batch = max(valid_batches) if valid_batches else 0

                warm_pos = [0.0] * dim
                found_history = False
                new_req_increment = 0  # 用于给新请求分配微小的偏移量，保持内部顺序

                for i, r in enumerate(reqs):
                    if r.request_id in history_map:
                        # 旧请求：保持原有的批次号，+0.5 居中
                        warm_pos[i] = float(history_map[r.request_id]) + 0.5
                        found_history = True
                    else:
                        # 新请求：紧跟在旧请求最后一个批次之后
                        # 使用 max_prev_batch + 1 作为基准
                        # 增加一个极其微小的偏移 (new_req_increment * 0.01)
                        # 这确保了在 _evaluate_particle 的排序逻辑中，新请求按到达顺序排列
                        warm_pos[i] = float(max_prev_batch + 1) + (new_req_increment * 0.01)
                        new_req_increment += 1

                if found_history:
                    swarm.append(Particle(dim, bounds, warm_pos))
                    print(f"   [Baseline: History Seed] (Refined: Follow at Batch {max_prev_batch + 1})")

            # 专家 1: LST (最少松弛时间优先)
            pos_lst = await self._generate_heuristic_particle(session, reqs, lambda r: r.slack, start_time)
            print("   [Baseline: LST]")
            await self._evaluate_particle(session, reqs, pos_lst, start_time, verbose=True)
            swarm.append(Particle(dim, bounds, pos_lst))

            # 专家 2: SJF (短作业优先)
            pos_sjf = await self._generate_heuristic_particle(session, reqs, lambda r: r.prompt_length, start_time)
            print("   [Baseline: SJF]")
            await self._evaluate_particle(session, reqs, pos_sjf, start_time, verbose=True)
            swarm.append(Particle(dim, bounds, pos_sjf))

            # 专家 3: FCFS (先来先服务 / 保持输入原序)
            pos_fcfs = await self._generate_heuristic_particle(session, reqs, lambda r: 0, start_time)
            print("   [Baseline: FCFS]")
            await self._evaluate_particle(session, reqs, pos_fcfs, start_time, verbose=True)
            swarm.append(Particle(dim, bounds, pos_fcfs))

            # 填充随机粒子以增强搜索多样性
            while len(swarm) < self.pso_particles:
                swarm.append(Particle(dim, bounds))

            # 3. PSO 迭代搜索
            global_best_score = float('inf')
            global_best_plan = []

            print(f"\n--- 2. Starting PSO Iterative Search ({self.pso_iterations} iterations) ---")
            for iter_idx in range(self.pso_iterations):
                # 确定当前的全局最优解位置
                if iter_idx > 0:
                    g_best_pos = min(swarm, key=lambda x: x.best_score).best_position
                else:
                    g_best_pos = swarm[0].best_position

                for p in swarm:
                    # 仅在评估过程中计算，不开启 verbose
                    score, plan = await self._evaluate_particle(session, reqs, p.position, start_time)

                    if score < p.best_score:
                        p.best_score = score
                        p.best_position = list(p.position)

                    if score < global_best_score:
                        global_best_score = score
                        global_best_plan = plan
                        print(f"   >>> Iter {iter_idx}: New Global Best Token Goodput Score = {score:.4f}")
                        # 发现新的最优方案时，打印其详细拆解
                        await self._evaluate_particle(session, reqs, p.position, start_time, verbose=True)

                    # --- 更新粒子位置与速度 (PSO 公式) ---
                    for d in range(dim):
                        r1, r2 = random.random(), random.random()
                        # v(t+1) = w*v(t) + c1*r1*(pBest-x) + c2*r2*(gBest-x)
                        p.velocity[d] = (self.w * p.velocity[d] +
                                         self.c1 * r1 * (p.best_position[d] - p.position[d]) +
                                         self.c2 * r2 * (g_best_pos[d] - p.position[d]))
                        # 速度截断
                        p.velocity[d] = max(-2.0, min(2.0, p.velocity[d]))
                        p.position[d] += p.velocity[d]
                        # 边界约束
                        p.position[d] = max(bounds[0], min(bounds[1], p.position[d]))

            # 4. 确认结果
            if not global_best_plan: return []

            print(f"\n[Result] PSO Final Best Token Goodput Score: {global_best_score:.4f}")
            final_requests = []
            real_batch_order = 1
            for batch_reqs, f_time, dur in global_best_plan:
                rid_strs = [str(r.request_id)[-4:] for r in batch_reqs]
                batch_tokens = sum(r.prompt_length for r in batch_reqs)
                goodput_tokens = calculate_batch_token_goodput(batch_reqs, f_time)
                print(
                    f"  -> Committed Batch {real_batch_order}: Size={len(batch_reqs)} | "
                    f"Tokens={batch_tokens} | Goodput={goodput_tokens} | "
                    f"Dur={dur:.3f}s | Finish={f_time:.3f} | IDs={rid_strs}")

                # 更新全局时间轴，预占 GPU 资源
                self.storage.update_predicted_availability(target_ip, f_time)

                for r in batch_reqs:
                    r.req.Task.prefill_instance = target_ip
                    r.req.Task.batch_order = real_batch_order
                    final_requests.append(r.req)
                real_batch_order += 1

            print("=" * 80)
            return final_requests

    def update_instance_feedback(self, instance_ip: str, actual_finish_time: float):
        """反馈校准接口：当vLLM真实执行完毕后，调用此方法更新全局可用时间"""
        self.storage.update_real_availability(instance_ip, actual_finish_time)
