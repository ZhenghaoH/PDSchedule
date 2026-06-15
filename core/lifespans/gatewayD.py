import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import threading
import time

from fastapi import FastAPI

from coord.gateway.up_store import (
    decode_compute_register,
    decode_compute_unregister,
    decode_compute_update,
)
from store.gateway.manager import GatewayStoreManager

PROFILE_DIR = Path(__file__).resolve().parents[2] / "profile"
if str(PROFILE_DIR) not in sys.path:
    sys.path.append(str(PROFILE_DIR))

from TPOT_predictor.TPOT_predictor import (
    DEFAULT_MODEL_FILE as TPOT_DEFAULT_MODEL_FILE,
    VLLM_CONFIG_DEFAULT as TPOT_VLLM_CONFIG_DEFAULT,
    load_tpot_model,
    perform_detailed_warmup,
    predict_generation_time,
    predict_tpot,
)


class EndpointFilter(logging.Filter):
    def __init__(self, excluded_paths):
        super().__init__()
        self.excluded_paths = excluded_paths

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(path in message for path in self.excluded_paths)


def silence_heartbeat_logs():
    excluded = ["/instance/update"]
    try:
        uvicorn_logger = logging.getLogger("uvicorn.access")
        if not any(isinstance(f, EndpointFilter) for f in uvicorn_logger.filters):
            uvicorn_logger.addFilter(EndpointFilter(excluded))
            logging.info("[Scheduler] Uvicorn heartbeat logs filtered: %s", excluded)
    except Exception as exc:
        logging.warning("[Scheduler] Failed to configure heartbeat log filter: %s", exc)


storage = GatewayStoreManager()
LOOP_TASK_TIME = storage.LOOP_TASK_TIME

stop_event = threading.Event()
tpot_warmup_task = None


def compute_update_loop_task():
    while not stop_event.is_set():
        decode_compute_update()
        time.sleep(LOOP_TASK_TIME)


def _parse_host_port(addr: str):
    host, port = addr.rsplit(":", 1)
    return host.strip(), int(port)


async def run_tpot_warmup_task():
    if not storage.TPOT_WARMUP_ENABLED:
        return

    storage.clear_tpot_predictor()
    storage.clear_tpot_step_predictor()
    model_file = storage.TPOT_MODEL_FILE or TPOT_DEFAULT_MODEL_FILE
    loaded = await load_tpot_model(model_file)
    if loaded:
        storage.register_tpot_predictor(predict_generation_time)
        storage.register_tpot_step_predictor(predict_tpot)
        logging.info("[TPOT Predictor] Using persisted model: %s", model_file)
        return

    target = storage.TPOT_TARGET
    if not target and storage.decode_objs:
        target = storage.decode_objs[0]

    if not target:
        logging.warning("[TPOT Predictor] Warmup skipped: no TPOT target or decode instance configured.")
        return

    try:
        target_host, target_port = _parse_host_port(target)
    except Exception as exc:
        logging.warning("[TPOT Predictor] Warmup skipped: invalid target %s: %s", target, exc)
        return

    vllm_config = dict(TPOT_VLLM_CONFIG_DEFAULT)
    vllm_config["host"] = target_host
    vllm_config["port"] = target_port
    if storage.TPOT_MODEL_ID:
        vllm_config["model_id"] = storage.TPOT_MODEL_ID
    if storage.TPOT_TOKENIZER_PATH:
        vllm_config["tokenizer_path"] = storage.TPOT_TOKENIZER_PATH

    logging.info(
        "[TPOT Predictor] Warmup starting: target=%s:%s, repeats=%s, model_file=%s",
        target_host,
        target_port,
        storage.TPOT_WARMUP_REPEATS,
        model_file,
    )
    try:
        await perform_detailed_warmup(
            vllm_config=vllm_config,
            repeats=storage.TPOT_WARMUP_REPEATS,
            model_file=model_file,
        )
        storage.register_tpot_predictor(predict_generation_time)
        storage.register_tpot_step_predictor(predict_tpot)
        logging.info("[TPOT Predictor] Warmup finished.")
    except asyncio.CancelledError:
        logging.info("[TPOT Predictor] Warmup cancelled.")
        raise
    except Exception as exc:
        logging.error("[TPOT Predictor] Warmup failed: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(api: FastAPI):
    del api
    silence_heartbeat_logs()

    global stop_event, tpot_warmup_task
    while not decode_compute_register():
        time.sleep(LOOP_TASK_TIME)

    update_thread = threading.Thread(target=compute_update_loop_task, daemon=True)
    update_thread.start()

    if storage.TPOT_WARMUP_ENABLED:
        tpot_warmup_task = asyncio.create_task(run_tpot_warmup_task())
        logging.info("[TPOT Predictor] Background warmup task scheduled.")

    logging.info("[Gateway] startup: Decode gateway initialized.")

    yield

    stop_event.set()
    if tpot_warmup_task is not None and not tpot_warmup_task.done():
        tpot_warmup_task.cancel()
        try:
            await tpot_warmup_task
        except asyncio.CancelledError:
            logging.info("[TPOT Predictor] Background warmup task stopped.")
    tpot_warmup_task = None
    storage.clear_tpot_predictor()
    storage.clear_tpot_step_predictor()

    decode_compute_unregister()
    logging.info("[Gateway] shutdown: Decode gateway stopped.")
