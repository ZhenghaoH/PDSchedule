import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import threading

from coord.instance.up_store import instance_register, metric_update, instance_unregister
from store.instance.store import InstanceStore
from instance.perception.compute_metric_h import ThroughputMonitor

storage = InstanceStore()

# 任务停止事件
stop_event = threading.Event()


def _retry_delay(attempt: int, maximum: float) -> float:
    return min(float(2 ** min(max(0, attempt), 8)), max(1.0, float(maximum)))


def metric_update_loop_task(
    monitor: ThroughputMonitor,
    worker_stop: threading.Event,
    initially_registered: bool,
    loop_interval: float,
    retry_max_interval: float,
):
    registered = initially_registered
    attempt = 0
    while not worker_stop.is_set():
        if not registered:
            result = instance_register()
            if result == "success":
                registered = True
                attempt = 0
            else:
                worker_stop.wait(_retry_delay(attempt, retry_max_interval))
                attempt += 1
                continue

        if worker_stop.is_set():
            break

        result = metric_update(monitor)
        if result == "registration_lost":
            registered = False
            attempt = 0
            continue
        if result == "success":
            attempt = 0
            delay = max(0.001, float(loop_interval))
        else:
            delay = _retry_delay(attempt, retry_max_interval)
            attempt += 1
        worker_stop.wait(delay)


@asynccontextmanager
async def lifespan(api: FastAPI):
    """
    FastAPI 生命周期管理：
      - startup: 向全局调度器注册，开启轮询任务
      - shutdown: 向全局调度器注销
    """
    # 初始化阶段，向全局调度器注册
    global stop_event
    stop_event = threading.Event()
    initial_registration = await asyncio.to_thread(instance_register)
    initially_registered = initial_registration == "success"

    # Start the existing throughput sampler and the configured metric reporter.
    vllm_host = storage.vllm_addr()
    monitor = ThroughputMonitor(host=vllm_host, interval=0.1, window_size=2.0)
    monitor.start()

    worker_stop = stop_event
    update_thread = threading.Thread(
        target=metric_update_loop_task,
        args=(
            monitor,
            worker_stop,
            initially_registered,
            storage.LOOP_TASK_TIME,
            storage.REGISTRATION_RETRY_MAX_INTERVAL_SEC,
        ),
        name="instance-metric-management",
        daemon=True,
    )
    update_thread.start()

    logging.info(
        "[Instance] startup: metric loop sleep interval=%.3fs, registered=%s",
        storage.LOOP_TASK_TIME,
        initially_registered,
    )
    try:
        yield
    finally:
        worker_stop.set()
        await asyncio.to_thread(update_thread.join, 6.0)
        if update_thread.is_alive():
            logging.warning("[Instance] Metric management worker did not exit within the shutdown bound")

        await asyncio.to_thread(monitor.stop, 5.0)
        await asyncio.to_thread(instance_unregister)
        logging.info("[Instance] shutdown: complete")
