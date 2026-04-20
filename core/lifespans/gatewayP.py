import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import threading
import time

from coord.gateway.up_store import prefill_compute_register, prefill_compute_update, network_update
from store.gateway.manager import GatewayStoreManager

from core.handlers.gateway.prefill.proxy import request_batcher


# ======================= 日志过滤器配置 =======================
class EndpointFilter(logging.Filter):
    def __init__(self, excluded_paths):
        super().__init__()
        self.excluded_paths = excluded_paths

    def filter(self, record: logging.LogRecord) -> bool:
        # 获取日志消息文本
        message = record.getMessage()
        # 如果消息中包含任何一个被排除的路径，则返回 False (不打印)
        return not any(path in message for path in self.excluded_paths)


def silence_heartbeat_logs():
    """配置日志过滤器，屏蔽心跳接口的访问日志"""
    # 定义需要屏蔽的路径片段
    excluded = [
        "/instance/update"
    ]

    # 获取 uvicorn 的访问日志记录器并添加过滤器
    try:
        uvicorn_logger = logging.getLogger("uvicorn.access")
        # 避免重复添加
        if not any(isinstance(f, EndpointFilter) for f in uvicorn_logger.filters):
            uvicorn_logger.addFilter(EndpointFilter(excluded))
            logging.info(f"[Scheduler] 已屏蔽以下路径的 Uvicorn 日志: {excluded}")
    except Exception as e:
        logging.warning(f"[Scheduler] 配置日志过滤器失败: {e}")


storage = GatewayStoreManager()
LOOP_TASK_TIME = storage.LOOP_TASK_TIME

# 任务停止事件
stop_event = threading.Event()


def compute_update_loop_task():
    while not stop_event.is_set():
        prefill_compute_update()
        time.sleep(LOOP_TASK_TIME)


def network_update_loop_task():
    while not stop_event.is_set():
        network_update()
        time.sleep(LOOP_TASK_TIME)


@asynccontextmanager
async def lifespan(api: FastAPI):
    """
    FastAPI 生命周期管理：
      - startup: 向全局调度器注册，开启轮询任务
      - shutdown: 向全局调度器注销
    """
    # 配置日志过滤器，屏蔽心跳接口的访问日志
    silence_heartbeat_logs()

    # 初始化阶段，向全局调度器注册
    global stop_event
    while not prefill_compute_register():
        time.sleep(LOOP_TASK_TIME)
    update_thread1 = threading.Thread(target=compute_update_loop_task, daemon=True)
    update_thread2 = threading.Thread(target=network_update_loop_task, daemon=True)
    update_thread1.start()
    update_thread2.start()

    logging.info("准备启动 RequestBatcher...")
    try:
        await request_batcher.start()
        logging.info(f"✅ RequestBatcher 启动成功，当前策略: {request_batcher.scheduler_mgr.strategy}")
        logging.info(f"   - 分发策略: {request_batcher.dispatch_policy}")
        logging.info(f"   - 性能分析模式: {'开启' if request_batcher.profile_mode else '关闭'}")
    except Exception as e:
        logging.error(f"!!! RequestBatcher 启动失败: {str(e)}")

    logging.info("[Gateway] startup: 初始化完成，监听服务中")

    # 这里 yield 之后是正常服务期
    yield

    # shutdown 阶段，如果后面有资源要清理，可以写在这里
    stop_event.set()

    logging.info("Prefill Proxy 关闭，清理资源...")

    # 关闭RequestBatcher
    logging.info("准备关闭 RequestBatcher...")
    try:
        await request_batcher.shutdown()
        logging.info("✅ RequestBatcher 已成功关闭")
    except Exception as e:
        logging.error(f"!!! RequestBatcher 关闭失败: {str(e)}")

    # TODO：向全局调度器注销
    logging.info("[Gateway] shutdown: 结束服务")
