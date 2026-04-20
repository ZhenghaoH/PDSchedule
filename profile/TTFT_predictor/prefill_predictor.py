import asyncio
import logging
from typing import Dict, Optional

import numpy as np

if __package__:
    from .prefill_regressor import PrefillTimeRegressor
else:
    from profile.TTFT_predictor.prefill_regressor import PrefillTimeRegressor


VLLM_CONFIG_DEFAULT = {
    "host": "172.18.0.250",
    "port": 8000,
    "model_id": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "tokenizer_path": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B/",
}

BATCH_SIZES_TO_TEST = range(1, 9)
TOKEN_LENGTHS_TO_TEST = range(64, 2048, 64)

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
                (1, 1000, 0.22),
                (8, 100, 0.06),
                (8, 1000, 0.35),
                (4, 500, 0.12),
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
    if not regressor._is_fitted:
        return 0.02 * batch_size + 0.0002 * prompt_length
    return max(0.001, regressor.predict(batch_size, prompt_length))


async def perform_detailed_warmup(vllm_config: Dict = VLLM_CONFIG_DEFAULT, repeats: int = 3):
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

        coeffs = regressor.get_coefficients()
        print("\n[Detailed Warmup] Model calibrated.")
        print(f"   a (B*L) = {coeffs['a']:.2e}")
        print(f"   b (L)   = {coeffs['b']:.2e}")
        print(f"   c (B)   = {coeffs['c']:.2e}")
        print(f"   d (Int) = {coeffs['d']:.3f}")
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
