import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

if __package__:
    from .prefill_predictor import predict_ttft, perform_detailed_warmup
else:
    from prefill_predictor import predict_ttft, perform_detailed_warmup

try:
    predict_ttft
except NameError:
    try:
        from prefill_predictor import predict_ttft, perform_detailed_warmup
    except Exception as exc:
        print(f"[Critical Error] Failed to import prefill_predictor: {exc}")
        raise SystemExit(1)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
warmup_task: Optional[asyncio.Task] = None


async def run_warmup_in_background():
    logger.info(">>> [Background] Waiting 5 seconds before warmup...")
    try:
        await asyncio.sleep(5)
        logger.info(">>> [Background] Starting detailed warmup...")
        await perform_detailed_warmup(repeats=3)
        logger.info(">>> [Background] Warmup finished.")
    except asyncio.CancelledError:
        logger.info(">>> [Background] Warmup cancelled.")
        raise
    except KeyboardInterrupt:
        logger.info(">>> [Background] Warmup interrupted by user.")
        raise
    except Exception as exc:
        logger.error(f">>> [Background] Warmup failed: {exc}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global warmup_task
    logger.info("Server starting...")

    try:
        await predict_ttft(batch_size=1, prompt_length=1)
        logger.info("Dummy init finished.")
    except Exception as exc:
        logger.error(f"[Error] Dummy init failed: {exc}")

    warmup_task = asyncio.create_task(run_warmup_in_background())
    logger.info("Background warmup task scheduled.")

    try:
        yield
    finally:
        logger.info("Server shutting down...")
        if warmup_task is not None and not warmup_task.done():
            warmup_task.cancel()
            try:
                await warmup_task
            except asyncio.CancelledError:
                logger.info("Background warmup task stopped.")
        warmup_task = None


app = FastAPI(
    title="vLLM TTFT Predictor API",
    description="Online TTFT prediction service with warmup data collection.",
    version="1.1.0",
    lifespan=lifespan,
)


class PredictionRequest(BaseModel):
    batch_size: int = 1
    prompt_length: int = 0
    total_tokens: Optional[int] = None


class PredictionResponse(BaseModel):
    predicted_ttft_seconds: float
    predicted_ttft_ms: float


@app.get("/", summary="Health check")
async def read_root():
    return {"status": "ok", "message": "TTFT Predictor is running."}


@app.post("/predict", summary="Predict TTFT", response_model=PredictionResponse)
async def handle_prediction(request: PredictionRequest):
    prompt_length = (
        max(0, int(request.total_tokens))
        if request.total_tokens is not None
        else request.prompt_length
    )
    prediction_sec = await predict_ttft(
        batch_size=1 if request.total_tokens is not None else request.batch_size,
        prompt_length=prompt_length,
    )
    return {
        "predicted_ttft_seconds": prediction_sec,
        "predicted_ttft_ms": prediction_sec * 1000,
    }

if __name__ == "__main__":
    logger.info("Starting uvicorn server...")
    try:
        uvicorn.run(
            app,
            host="172.18.0.250",
            port=9000,
            reload=False,
        )
    except KeyboardInterrupt:
        logger.info("Server interrupted by user.")
