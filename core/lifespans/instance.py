import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import threading
import time

from coord.instance.up_store import metric_update, instance_unregister
from store.instance.store import InstanceStore
from instance.perception.compute_metric_h import ThroughputMonitor

storage = InstanceStore()
LOOP_TASK_TIME = storage.LOOP_TASK_TIME

# 任务停止事件
stop_event = threading.Event()


def metric_update_loop_task(monitor: ThroughputMonitor):
    while not stop_event.is_set():
        metric_update(monitor)
        time.sleep(LOOP_TASK_TIME)


@asynccontextmanager
async def lifespan(api: FastAPI):
    """
    FastAPI 生命周期管理：
      - startup: 向全局调度器注册，开启轮询任务
      - shutdown: 向全局调度器注销
    """
    # 初始化阶段，向全局调度器注册
    global stop_event

    # 初始化并启动吞吐量监控器 (高频采样)
    vllm_host = storage.vllm_addr()
    monitor = ThroughputMonitor(host=vllm_host, interval=0.1, window_size=2.0)
    monitor.start()

    update_thread = threading.Thread(target=metric_update_loop_task, args=(monitor,), daemon=True)
    update_thread.start()

    logging.info("[Instance] startup: 初始化完成，监听服务中")

    # 这里 yield 之后是正常服务期
    yield

    # shutdown 阶段，如果后面有资源要清理，可以写在这里
    stop_event.set()

    # 停止吞吐量监控器
    monitor.stop()

    instance_unregister()
    logging.info("[Instance] shutdown: 结束服务")
