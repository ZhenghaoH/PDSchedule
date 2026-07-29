import uvicorn
import logging
import os
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI

from config.controller import DEFAULT_MODEL
from utils import TokenizerRegistry

# ======================= 算网状态更新器 =======================
from store.controller import PoolManager
from store.controller import NetworkManager
from scheduler.controller.global_scheduler import GlobalScheduler
import asyncio  # 如果其他函数也用到了可以往前提


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
        "/prefill_pool/update",
        "/decode_pool/update",
        "/network/update",
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


async def scheduler_sync_loop(scheduler: GlobalScheduler, interval: float = 0.1):
    """后台任务：每2秒同步一次资源状态"""
    while True:
        try:
            # 由于 sync_state 内部主要是 CPU 操作和短暂锁，可以直接调用
            # 如果计算量极大，可以用 await loop.run_in_executor(None, scheduler.sync_state)
            scheduler.sync_state()
        except Exception as e:
            logging.error(f"Sync failed: {e}")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理：
      - startup: 预热分词器
      - shutdown: 目前没资源要清理，预留接口
    """
    # 配置日志过滤器，屏蔽心跳接口的访问日志
    silence_heartbeat_logs()

    # ------------------------------------------
    # 初始化亲和性调度运行时状态
    # ------------------------------------------
    app.state.affinity_store = {}  # type: ignore
    app.state.affinity_lock = threading.Lock()  # type: ignore
    logging.info("[Scheduler] Affinity store initialized.")
    
    # ------------------------------------------
    # 尝试预热 tokenizer 分词器
    # ------------------------------------------
    model_path = os.getenv("SCHEDULER_MODEL_PATH", DEFAULT_MODEL)
    try:
        # print("[Scheduler] startup: 开始预热分词器")
        TokenizerRegistry.warmup_tokenizers(model_path)
        logging.info(f"[Scheduler] Warmup tokenizers, model_path={model_path!r}")
    except Exception as e:
        logging.info(f"[Scheduler] 分词器预热失败:{e}")

    # 初始化全局调度器
    try:
        logging.info("[Scheduler] Init: Starting GlobalScheduler...")

        # 启动池管理器
        pool_manager = PoolManager()
        app.state.pool_manager = pool_manager  # 存储到 app.state 供其他模块使用
        logging.info("[Scheduler] PoolManager accessed.")

        # 实例化网络管理器
        network_manager = NetworkManager()
        app.state.network_manager = network_manager  # 存储到 app.state 供其他模块使用
        logging.info("[Scheduler] NetworkManager instantiated.")

        # 实例化全局调度器
        app.state.global_scheduler = GlobalScheduler(
            pool_manager,
            network_manager,
            routing_mode=getattr(app.state, "controller_routing_mode", "global"),
            fixed_prefill_gateway=getattr(app.state, "fixed_prefill_gateway", ""),
            fixed_decode_gateway=getattr(app.state, "fixed_decode_gateway", ""),
        )
        logging.info(
            "[Scheduler] GlobalScheduler instantiated: %s",
            app.state.global_scheduler.routing_status(),
        )

        # 启动后台同步循环
        sync_pool_net_state_task = asyncio.create_task(scheduler_sync_loop(app.state.global_scheduler))

    except Exception as e:
        logging.exception(f"[Scheduler] Failed to initialize GlobalScheduler: {e}")
        # 根据业务需求，调度器初始化失败可能需要阻断启动，或者设为 None
        app.state.global_scheduler = None

    logging.info("[Scheduler] startup: 初始化完成，监听服务中")

    # 这里 yield 之后是正常服务期
    try:
        yield
    finally:
        # ---------- shutdown ----------
        cp_server = getattr(app.state, "_cp_server", None)  # type: ignore
        cp_task = getattr(app.state, "_cp_task", None)  # type: ignore

        if cp_server is not None:
            cp_server.should_exit = True
        if cp_task is not None:
            try:
                await cp_task
            except Exception as e:
                pass

    # shutdown 阶段，如果后面有资源要清理，可以写在这里

    # shutdown 阶段，如果后面有资源要清理，可以写在这里
    logging.info("[Scheduler] shutdown: 结束服务")
