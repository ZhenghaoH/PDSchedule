import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import threading

from coord.gateway.registration import (
    run_gateway_management_loop,
    run_instance_expiry_loop,
    wait_for_first_registration,
)
from coord.gateway.up_store import prefill_compute_unregister
from store.gateway.manager import GatewayStoreManager

from pdschedule.profiling.TTFT_predictor.prefill_predictor import (
    DEFAULT_MODEL_FILE as TTFT_DEFAULT_MODEL_FILE,
    VLLM_CONFIG_DEFAULT as TTFT_VLLM_CONFIG_DEFAULT,
    load_ttft_model,
    perform_detailed_warmup,
    predict_ttft,
)

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

# 任务停止事件
stop_event = threading.Event()
ttft_warmup_task = None


def _parse_host_port(addr: str):
    host, port = addr.rsplit(":", 1)
    return host.strip(), int(port)


async def run_ttft_warmup_task():
    if not storage.TTFT_WARMUP_ENABLED:
        return

    storage.clear_ttft_predictor()
    model_file = storage.TTFT_MODEL_FILE or TTFT_DEFAULT_MODEL_FILE
    loaded = await load_ttft_model(model_file)
    if loaded:
        storage.register_ttft_predictor(predict_ttft)
        logging.info(f"[TTFT Predictor] Using persisted model: {model_file}")
        return

    target = storage.TTFT_TARGET
    if not target:
        prefill_objs = storage.prefill_objs
        if prefill_objs:
            target = prefill_objs[0]

    if not target:
        logging.warning("[TTFT Predictor] Warmup skipped: no TTFT target or prefill instance configured.")
        return

    try:
        target_host, target_port = _parse_host_port(target)
    except Exception as exc:
        logging.warning(f"[TTFT Predictor] Warmup skipped: invalid target {target}: {exc}")
        return

    vllm_config = dict(TTFT_VLLM_CONFIG_DEFAULT)
    vllm_config["host"] = target_host
    vllm_config["port"] = target_port
    if storage.TTFT_MODEL_ID:
        vllm_config["model_id"] = storage.TTFT_MODEL_ID
    if storage.TTFT_TOKENIZER_PATH:
        vllm_config["tokenizer_path"] = storage.TTFT_TOKENIZER_PATH

    logging.info(
        f"[TTFT Predictor] Warmup starting: target={target_host}:{target_port}, "
        f"repeats={storage.TTFT_WARMUP_REPEATS}, model_file={model_file}"
    )
    try:
        await perform_detailed_warmup(
            vllm_config=vllm_config,
            repeats=storage.TTFT_WARMUP_REPEATS,
            model_file=model_file,
        )
        storage.register_ttft_predictor(predict_ttft)
        logging.info("[TTFT Predictor] Warmup finished.")
    except asyncio.CancelledError:
        logging.info("[TTFT Predictor] Warmup cancelled.")
        raise
    except Exception as exc:
        logging.error(f"[TTFT Predictor] Warmup failed: {exc}", exc_info=True)


@asynccontextmanager
async def lifespan(api: FastAPI):
    """
    FastAPI 生命周期管理：
      - startup: 向全局调度器注册，开启轮询任务
      - shutdown: 向全局调度器注销
    """
    # 配置日志过滤器，屏蔽心跳接口的访问日志
    silence_heartbeat_logs()

    global stop_event, ttft_warmup_task
    stop_event = threading.Event()
    registered_event = threading.Event()
    management_thread = threading.Thread(
        target=run_gateway_management_loop,
        args=(
            "prefill",
            stop_event,
            registered_event,
            storage.LOOP_TASK_TIME,
            storage.REGISTRATION_RETRY_MAX_INTERVAL_SEC,
            storage,
        ),
        name="prefill-controller-management",
        daemon=True,
    )
    expiry_thread = threading.Thread(
        target=run_instance_expiry_loop,
        args=(stop_event, storage),
        name="prefill-instance-expiry",
        daemon=True,
    )
    management_thread.start()
    expiry_thread.start()
    try:
        if not await wait_for_first_registration(
            registered_event, stop_event, management_thread
        ):
            raise RuntimeError("Prefill Gateway stopped before Controller registration")

        if storage.TTFT_WARMUP_ENABLED:
            ttft_warmup_task = asyncio.create_task(run_ttft_warmup_task())
            logging.info("[TTFT Predictor] Background warmup task scheduled.")

        logging.info("Preparing RequestBatcher...")
        try:
            await request_batcher.start()
            logging.info(
                "RequestBatcher started; strategy=%s dispatch=%s profile=%s",
                request_batcher.scheduler_mgr.strategy,
                request_batcher.dispatch_policy,
                request_batcher.profile_mode,
            )
        except Exception as exc:
            logging.error("RequestBatcher startup failed: %s", exc)

        logging.info("[Gateway] Prefill startup complete; listening for requests")
        yield
    finally:
        stop_event.set()
        await asyncio.to_thread(management_thread.join, 6.0)
        await asyncio.to_thread(expiry_thread.join, 2.0)
        if management_thread.is_alive() or expiry_thread.is_alive():
            logging.warning("[Gateway] Prefill management workers did not exit within the shutdown bound")

        if ttft_warmup_task is not None and not ttft_warmup_task.done():
            ttft_warmup_task.cancel()
            try:
                await ttft_warmup_task
            except asyncio.CancelledError:
                logging.info("[TTFT Predictor] Background warmup task stopped.")
        ttft_warmup_task = None
        storage.clear_ttft_predictor()

        try:
            await request_batcher.shutdown()
        except Exception as exc:
            logging.error("RequestBatcher shutdown failed: %s", exc)

        await asyncio.to_thread(prefill_compute_unregister)
        logging.info("[Gateway] Prefill shutdown complete")
