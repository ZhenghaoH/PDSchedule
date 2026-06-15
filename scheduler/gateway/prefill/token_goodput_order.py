import asyncio
import heapq
import logging
import math
import time
from typing import Dict, List, Tuple

import aiohttp

from core.schemas.request import BaseRequest
from store.gateway.manager import GatewayStoreManager


global_storage = GatewayStoreManager()


class TokenGoodputOrderScheduler:
    def __init__(self, storage=None):
        self.storage = storage if storage else global_storage
        self._prediction_cache: Dict[int, float] = {}
        self.max_batch_tokens = 10240
        self.max_batch_size = 100

    def _get_instance_heap(self):
        heap = []
        if not hasattr(self.storage, "active_prefill_objs") or not self.storage.active_prefill_objs:
            return heap
        for ip in self.storage.active_prefill_objs:
            if not ip:
                continue
            avail_time = self.storage.get_predicted_availability(ip)
            heapq.heappush(heap, (avail_time, ip))
        return heap

    async def _get_prediction(self, session, batch_size: int, prompt_length: int) -> float:
        if batch_size <= 0:
            return 0.0

        total_tokens = max(0, int(batch_size)) * max(0, int(prompt_length))
        key = total_tokens
        if key in self._prediction_cache:
            return self._prediction_cache[key]

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
                logging.warning("[TokenGoodputOrder] In-process predictor failed: %s", exc)

        try:
            async with session.post(
                "http://localhost:9000/predict",
                json={"total_tokens": total_tokens},
            ) as resp:
                if resp.status == 200:
                    val = (await resp.json()).get("predicted_ttft_seconds")
                    if val is not None and math.isfinite(val) and val > 0:
                        self._prediction_cache[key] = val
                        return val
        except Exception as exc:
            logging.debug("[TokenGoodputOrder] HTTP predictor unavailable: %s", exc)

        return 0.0002 * total_tokens

    def _score_request(self, req: BaseRequest, now: float, predicted_solo_time: float):
        prompt_tokens = getattr(req.Prompt, "token_length", 0)
        arrival_time = getattr(req.Service, "arrival_time", now)
        slo_sec = getattr(req.Service, "SLO_TTFT", 0) / 1000.0
        deadline = arrival_time + slo_sec
        slack = deadline - now - predicted_solo_time
        token_rate = prompt_tokens / max(predicted_solo_time, 1e-6)

        if slack >= 0:
            score = token_rate
        else:
            score = -abs(slack) * 1000.0 + token_rate * 0.001

        return score, deadline, arrival_time, slack, token_rate

    async def schedule(self, queue: List[BaseRequest]) -> List[BaseRequest]:
        now = time.time()
        self._prediction_cache.clear()

        if not queue:
            return []

        instance_heap = self._get_instance_heap()
        if not instance_heap:
            return []
        _avail_time, target_ip = heapq.heappop(instance_heap)

        async with aiohttp.ClientSession() as session:
            solo_times = await self._predict_solo_times(session, queue)

        scored = []
        for idx, req in enumerate(queue):
            score, deadline, arrival_time, slack, token_rate = self._score_request(
                req,
                now,
                solo_times[idx],
            )
            scored.append((score, deadline, arrival_time, idx, req, slack, token_rate))

        scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))

        ordered_reqs = []
        current_order = 1
        current_tokens = 0
        current_size = 0

        log_rows = []
        for score, deadline, _arrival_time, _idx, req, slack, token_rate in scored:
            prompt_tokens = getattr(req.Prompt, "token_length", 0)
            if current_size and (
                current_tokens + prompt_tokens > self.max_batch_tokens
                or current_size >= self.max_batch_size
            ):
                current_order += 1
                current_tokens = 0
                current_size = 0

            req.Task.prefill_instance = target_ip
            req.Task.batch_order = current_order
            ordered_reqs.append(req)

            current_tokens += prompt_tokens
            current_size += 1
            log_rows.append(
                "%s:order=%d,tok=%d,score=%.3f,slack=%.3f,rate=%.1f,deadline=%.3f"
                % (
                    str(req.Request_ID)[-4:],
                    current_order,
                    prompt_tokens,
                    score,
                    slack,
                    token_rate,
                    deadline,
                )
            )

        logging.info(
            "[TokenGoodputOrder] target=%s q=%d ordered=%s",
            target_ip,
            len(queue),
            log_rows,
        )
        return ordered_reqs

    async def _predict_solo_times(self, session, queue: List[BaseRequest]) -> List[float]:
        tasks = [
            self._get_prediction(session, 1, getattr(req.Prompt, "token_length", 0))
            for req in queue
        ]
        return await asyncio.gather(*tasks)
