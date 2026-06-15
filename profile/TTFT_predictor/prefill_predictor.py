import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np

if __package__:
    from .prefill_regressor import PrefillTimeRegressor
else:
    from prefill_regressor import PrefillTimeRegressor


VLLM_CONFIG_DEFAULT = {
    "host": "172.18.0.250",
    "port": 8000,
    "model_id": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "tokenizer_path": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B/",
}

DEFAULT_MODEL_FILE = str(
    Path(__file__).resolve().parent / "artifacts" / "prefill_ttft_regressor.pkl"
)

BATCH_SIZES_TO_TEST = range(1, 9)
TOKEN_LENGTHS_TO_TEST = range(64, 1024, 64)

WARM_UP_CONFIGS_DEFAULT = [
    (bs, pl)
    for bs in BATCH_SIZES_TO_TEST
    for pl in TOKEN_LENGTHS_TO_TEST
    if bs * pl <= 10000
]

_regressor: Optional[PrefillTimeRegressor] = None
_lock = asyncio.Lock()


async def get_regressor() -> PrefillTimeRegressor:
    global _regressor
    async with _lock:
        if _regressor is None:
            print("[TTFT Predictor] Initializing Regressor...")
            _regressor = PrefillTimeRegressor()
            dummy_data = [
                (1, 100, 0.04),
                (1, 1000, 0.20),
                (1, 2000, 0.36),
                (1, 4000, 0.68),
                (1, 8000, 1.32),
            ]
            print("[TTFT Predictor] Seeding with dummy data for fast start...")
            try:
                for data_point in dummy_data:
                    _regressor.add_data(*data_point)
                _regressor.fit()
            except Exception as exc:
                logging.error(f"[TTFT Predictor] Dummy init failed: {exc}")
    return _regressor


async def predict_ttft(batch_size: int, prompt_length: int) -> float:
    regressor = await get_regressor()
    total_tokens = max(0, int(batch_size)) * max(0, int(prompt_length))
    if not regressor._is_fitted:
        return 0.0002 * total_tokens
    return max(0.001, regressor.predict(batch_size, prompt_length))


async def load_ttft_model(model_file: str = DEFAULT_MODEL_FILE) -> bool:
    regressor = await get_regressor()
    try:
        loaded = regressor.load_from_file(model_file)
    except Exception as exc:
        logging.warning(f"[TTFT Predictor] Failed to load model from {model_file}: {exc}")
        return False

    if loaded:
        logging.info(f"[TTFT Predictor] Loaded fitted model from {model_file}")
    else:
        logging.info(f"[TTFT Predictor] No fitted model found at {model_file}")
    return loaded


async def save_ttft_model(model_file: str = DEFAULT_MODEL_FILE) -> bool:
    regressor = await get_regressor()
    if not regressor._is_fitted:
        logging.warning("[TTFT Predictor] Skip saving model: regressor is not fitted.")
        return False

    try:
        regressor.save_to_file(model_file)
    except Exception as exc:
        logging.warning(f"[TTFT Predictor] Failed to save model to {model_file}: {exc}")
        return False

    logging.info(f"[TTFT Predictor] Saved fitted model to {model_file}")
    return True


async def perform_detailed_warmup(
    vllm_config: Dict = VLLM_CONFIG_DEFAULT,
    repeats: int = 3,
    model_file: Optional[str] = None,
):
    """
    Run warmup tests step by step and fit the model after all collected points.
    Ctrl+C / cancellation should stop the loop immediately.
    """
    print("\n" + "=" * 50)
    print("[Detailed Warmup] Starting Step-by-Step Benchmark...")
    print(f"   Target: {vllm_config['host']}:{vllm_config['port']}")
    print(f"   Total Configs: {len(WARM_UP_CONFIGS_DEFAULT)}")
    print("=" * 50)

    regressor = await get_regressor()
    regressor.clear_data()
    total_configs = len(WARM_UP_CONFIGS_DEFAULT)

    try:
        for idx, (bs, pl) in enumerate(WARM_UP_CONFIGS_DEFAULT):
            await asyncio.sleep(0)
            print(
                f"\n[{idx + 1}/{total_configs}] Testing: BatchSize={bs}, PromptLen={pl} ...",
                end="",
                flush=True,
            )

            start_data_count = len(regressor._training_data)
            await regressor.trigger_warmup_requests(
                test_configs=[(bs, pl)],
                vllm_config=vllm_config,
                repeats_per_config=repeats,
            )
            new_data_points = regressor._training_data[start_data_count:]
            if new_data_points:
                times = [t for _, _, t in new_data_points]
                avg_time = np.mean(times)
                print(f" Done. Avg TTFT: {avg_time * 1000:.2f} ms ({len(times)} samples)")
            else:
                print(" Cancelled/Timeout. No data collected.")

        final_count = len(regressor._training_data)
        if final_count == 0:
            print("\n[Detailed Warmup] No data collected at all.")
            return

        print(f"\n[Detailed Warmup] All tests finished. Total points: {final_count}. Fitting model...")
        regressor.fit()

        if model_file:
            await save_ttft_model(model_file)

        coeffs = regressor.get_coefficients()
        print("\n[Detailed Warmup] Model calibrated.")
        print(f"   tokens = {coeffs['tokens']:.2e}")
        print(f"   d      = {coeffs['d']:.3f}")
        print("=" * 50 + "\n")

    except asyncio.CancelledError:
        print("\n[Detailed Warmup] Cancelled by user.")
        raise
    except KeyboardInterrupt:
        print("\n[Detailed Warmup] Interrupted by user.")
        raise
    except Exception as exc:
        logging.error(f"[Detailed Warmup] Failed: {exc}", exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def main():
        await perform_detailed_warmup()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[TTFT Predictor] Stopped.")
