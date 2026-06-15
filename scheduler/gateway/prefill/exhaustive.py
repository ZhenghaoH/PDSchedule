import itertools
from typing import List, Tuple
import time
import asyncio
import aiohttp

from scheduler.gateway.prefill.slo_aware import SLOAwarePrefillScheduler, SchedulableRequest, calculate_batch_nnvp
from core.schemas.request import BaseRequest


class ExhaustiveOracleScheduler(SLOAwarePrefillScheduler):
    """
    穷举调度先知：
    遍历所有请求排列 (Ordering) 和所有切分方案 (Batching)，
    找到理论上的 NNVP 最小值。
    """
    async def find_optimal(self, queue: List[BaseRequest]):
        t_now = time.time()
        if not queue: return None
        
        # 1. 预处理
        reqs = [SchedulableRequest(req=r) for r in queue]
        async with aiohttp.ClientSession() as session:
            # 获取基础指标
            for r in reqs:
                arrival_time = getattr(r.req.Service, "arrival_time", t_now)
                r.deadline = arrival_time + r.ttft_slo_sec
                r.solo_time = await self._get_prediction(session, 1, r.prompt_length)

            instance_heap = self._get_instance_heap()
            avail_time, target_ip = instance_heap[0] if instance_heap else (t_now, "unknown")
            start_time = max(t_now, avail_time)

            best_cost = float('inf')
            best_plan = None
            
            # 2. 开始穷举排列 (Ordering)
            # N 个请求有 N! 种排列
            permutations = list(itertools.permutations(reqs))
            total_cases = 0

            print(f"\n[Oracle] Starting Exhaustive Search for N={len(reqs)}...")
            
            for p_idx, perm in enumerate(permutations):
                # 3. 对每种排列穷举所有可能的组批切分 (Partitioning)
                # N 个元素之间有 N-1 个缝隙，每个缝隙可切可不切，共 2^(N-1) 种方案
                n = len(perm)
                for i in range(2**(n-1)):
                    total_cases += 1
                    current_plan = []
                    # 构建切分方案
                    current_batches = []
                    temp_batch = [perm[0]]
                    for j in range(n-1):
                        if (i >> j) & 1: # 在此处切分
                            current_batches.append(temp_batch)
                            temp_batch = []
                        temp_batch.append(perm[j+1])
                    current_batches.append(temp_batch)

                    # 4. 模拟执行并评估
                    total_nnvp = 0.0
                    sim_time = start_time
                    valid_plan = True
                    plan_detail = []

                    for batch in current_batches:
                        # 硬件约束检查
                        if sum(r.prompt_length for r in batch) > 32000:
                            valid_plan = False; break
                        
                        batch_tokens = sum(r.prompt_length for r in batch)
                        dur = await self._get_prediction(session, 1, batch_tokens)
                        sim_time += dur
                        cost = calculate_batch_nnvp(batch, sim_time)
                        total_nnvp += cost
                        plan_detail.append((batch, sim_time, dur))
                    
                    if valid_plan and total_nnvp < best_cost:
                        best_cost = total_nnvp
                        best_plan = plan_detail

            # 5. 打印对比结果
            print(f"\n{'#'*30} ORACLE RESULT {'#'*30}")
            print(f"Total combinations explored: {total_cases}")
            print(f"Theoretical Min NNVP: {best_cost:.4f}")
            for idx, (batch, f_time, dur) in enumerate(best_plan):
                ids = [str(r.request_id)[-4:] for r in batch]
                print(f"  Batch {idx+1}: IDs={ids} | Finish={f_time:.3f} | Dur={dur:.4f}")
            print(f"{'#'*75}\n")
            
            return best_cost, best_plan
