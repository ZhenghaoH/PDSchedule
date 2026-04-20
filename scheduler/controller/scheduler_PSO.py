# 文件路径: scheduler/scheduler_pso.py

import time
import asyncio
import random
import numpy as np
import sys
import os
import math
from typing import List, Dict, Tuple, Optional

from store.controller.pool_base import PrefillPool, DecodePool
from store.controller.network import Network

import bisect
from collections import deque
from scipy.stats import rankdata


class RequestRanker:
    def __init__(self, window_size=1000):
        # 使用双端队列保存原始顺序，用于移除过期数据
        self.window_size = window_size
        self.history_queue = deque()
        # 使用列表保存排序后的数据，用于快速计算 Rank
        self.sorted_values = []

        initial_data = list(range(0, 2048 + 1, 64))
        for val in initial_data:
            self.history_queue.append(val)
            self.sorted_values.append(val)

    def update(self, value: int):
        """更新历史数据，保持窗口大小"""
        # 1. 移除旧数据
        if len(self.history_queue) >= self.window_size:
            old_val = self.history_queue.popleft()
            # 从有序列表中移除 (O(N)操作，但 N=1000 时极快)
            # 实际生产中可用 SortedList 优化，这里用标准库演示
            idx = bisect.bisect_left(self.sorted_values, old_val)
            if idx < len(self.sorted_values) and self.sorted_values[idx] == old_val:
                self.sorted_values.pop(idx)

        # 2. 添加新数据
        self.history_queue.append(value)
        bisect.insort(self.sorted_values, value)

    def get_rank(self, value: int) -> float:
        """
        计算当前值在历史数据中的百分位 (0.0 ~ 1.0)
        """
        if not self.sorted_values:
            return 0.5  # 默认中间值

        # 查找插入位置
        idx = bisect.bisect_left(self.sorted_values, value)

        # 计算百分位
        rank = idx / len(self.sorted_values)

        # 稍微平滑一下，避免 0.0 和 1.0
        return min(max(rank, 0.01), 0.99)


