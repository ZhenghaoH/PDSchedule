import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np

if __package__:
    from .TPOT_regressor import OnlineTPOTRegressor
else:
    from TPOT_regressor import OnlineTPOTRegressor


VLLM_CONFIG_DEFAULT = {
    "host": "172.18.0.250",
    "port": 8000,
    "model_id": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "tokenizer_path": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
}
DEFAULT_PROMPT = "编写一个两万字的故事"

DEFAULT_MODEL_FILE = str(
    Path(__file__).resolve().parent / "artifacts" / "decode_tpot_regressor.pkl"
)

BATCH_SIZES_TO_TEST = range(1, 8)
GENERATION_LENGTHS_TO_TEST = range(128, 1281, 128)

TPOT_WARM_UP_CONFIGS_DEFAULT = [
    {"bs": bs, "gen": gen}
    for bs in BATCH_SIZES_TO_TEST
    for gen in GENERATION_LENGTHS_TO_TEST
]

_regressor: Optional[OnlineTPOTRegressor] = None
_lock = asyncio.Lock()


async def get_regressor() -> OnlineTPOTRegressor:
    global _regressor
    async with _lock:
        if _regressor is None:
            print("[TPOT Predictor] Initializing Regressor...")
            _regressor = OnlineTPOTRegressor()
            dummy_prompt_tokens = 8
            dummy_data = [
                {"bs": 1, "pl": dummy_prompt_tokens, "gen": 128, "gen_time": 1.20},
                {"bs": 2, "pl": dummy_prompt_tokens, "gen": 256, "gen_time": 2.80},
                {"bs": 4, "pl": dummy_prompt_tokens, "gen": 512, "gen_time": 7.00},
                {"bs": 7, "pl": dummy_prompt_tokens, "gen": 1280, "gen_time": 20.00},
            ]
            print("[TPOT Predictor] Seeding with dummy data for fast start...")
            try:
                _regressor._initialize_model_from_data(dummy_data)
            except Exception as exc:
                logging.error("[TPOT Predictor] Dummy init failed: %s", exc)
    return _regressor


async def predict_tpot(batch_size: int, context_length: int) -> float:
    regressor = await get_regressor()
    if not regressor._is_fitted:
        return 0.01
    return max(0.0001, regressor.predict_tpot(batch_size, context_length))


async def predict_generation_time(
    batch_size: int,
    prompt_length: int,
    generation_length: int,
) -> float:
    regressor = await get_regressor()
    if not regressor._is_fitted:
        return max(0.001, 0.01 * max(0, int(generation_length)))
    return max(
        0.0001,
        regressor.predict_generation_time(batch_size, prompt_length, generation_length),
    )


async def load_tpot_model(model_file: str = DEFAULT_MODEL_FILE) -> bool:
    regressor = await get_regressor()
    try:
        loaded = regressor.load_from_file(model_file)
    except Exception as exc:
        logging.warning("[TPOT Predictor] Failed to load model from %s: %s", model_file, exc)
        return False

    if loaded:
        logging.info("[TPOT Predictor] Loaded fitted model from %s", model_file)
    else:
        logging.info("[TPOT Predictor] No fitted model found at %s", model_file)
    return loaded


async def save_tpot_model(model_file: str = DEFAULT_MODEL_FILE) -> bool:
    regressor = await get_regressor()
    if not regressor._is_fitted:
        logging.warning("[TPOT Predictor] Skip saving model: regressor is not fitted.")
        return False

    try:
        regressor.save_to_file(model_file)
    except Exception as exc:
        logging.warning("[TPOT Predictor] Failed to save model to %s: %s", model_file, exc)
        return False

    logging.info("[TPOT Predictor] Saved fitted model to %s", model_file)
    return True


async def perform_detailed_warmup(
    vllm_config: Dict = VLLM_CONFIG_DEFAULT,
    repeats: int = 2,
    model_file: Optional[str] = None,
):
    print("\n" + "=" * 50)
    print("[TPOT Warmup] Starting Step-by-Step Benchmark...")
    print(f"   Target: {vllm_config['host']}:{vllm_config['port']}")
    vllm_config = dict(vllm_config)
    vllm_config.setdefault("prompt", DEFAULT_PROMPT)
    print(f"   Fixed prompt: {vllm_config['prompt']}")
    print(f"   Total Configs: {len(TPOT_WARM_UP_CONFIGS_DEFAULT)}")
    print("=" * 50)

    regressor = await get_regressor()
    regressor.clear_data()
    total_configs = len(TPOT_WARM_UP_CONFIGS_DEFAULT)

    try:
        for idx, config in enumerate(TPOT_WARM_UP_CONFIGS_DEFAULT):
            await asyncio.sleep(0)
            print(
                f"\n[{idx + 1}/{total_configs}] Testing: "
                f"bs={config['bs']}, gen={config['gen']} ...",
                end="",
                flush=True,
            )

            start_count = len(regressor._training_data)
            await regressor.warm_up_from_tests(
                test_configs=[config],
                vllm_config=vllm_config,
                repeats_per_config=repeats,
            )
            new_points = regressor._training_data[start_count:]
            if new_points:
                times = [point["gen_time"] for point in new_points]
                avg_time = float(np.mean(times))
                print(f" Done. Avg generation time: {avg_time * 1000:.2f} ms")
            else:
                print(" No data collected.")

        final_count = len(regressor._training_data)
        if final_count == 0:
            print("\n[TPOT Warmup] No data collected at all.")
            return

        print(f"\n[TPOT Warmup] All tests finished. Total points: {final_count}. Fitting model...")
        regressor._initialize_model_from_data(regressor._training_data)

        if model_file:
            await save_tpot_model(model_file)

        coeffs = regressor.get_coefficients()
        print("\n[TPOT Warmup] Model calibrated.")
        print(
            f"   d1={coeffs['d1']:.2e}, d2={coeffs['d2']:.2e}, "
            f"d3={coeffs['d3']:.2e}, d4={coeffs['d4']:.3e}"
        )
        print("=" * 50 + "\n")
    except asyncio.CancelledError:
        print("\n[TPOT Warmup] Cancelled by user.")
        raise
    except KeyboardInterrupt:
        print("\n[TPOT Warmup] Interrupted by user.")
        raise
    except Exception as exc:
        logging.error("[TPOT Warmup] Failed: %s", exc, exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def main():
        await perform_detailed_warmup()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[TPOT Predictor] Stopped.")
