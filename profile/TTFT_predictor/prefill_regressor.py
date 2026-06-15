import asyncio
from collections import defaultdict
from pathlib import Path
import pickle
from typing import Dict, List, Tuple

import aiohttp
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
from transformers import AutoTokenizer

if __package__:
    from .request_generator import generate_prompt_with_tokens, measure_ttft
else:
    from request_generator import generate_prompt_with_tokens, measure_ttft


class PrefillTimeRegressor:
    """Collect warmup samples and fit a simple TTFT regressor."""

    def __init__(self):
        self.model = LinearRegression(fit_intercept=True)
        self.scaler = StandardScaler()
        self._is_fitted = False
        self._coeffs = {"tokens": 0.0, "d": 0.0}
        self._training_data: List[Tuple[int, int, float]] = []

    def _transform_features(self, batchsize: int, prompt_length: int) -> np.ndarray:
        total_tokens = max(0, int(batchsize)) * max(0, int(prompt_length))
        return np.array([[total_tokens]])

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

        self._coeffs = {"tokens": raw_coeffs[0], "d": intercept}

        print("[Regressor] Model fitted successfully.")
        print(
            f"   Coeffs: tokens={self._coeffs['tokens']:.2e}, "
            f"d={self._coeffs['d']:.3f}"
        )

    def add_data(self, batch_size: int, prompt_length: int, prefill_time: float):
        self._training_data.append((batch_size, prompt_length, prefill_time))

    def clear_data(self):
        self._training_data = []

    def _cluster_ttfts(self, ttfts: List[float]) -> List[List[float]]:
        clusters: List[List[float]] = []
        for ttft in sorted(ttfts):
            if not clusters:
                clusters.append([ttft])
                continue

            current_cluster = clusters[-1]
            cluster_mean = float(np.mean(current_cluster))
            threshold = max(0.02, cluster_mean * 0.15)
            if ttft - current_cluster[-1] <= threshold:
                current_cluster.append(ttft)
            else:
                clusters.append([ttft])
        return clusters

    def _add_split_batch_samples(self, batch_size: int, prompt_length: int, ttfts: List[float]):
        """
        Group warmup TTFT samples into inferred vLLM batches before fitting.

        Samples in the same time cluster are treated as one batch. Later
        clusters include the waiting time of earlier batches, so fit the time
        delta between cluster means.
        """
        valid_ttfts = [ttft for ttft in ttfts if ttft is not None and 0.001 <= ttft <= 60.0]
        if not valid_ttfts:
            return [], None

        clusters = self._cluster_ttfts(valid_ttfts)
        batch_infos = []
        previous_mean = 0.0

        for cluster in clusters:
            cluster_bs = len(cluster)
            cluster_mean = float(np.mean(cluster))
            batch_time = cluster_mean - previous_mean
            previous_mean = cluster_mean

            if not 0.001 <= batch_time <= 60.0:
                continue

            self.add_data(cluster_bs, prompt_length, batch_time)
            batch_infos.append((cluster_bs, batch_time))

        return valid_ttfts, batch_infos

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
                        valid_ttfts, split_info = self._add_split_batch_samples(bs, pl, results)

                        if valid_ttfts:
                            details = ", ".join(
                                f"{idx + 1}:{ttft * 1000:.2f}ms"
                                for idx, ttft in enumerate(valid_ttfts)
                            )
                            print(f"      Repeat TTFTs -> {details}")
                            if split_info:
                                batch_details = ", ".join(
                                    f"bs={cluster_bs}:{batch_time * 1000:.2f}ms"
                                    for cluster_bs, batch_time in split_info
                                )
                                print(f"      Clustered batches -> {batch_details}")
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

    def save_to_file(self, file_path: str) -> None:
        """Persist the fitted regressor state for reuse after gateway restart."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "model": self.model,
            "scaler": self.scaler,
            "is_fitted": self._is_fitted,
            "coeffs": self._coeffs,
            "training_data": self._training_data,
        }
        with path.open("wb") as f:
            pickle.dump(state, f)

    def load_from_file(self, file_path: str) -> bool:
        """Load a persisted regressor state. Returns False if no state exists."""
        path = Path(file_path)
        if not path.exists():
            return False

        with path.open("rb") as f:
            state = pickle.load(f)

        scaler = state["scaler"]
        if getattr(scaler, "n_features_in_", 1) != 1:
            return False

        self.model = state["model"]
        self.scaler = scaler
        self._is_fitted = bool(state.get("is_fitted", False))
        coeffs = state.get("coeffs", {"tokens": 0.0, "d": 0.0})
        if "tokens" not in coeffs:
            coeffs = {
                "tokens": coeffs.get("a", 0.0) + coeffs.get("b", 0.0),
                "d": coeffs.get("d", 0.0),
            }
        self._coeffs = coeffs
        self._training_data = state.get("training_data", [])
        return self._is_fitted