class PSO_Scheduler:
    def __init__(self,
                 # PSO 超参数
                 num_particles: int = 20,
                 max_iterations: int = 15,
                 # 权重参数
                 weights: Dict[str, float] = None):

        # PSO 参数
        self.num_particles = num_particles
        self.max_iterations = max_iterations
        self.w_inertia = 0.5
        self.c1 = 1.4
        self.c2 = 1.4

        # 适应度权重
        self.weights = weights if weights else {
            'ttft': 5.0, 'tpot': 0.1, 'load': 0.5
        }

        self.req_ranker = RequestRanker(window_size=2000)

    # --- 新增：计算节点排名的辅助函数 ---
    def _calculate_pool_ranks(self, pools: List) -> Dict[int, float]:
        """
        计算每个节点的算力/带宽排名 (Percentile)。
        对于能力相同的节点，赋予相同的平均排名 (method='average')。
        返回: {pool_id: rank_score(0~1)}
        """
        if not pools:
            return {}

        # 1. 提取能力值
        # 注意：保持列表顺序以便后续 zip 映射
        pool_ids = [p.pool_id for p in pools]
        capacities = [getattr(p, 'flops', getattr(p, 'mem_bandwidth', 0)) for p in pools]

        # 2. 计算排名 (1-based index)
        # method='average': [100, 200, 200, 300] -> [1.0, 2.5, 2.5, 4.0]
        ranks = rankdata(capacities, method='average')

        total = len(pools)
        rank_map = {}

        # 3. 归一化到 0~1
        for pid, r in zip(pool_ids, ranks):
            # 公式: (Rank - 0.5) / N
            # 解释: r 是 1-based 的，减 0.5 等价于之前的 (i + 0.5) 逻辑
            # 例如 r=1.0 -> (1-0.5)/N = 0.5/N (即最弱节点的中心位置)
            norm_rank = (r - 0.5) / total
            rank_map[pid] = norm_rank

        # print(f"Pool Ranks: {rank_map}")
        return rank_map

    def _calculate_fitness(self,
                           particle_pos: np.ndarray,
                           token_length: int,
                           slo_ttft: float,
                           slo_tpot: float,
                           compress_factor: float,
                           prefill_pools: List[PrefillPool],
                           decode_pools: List[DecodePool],
                           network_map: Dict[Tuple[int, int], Network],
                           # 新增：预计算好的节点排名 Map
                           req_rank_score: float,
                           p_rank_map: Dict[int, float],
                           d_rank_map: Dict[int, float]) -> float:

        # SLO 基准值
        BASE_SLO_TTFT = 1500.0  # ms
        BASE_SLO_TPOT = 50.0  # ms

        # 权重配置 (Modified Weights)
        A = 2.0  # Mismatch 权重 (保持不变)
        B = 2.0  # Load 权重 (从 1.0 提高到 2.0，配合指数函数增强负载敏感度)

        W1 = 1.0  # TTFT 内部权重
        W2 = 1.0  # TPOT 内部权重

        # --- 1. 离散化 ---
        num_p = len(prefill_pools)
        num_d = len(decode_pools)
        p_idx = int(np.floor(particle_pos[0])) % num_p
        d_idx = int(np.floor(particle_pos[1])) % num_d

        p_node = prefill_pools[p_idx]
        d_node = decode_pools[d_idx]

        # --- 2. 核心计算：CDF 匹配与对数归一化 ---

        # 2.1 请求归一化 (Logarithmic Scaling)
        norm_req_rank = req_rank_score

        # 2.2 节点能力 (直接查表获取 Rank)
        # 如果 Map 中找不到（极其罕见），默认给 0.5
        norm_p_cap = p_rank_map.get(p_node.pool_id, 0.5)
        norm_d_cap = d_rank_map.get(d_node.pool_id, 0.5)

        # =================================================================
        # 第一部分：TTFT Mismatch
        # =================================================================

        # A. 紧迫度放大
        safe_slo_ttft = max(slo_ttft, 1.0)
        gamma_ttft = max(1.0, BASE_SLO_TTFT / safe_slo_ttft)

        # B. 等效请求 Rank
        effective_req_p = norm_req_rank * gamma_ttft

        # C. Prefill 算力错配 (Rank Distance)
        diff_p = norm_p_cap - effective_req_p
        if diff_p >= 0:
            cost_p_mismatch = diff_p * 5.0
        else:
            cost_p_mismatch = abs(diff_p) * 10.0  # 严惩能力不足

        # D. 网络错配
        net = network_map.get((p_node.pool_id, d_node.pool_id))
        if not net:
            return 1e9

        data_size = token_length * compress_factor
        net_time_ms = net.rtt + (data_size / (net.bandwidth + 1e-6))
        network_mismatch = min(net_time_ms / safe_slo_ttft, 1.0)

        ttft_slo_mismatch = cost_p_mismatch + network_mismatch

        # =================================================================
        # 第二部分：TPOT Mismatch
        # =================================================================

        safe_slo_tpot = max(slo_tpot, 0.1)
        gamma_tpot = max(1.0, BASE_SLO_TPOT / safe_slo_tpot)
        effective_req_d = norm_req_rank * gamma_tpot

        diff_d = norm_d_cap - effective_req_d
        if diff_d >= 0:
            cost_d_mismatch = diff_d * 5.0
        else:
            cost_d_mismatch = abs(diff_d) * 10.0

        tpot_slo_mismatch = cost_d_mismatch

        # =================================================================
        # 第三部分：Load Balance (核心修改部分)
        # =================================================================

        # 使用指数惩罚函数代替原本的二次方
        # 假设 load 在 0.0-1.0 之间
        # exp(4 * 0.0) = 1.0
        # exp(4 * 0.5) = 7.39
        # exp(4 * 0.8) = 24.5
        # exp(4 * 1.0) = 54.6 (极强的惩罚，足以覆盖 SLO Mismatch)
        # 这样当负载超过 80% 时，调度器会极力避免该节点

        load_alpha = 4.0

        # 限制指数输入，防止溢出（虽然归一化后一般不会）
        safe_p_load = min(p_node.current_load, 5.0)
        # safe_d_load = min(d_node.current_load, 5.0)
        safe_d_load = 0

        p_load_score = np.exp(load_alpha * safe_p_load)
        d_load_score = np.exp(load_alpha * safe_d_load)

        load_balance_score = (p_load_score + d_load_score) / 2.0

        # =================================================================
        # 最终 Fitness
        # =================================================================

        slo_mismatch = (W1 * ttft_slo_mismatch) + (W2 * tpot_slo_mismatch)
        fitness = (A * slo_mismatch) + (B * load_balance_score)

        return fitness

    # --- 专门用于调试打印的计算函数 ---
    def calculate_debug_info(self,
                             token_length: int,
                             slo_ttft: float,
                             slo_tpot: float,  # [新增] 需要 slo_tpot 来计算 Decode Mismatch
                             compress_factor: float,
                             prefill_pools: List[PrefillPool],
                             decode_pools: List[DecodePool],
                             network_map: Dict[Tuple[int, int], Network],
                             p_rank_map: Dict[int, float],
                             d_rank_map: Dict[int, float]):
        """
        [DEBUG] 遍历并打印所有 (P, D) 组合的详细匹配情况与得分构成
        """
        # 1. 基础参数准备与归一化
        current_req_rank = self.req_ranker.get_rank(token_length)

        BASE_SLO_TTFT = 1500.0
        safe_slo_ttft = max(slo_ttft, 1.0)
        gamma_ttft = max(1.0, BASE_SLO_TTFT / safe_slo_ttft)
        effective_req_p = min(current_req_rank * gamma_ttft, 1.0)

        BASE_SLO_TPOT = 50.0
        safe_slo_tpot = max(slo_tpot, 0.1)
        gamma_tpot = max(1.0, BASE_SLO_TPOT / safe_slo_tpot)
        effective_req_d = min(current_req_rank * gamma_tpot, 1.0)

        # 权重引用 (保持与 _calculate_fitness 一致)
        A, B = 2.0, 2.0  # Modified B
        W1, W2 = 1.0, 1.0
        load_alpha = 4.0

        print("\n" + "=" * 120)
        print(
            f"🚀 [DEBUG REPORT] Token={token_length} | Rank={current_req_rank:.4f} | SLO_TTFT={slo_ttft}ms | SLO_TPOT={slo_tpot}ms")
        print(
            f"{'Pair(P->D)':<12} | {'P-Mis(Rank)':<14} | {'Net-Mis(ms)':<14} | {'D-Mis(Rank)':<14} | {'Load Score':<12} | {'SLO Score':<12} | {'FINAL FIT':<10}")
        print("-" * 120)

        # 2. 暴力遍历所有组合 (P x D)
        for p_node in prefill_pools:
            for d_node in decode_pools:

                # --- A. Prefill Mismatch ---
                norm_p_cap = p_rank_map.get(p_node.pool_id, 0.5)
                diff_p = norm_p_cap - effective_req_p
                if diff_p >= 0:
                    cost_p_mismatch = diff_p * 5.0
                    p_note = f"{cost_p_mismatch:.2f}(Ov)"
                else:
                    cost_p_mismatch = abs(diff_p) * 10.0
                    p_note = f"{cost_p_mismatch:.2f}(Un)"

                # --- B. Network Mismatch ---
                net = network_map.get((p_node.pool_id, d_node.pool_id))
                net_cost = 1e9
                net_time_display = "N/A"
                if net:
                    data_size = token_length * compress_factor
                    net_time_ms = net.rtt + (data_size / (net.bandwidth + 1e-6))
                    net_cost = min(net_time_ms / safe_slo_ttft, 1.0)
                    net_time_display = f"{net_time_ms:.1f}ms"

                # --- C. Decode Mismatch ---
                norm_d_cap = d_rank_map.get(d_node.pool_id, 0.5)
                diff_d = norm_d_cap - effective_req_d
                if diff_d >= 0:
                    cost_d_mismatch = diff_d * 5.0
                    d_note = f"{cost_d_mismatch:.2f}(Ov)"
                else:
                    cost_d_mismatch = abs(diff_d) * 10.0
                    d_note = f"{cost_d_mismatch:.2f}(Un)"

                # --- D. Load Score (Modified) ---
                safe_p_load = min(p_node.current_load, 5.0)
                safe_d_load = min(d_node.current_load, 5.0)
                p_load_score = np.exp(load_alpha * safe_p_load)
                d_load_score = np.exp(load_alpha * safe_d_load)
                load_score = (p_load_score + d_load_score) / 2.0

                # --- E. Total Fitness ---
                ttft_part = cost_p_mismatch + net_cost
                tpot_part = cost_d_mismatch
                slo_score = (W1 * ttft_part) + (W2 * tpot_part)

                final_fitness = (A * slo_score) + (B * load_score)

                # --- 格式化打印 ---
                pair_str = f"P{p_node.pool_id}->D{d_node.pool_id}"

                print(
                    f"{pair_str:<12} | {p_note:<14} | {f'{net_time_display}/{net_cost:.2f}':<14} | {d_note:<14} | {load_score:<12.4f} | {slo_score:<12.4f} | {final_fitness:<10.4f}")

        print("=" * 120 + "\n")

    def schedule(self,
                 token_length: int,
                 slo_ttft: float,
                 slo_tpot: float,
                 compress_factor: float,
                 prefill_pools: List[PrefillPool],
                 decode_pools: List[DecodePool],
                 network_map: Dict[Tuple[int, int], Network]) -> Tuple[str, int, str, int]:

        num_p = len(prefill_pools)
        num_d = len(decode_pools)
        for p_node in prefill_pools:
            print(f"Pool {p_node.pool_id} flops: {p_node.flops}, load: {p_node.current_load}")
        for d_node in decode_pools:
            print(f"Pool {d_node.pool_id} mem_bandwidth: {d_node.mem_bandwidth}, load: {d_node.current_load}")
        for net in network_map.values():
            print(
                f"Network {net.prefill_pool.pool_id} -> {net.decode_pool.pool_id} bw: {net.bandwidth}")

        if num_p == 0 or num_d == 0:
            raise ValueError("Empty pools passed to PSO schedule")

        # 更新请求分布 Ranker
        self.req_ranker.update(token_length)
        # 获取当前请求的 Rank
        current_req_rank = self.req_ranker.get_rank(token_length)

        p_rank_map = self._calculate_pool_ranks(prefill_pools)
        d_rank_map = self._calculate_pool_ranks(decode_pools)

        # 初始化粒子
        positions = np.random.rand(self.num_particles, 2)
        positions[:, 0] *= num_p
        positions[:, 1] *= num_d

        velocities = np.random.rand(self.num_particles, 2) * 0.1

        p_best_pos = positions.copy()
        p_best_scores = np.full(self.num_particles, np.inf)

        g_best_pos = positions[0].copy()
        g_best_score = np.inf

        # 迭代
        for _ in range(self.max_iterations):
            for i in range(self.num_particles):
                # 传入预计算的 Rank Maps
                score = self._calculate_fitness(
                    positions[i], token_length, slo_ttft, slo_tpot, compress_factor,
                    prefill_pools, decode_pools, network_map,
                    current_req_rank,
                    p_rank_map, d_rank_map
                )

                if score < p_best_scores[i]:
                    p_best_scores[i] = score
                    p_best_pos[i] = positions[i].copy()

                if score < g_best_score:
                    g_best_score = score
                    g_best_pos = positions[i].copy()

            r1 = np.random.rand(self.num_particles, 2)
            r2 = np.random.rand(self.num_particles, 2)
            cognitive = self.c1 * r1 * (p_best_pos - positions)
            social = self.c2 * r2 * (g_best_pos - positions)
            velocities = (self.w_inertia * velocities) + cognitive + social
            positions += velocities

            positions[:, 0] = np.clip(positions[:, 0], 0, num_p - 1e-6)
            positions[:, 1] = np.clip(positions[:, 1], 0, num_d - 1e-6)

        self.calculate_debug_info(
            token_length, slo_ttft, slo_tpot, compress_factor,
            prefill_pools, decode_pools, network_map,
            p_rank_map, d_rank_map
        )
        best_p_idx = int(np.floor(g_best_pos[0])) % num_p
        best_d_idx = int(np.floor(g_best_pos[1])) % num_d

        target_p = prefill_pools[best_p_idx]
        target_d = decode_pools[best_d_idx]

        return (target_p.proxy_ip_address, target_p.proxy_port,
                target_d.proxy_ip_address, target_d.proxy_port)


