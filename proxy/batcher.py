import logging
from typing import Optional, List, Dict, Any
import asyncio
import time

import threading
from collections.abc import AsyncIterator
from collections import defaultdict

from store.gateway.manager import GatewayStoreManager
from scheduler.gateway.decode.sync_xpyd import SyncBaseScheduler as DecodeScheduler
from proxy.forward import forward_request

from core.schemas.request import BaseRequest


class IterableTracker:
    """封装流式传输迭代器，用于获取迭代器响应结束时机"""
    def __init__(self, original_iter: AsyncIterator, completion_counter: List[int]):
        self.original_iter = original_iter
        self._finished = False
        self.completion_counter = completion_counter
        self._counter_lock = threading.Lock()  # 只用于保护计数器

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            result = await self.original_iter.__anext__()
            return result
        except Exception as e:
            if self._finished:
                # raise StopAsyncIteration
                raise
            with self._counter_lock:
                if not self._finished:
                    self._finished = True
                    self.completion_counter[0] += 1
            raise

    def is_finished(self) -> bool:
        # 读取状态时也不加锁，因为Python的GIL保证了单变量读写的原子性
        return self._finished


# noinspection PyAsyncCall
class RequestBatcher:
    def __init__(self):
        # 统一缓存所有请求
        self.batch: List[BaseRequest] = []
        # 统一管理所有请求的任务信号("event": asyncio.Event)和结果("result": dict)
        self.batch_manager: Dict[int, dict] = {}
        # 超时任务
        self.timeout_task: Optional[asyncio.Task] = None
        # 批次创建时间
        self.batch_time: float = 0
        # 全局锁，保护所有共享数据结构
        self.lock = asyncio.Lock()

        self.storage: GatewayStoreManager = GatewayStoreManager()

    async def add_request(self, request: BaseRequest) -> Dict[str, Any]:
        """添加请求到批处理队列，返回处理结果"""
        request_id = None
        event = None
        should_process = False
        current_batch = None

        # 使用锁保护共享数据
        async with self.lock:
            # 添加请求并记录ID
            request_id = request.Request_ID
            self.batch.append(request)

            if len(self.batch) == 1:
                # 启动新一轮超时任务
                self.batch_time = time.time()
                self._start_timeout_task()

            logging.info(f"Added request to batch, current size: {len(self.batch)}")

            # 检查是否达到批量大小
            if len(self.batch) >= self.storage.SYN_BATCH_SIZE:
                logging.info(f"Batch reached size {self.storage.SYN_BATCH_SIZE}, triggering processing")
                # 取消超时任务
                if self.timeout_task:
                    self.timeout_task.cancel()
                    self.timeout_task = None
                # 保存当前批次的数据
                current_batch = self.batch.copy()
                # 重置批次
                self.batch = []
                self.batch_time = time.time()
                should_process = True
            event = asyncio.Event()
            self.batch_manager[request_id] = {
                "event": event,
                "result": {}
            }

        # 如果达到批量大小，处理批次
        if should_process and current_batch is not None:
            # 处理批次
            asyncio.create_task(self._process_batch(current_batch))

        # 等待批处理完成
        if event:
            await event.wait()
            async with self.lock:
                if request_id is not None and request_id in self.batch_manager.keys():
                    result = self.batch_manager[request_id]["result"]
                    del self.batch_manager[request_id]
                    return result
        return {"error": "Request processing failed"}

    def _start_timeout_task(self):
        """启动超时任务"""
        if self.timeout_task:
            # 如果已有超时任务，先取消
            self.timeout_task.cancel()
        # 创建新的超时任务
        self.timeout_task = asyncio.create_task(self._timeout_handler())
        logging.info("Started timeout task")

    async def _timeout_handler(self):
        """处理超时逻辑"""
        try:
            await asyncio.sleep(self.storage.SYN_TIMEOUT)
            async with self.lock:
                # 检查批次是否还有请求且未达到批量大小
                if 0 < len(self.batch) < self.storage.SYN_BATCH_SIZE:
                    # 检查批次是否已经超时
                    current_time = time.time()
                    if current_time - self.batch_time >= self.storage.SYN_TIMEOUT:
                        logging.info(
                            f"Batch timeout after {self.storage.SYN_TIMEOUT}s, processing {len(self.batch)} requests")
                        # 保存当前批次的数据
                        current_batch = self.batch.copy()
                        # 重置批次
                        self.batch = []
                        self.batch_time = time.time()
                        # 处理批次
                        asyncio.create_task(self._process_batch(current_batch))
                    else:
                        # 批次创建时间不足，重新启动超时任务
                        remaining_time = self.storage.SYN_TIMEOUT - (current_time - self.batch_time)
                        logging.info(f"Batch not ready for timeout, remaining time: {remaining_time:.2f}s")
                        self._start_timeout_task()
        except asyncio.CancelledError:
            # 超时任务被取消是正常情况
            logging.debug("Timeout task was cancelled")
            pass

    async def _process_batch(self, batch_requests: List[BaseRequest]):
        """处理指定批次的所有请求"""
        logging.info(f"Processing batch with {len(batch_requests)} requests")

        scheduler = DecodeScheduler()
        scheduled_requests = scheduler.schedule(batch_requests)

        classified_requests = defaultdict(lambda: defaultdict(list))
        for req in scheduled_requests:
            classified_requests[req.Task.decode_instance][req.Task.batch_order].append(req)
        for decode_instance in classified_requests:
            batch_groups = classified_requests[decode_instance]
            classified_requests[decode_instance] = dict(sorted(batch_groups.items()))

        # 分批处理
        for instance, batch_groups in classified_requests.items():
            asyncio.create_task(self._process_grouped_batch(instance, batch_groups))

    async def _process_grouped_batch(self, instance: str, batch_groups: Dict[int, List[BaseRequest]]):
        """把某批次请求转发给特定实例"""
        # 分优先级转发
        for batch_order, requests in batch_groups.items():
            completion_counter = [0]  # 创建共享计数器
            iter_num = len(requests)  # 需要等待的迭代器数量
            for req in requests:
                asyncio.create_task(
                    self._forward_single_request(instance, req, completion_counter)
                )
            # 检测所有迭代器是否完成
            while completion_counter[0] < iter_num:
                await asyncio.sleep(0.0001)

    async def _forward_single_request(self, instance: str, request: BaseRequest, counter: Optional[List[int]] = None):
        """转发单个请求到指定URL"""
        request_id = request.Request_ID
        request_payload = BaseRequest.to_vllm_payload(request)
        endpoint_type = request.Service.Endpoint_type
        # 根据请求数据决定目标URL
        target_url = f"http://{instance}/v1/{endpoint_type}"

        # FIXME: 演示用，打印池调度状态
        print("\n\n\n")
        print("+" * 80)
        print(f"[Gateway] 调度状态 ")
        print("+" * 80)
        print(f"转发实例: {instance}")
        for k, v in request_payload.items():
            print(f"请求报文 {k}: {v}")
        print("+" * 80)
        print("\n\n\n")

        try:
            generator = forward_request(target_url, request_payload)
            if counter is not None:
                generator = IterableTracker(generator, counter)

            async with self.lock:
                self.batch_manager[request_id]["result"] = {
                    "success": True,
                    "generator": generator,
                    "instance": instance
                }
                self.batch_manager[request_id]["event"].set()
        except Exception as e:
            logging.error(f"Error forwarding request to {target_url}: {str(e)}")
            async with self.lock:
                self.batch_manager[request_id]["result"] = {
                    "success": False,
                    "error": str(e)
                }
                self.batch_manager[request_id]["event"].set()
            if counter is not None:
                counter[0] += 1  # 报错直接判其传输结束，避免影响下一批
