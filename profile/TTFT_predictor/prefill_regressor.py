import asyncio
from collections import defaultdict
from typing import Dict, List, Tuple

import aiohttp
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
from transformers import AutoTokenizer

if __package__:
    from .request_generator import generate_prompt_with_tokens, measure_ttft
else:
    from profile.TTFT_predictor.request_generator import generate_prompt_with_tokens, measure_ttft


class PrefillTimeRegressor:
    """Collect warmup samples and fit a simple TTFT regressor."""

    def __init__(self):
        self.model = LinearRegression(fit_intercept=True)
        self.scaler = StandardScaler()
        self._is_fitted = False
        self._coeffs = {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}
        self._training_data: List[Tuple[int, int, float]] = []

    def _transform_features(self, batchsize: int, prompt_length: int) -> np.ndarray:
        return np.array([[batchsize * prompt_length, prompt_length, batchsize]])

    def fit(self):
        """Fit the regression model using the currently collected samples."""
        if not self._training_data:
            print("[Regressor] Warning: No data to fit.")
            return

        data = self._training_data

        print("\n--- Data Collection Summary ---")
        stats = defaultdict(list)
        for bs, pl, t in data:
            stats[(bs, pl)].append(t)

        for (bs, pl), times in sorted(stats.items()):
            avg_t = np.mean(times)
            min_t = np.min(times)
            max_t = np.max(times)
            count = len(times)
            print(
                f"   Config (BS={bs}, PL={pl}): Count={count}, "
                f"Avg={avg_t * 1000:.2f}ms (Min={min_t * 1000:.1f}, Max={max_t * 1000:.1f})"
            )
        print("-------------------------------\n")

        X_raw = np.array([self._transform_features(bs, pl)[0] for bs, pl, _ in data])
        y = np.array([ttft for _, _, ttft in data])

        print(f"[Regressor] Fitting model with {len(y)} samples...")

        self.scaler.fit(X_raw)
        X_scaled = self.scaler.transform(X_raw)
        self.model.fit(X_scaled, y)
        self._is_fitted = True

        raw_coeffs_scaled = self.model.coef_
        intercept_scaled = self.model.intercept_
        mean = self.scaler.mean_
        scale = self.scaler.scale_
        scale[scale == 0] = 1

        raw_coeffs = raw_coeffs_scaled / scale
        intercept = intercept_scaled - np.sum(raw_coeffs * mean)

        self._coeffs = {
            "a": raw_coeffs[0],
            "b": raw_coeffs[1],
            "c": raw_coeffs[2],
            "d": intercept,
        }

        print("[Regressor] Model fitted successfully.")
        print(
            f"   Coeffs: a={self._coeffs['a']:.2e}, "
            f"b={self._coeffs['b']:.2e}, "
            f"c={self._coeffs['c']:.2e}, "
            f"d={self._coeffs['d']:.3f}"
        )

    def add_data(self, batch_size: int, prompt_length: int, prefill_time: float):
        self._training_data.append((batch_size, prompt_length, prefill_time))

    def clear_data(self):
        self._training_data = []

    async def trigger_warmup_requests(
        self,
        test_configs: List[Tuple[int, int]],
        vllm_config: Dict,
        repeats_per_config: int = 3,
    ):
        """Send warmup requests, measure TTFT locally, and store samples directly."""
        print("--- Triggering Warmup Requests ---")
        print(f"[INFO] Configs: {len(test_configs)}, Repeats: {repeats_per_config}")

        try:
            tokenizer = AutoTokenizer.from_pretrained(vllm_config["tokenizer_path"])
        except Exception as exc:
            print(f"[ERROR] Tokenizer load failed: {exc}")
            return

        timeout = aiohttp.ClientTimeout(total=600)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for bs, pl in test_configs:
                print(f"   >> Firing: BatchSize={bs}, PromptLen={pl} (x{repeats_per_config})")
                prompts = [generate_prompt_with_tokens(tokenizer, pl) for _ in range(bs)]

                for _ in range(repeats_per_config):
                    tasks = [
                        asyncio.create_task(
                            measure_ttft(
                                session,
                                vllm_config["host"],
                                vllm_config["port"],
                                vllm_config["model_id"],
                                prompt,
                            )
                        )
                        for prompt in prompts
                    ]
                    try:
                        results = await asyncio.gather(*tasks)
                        valid_ttfts = []
                        for ttft in results:
                            if ttft is not None and 0.001 <= ttft <= 60.0:
                                self.add_data(bs, pl, ttft)
                                valid_ttfts.append(ttft)

                        if valid_ttfts:
                            details = ", ".join(
                                f"{idx + 1}:{ttft * 1000:.2f}ms"
                                for idx, ttft in enumerate(valid_ttfts)
                            )
                            print(f"      Repeat TTFTs -> {details}")
                        else:
                            print("      Repeat TTFTs -> no valid samples")

                        await asyncio.sleep(0.5)
                    except asyncio.CancelledError:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        print("\n[Warmup] Request triggering cancelled.")
                        raise

        print("\n--- All Warmup Requests Sent ---")
        print("[INFO] TTFT samples have been collected locally.")

    def predict(self, batchsize: int, prompt_length: int) -> float:
        if not self._is_fitted:
            return 0.0
        X = self._transform_features(batchsize, prompt_length)
        X_scaled = self.scaler.transform(X)
        pred = self.model.predict(X_scaled)[0]
        return max(0.001, pred)

    def get_coefficients(self) -> dict:
        return self._coeffs