# --- 4. 模拟处理流程 (Main) ---

async def request_processor(queue: asyncio.Queue, scheduler: PSO_Scheduler,
                            p_nodes: List[PrefillPool], d_nodes: List[DecodePool],
                            networks: Dict[Tuple[int, int], Network]):
    # print(">>> Worker: Scheduler Processor Started")
    processed_count = 0
    assignments = {}

    while True:
        # 获取 raw request data
        req_data = await queue.get()
        if req_data is None:
            queue.task_done()
            break

        # 解包数据: (ID, Length, SLO_TTFT, SLO_TPOT)
        rid, length, slo_ttft, slo_tpot = req_data

        start_t = time.perf_counter()

        # --- 核心调度调用 --- 直接调用schedule函数
        result = scheduler.schedule(
            length,
            slo_ttft,
            slo_tpot,
            0.8,  # Compress factor
            p_nodes,  # 传递prefill_pools
            d_nodes,  # 传递decode_pools
            networks  # 传递network_map
        )

        cost_t = (time.perf_counter() - start_t) * 1000  # ms

        # 解包结果 (schedule函数返回4个值：p_ip, p_port, d_ip, d_port)
        p_ip, p_port, d_ip, d_port = result

        # 根据p_ip和p_port查找对应的pool_id
        p_pool_id = None
        for p in p_nodes:
            if p.proxy_ip_address == p_ip and p.proxy_port == p_port:
                p_pool_id = p.pool_id
                break

        # 记录分配结果
        assignments[rid] = p_pool_id

        # 打印调度结果
        print(
            f"[REQ] {rid:8s} | Len: {length:5d}   -> Assigned: P[{p_ip}:{p_port}] + D[{d_ip}:{d_port}] (Calc Time: {cost_t:.2f}ms)")

        processed_count += 1
        queue.task_done()

    # print(f">>> Worker: Finished. Processed {processed_count} requests.")
    return assignments


async def run_test():
    """
    单次测试：生成测试数据并模拟调度流程
    """

    # --- 1. 生成测试数据 (模拟当前资源状态) ---

    # 1.1 Prefill 节点池
    p_nodes = [
        PrefillPool(pool_id=1, proxy_ip_address=f"10.1.1.10", proxy_port=8080, flops=17.6),
        PrefillPool(pool_id=2, proxy_ip_address=f"10.1.1.11", proxy_port=8080, flops=32 * 8),
    ]

    # 1.2 Decode 节点池
    d_nodes = [
        DecodePool(pool_id=i, proxy_ip_address=f"10.2.2.{20 + i}", proxy_port=9090,
                   mem_bandwidth=random.uniform(800, 800))
        for i in range(2)
    ]

    # 1.3 网络拓扑 (每对 Prefill-Decode 都有连接)
    networks = {
        (p.pool_id, d.pool_id): Network(
            prefill_pool=p,
            decode_pool=d,
            bandwidth=random.uniform(2000, 2000),
            rtt=random.uniform(5, 5)
        )
        for p in p_nodes
        for d in d_nodes
    }

    # 2. 初始化调度器
    scheduler = PSO_Scheduler()

    # 3. 模拟请求队列 (存放元组数据)
    request_queue = asyncio.Queue()

    # 测试数据: (ID, TokenLength, SLO_TTFT, SLO_TPOT)
    raw_requests = [
        ("REQ_A", 128, 1500.0, 20.0),
        ("REQ_B", 911, 1500.0, 50.0)
    ]

    for item in raw_requests:
        await request_queue.put(item)

    # 结束标记
    await request_queue.put(None)

    # 4. 运行处理
    assignments = await request_processor(request_queue, scheduler, p_nodes, d_nodes, networks)

    return assignments


def main():
    """
    主函数：运行100次测试并统计结果
    """
    total_runs = 1
    req_a_pool2_count = 0
    req_b_pool2_count = 0

    print(f"Starting {total_runs} test runs...")

    for i in range(total_runs):
        # 每次测试使用不同的随机种子
        np.random.seed(i)
        random.seed(i)

        # 运行一次测试
        assignments = asyncio.run(run_test())

        # 检查REQ_A的分配情况
        if "REQ_A" in assignments and assignments["REQ_A"] == 2:
            req_a_pool2_count += 1
        if "REQ_B" in assignments and assignments["REQ_B"] == 2:
            req_b_pool2_count += 1
            # 打印进度
        if (i + 1) % 10 == 0:
            print(f"Completed {i + 1}/{total_runs} tests...")

    # 输出统计结果
    print(f"\n=== Test Results ===")
    print(f"Total runs: {total_runs}")
    print(f"REQ_A assigned to pool_id=2: {req_a_pool2_count} times")
    print(f"Percentage: {req_a_pool2_count / total_runs * 100:.1f}%")
    print(f"REQ_B assigned to pool_id=2: {req_b_pool2_count} times")
    print(f"Percentage: {req_b_pool2_count / total_runs * 100:.1f}%")


if __name__ == "__main__":
    main()