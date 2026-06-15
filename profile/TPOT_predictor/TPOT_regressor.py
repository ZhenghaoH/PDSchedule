import asyncio
import pickle
from pathlib import Path
from typing import Dict, List

import aiohttp
import numpy as np
from sklearn.linear_model import LinearRegression, SGDRegressor
from sklearn.preprocessing import StandardScaler
from transformers import AutoTokenizer

if __package__:
    from .TPOT_measure import measure_generation_performance
else:
    from TPOT_measure import measure_generation_performance


DEFAULT_PROMPT = "编写一个两万字的故事"


class OnlineTPOTRegressor:
    """Summation-based TPOT regressor for decode generation time."""

    def __init__(self, learning_rate: float = 1e-9):
        self.online_model = SGDRegressor(
            loss="squared_error",
            learning_rate="constant",
            eta0=learning_rate,
            fit_intercept=False,
            max_iter=1,
            tol=None,
        )
        self.scaler = StandardScaler()
        self._is_fitted = False
        self.coeffs = {"d1": 0.0, "d2": 0.0, "d3": 0.0, "d4": 0.0}
        self._training_data: List[Dict] = []

    def _transform_full_generation_to_features(
        self,
        batchsize: int,
        prompt_length: int,
        gen_length: int,
    ) -> np.ndarray:
        batchsize = max(0, int(batchsize))
        prompt_length = max(0, int(prompt_length))
        gen_length = max(0, int(gen_length))
        if gen_length <= 0:
            return np.array([[0.0, 0.0, 0.0, 0.0]])

        sum_of_lengths = gen_length * prompt_length + gen_length * (gen_length - 1) / 2.0
        return np.array(
            [[
                batchsize * sum_of_lengths,
                batchsize * gen_length,
                sum_of_lengths,
                gen_length,
            ]]
        )

    def _initialize_model_from_data(self, initial_data: List[Dict]):
        if not initial_data:
            print("[TPOT Regressor] Warning: no data to fit.")
            return

        self._training_data = list(initial_data)
        X_raw = np.array(
            [
                self._transform_full_generation_to_features(
                    point["bs"],
                    point["pl"],
                    point["gen"],
                )[0]
                for point in initial_data
            ]
        )
        y = np.array([point["gen_time"] for point in initial_data])

        batch_model = LinearRegression(fit_intercept=False).fit(X_raw, y)
        d1, d2, d3, d4 = batch_model.coef_
        self.coeffs = {"d1": d1, "d2": d2, "d3": d3, "d4": d4}

        self.scaler.fit(X_raw)
        X_scaled = self.scaler.transform(X_raw)
        online_init_model = SGDRegressor(
            fit_intercept=False,
            eta0=self.online_model.eta0,
            learning_rate="constant",
        )
        online_init_model.fit(X_scaled, y)
        self.online_model = online_init_model
        self._is_fitted = True

        print("[TPOT Regressor] Model fitted successfully.")
        print(
            "[TPOT Regressor] Coeffs: "
            f"d1={d1:.2e}, d2={d2:.2e}, d3={d3:.2e}, d4={d4:.3e}"
        )

    def add_data(self, batch_size: int, prompt_length: int, generated_tokens: int, generation_time: float):
        self._training_data.append(
            {
                "bs": int(batch_size),
                "pl": int(prompt_length),
                "gen": int(generated_tokens),
                "gen_time": float(generation_time),
            }
        )

    def clear_data(self):
        self._training_data = []

    async def warm_up_from_tests(
        self,
        test_configs: List[Dict],
        vllm_config: Dict,
        repeats_per_config: int = 2,
    ):
        print("--- Triggering TPOT warmup requests ---")
        new_data_points = []

        try:
            tokenizer = AutoTokenizer.from_pretrained(vllm_config["tokenizer_path"])
        except Exception as exc:
            print(f"[TPOT Regressor] Tokenizer load failed: {exc}")
            return
        prompt = vllm_config.get("prompt", DEFAULT_PROMPT)
        pl = len(tokenizer.encode(prompt, add_special_tokens=False))
        if pl <= 0:
            print("[TPOT Regressor] Fixed prompt has zero tokens.")
            return
        print(f"[TPOT Warmup] Fixed prompt={prompt}")
        print(f"[TPOT Warmup] Fixed prompt tokens={pl}")

        timeout = aiohttp.ClientTimeout(total=900)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for config in test_configs:
                bs = int(config["bs"])
                gen = int(config["gen"])
                print(f"[TPOT Warmup] Testing bs={bs}, pl={pl}, gen={gen}")

                for repeat_idx in range(repeats_per_config):
                    tasks = [
                        asyncio.create_task(
                            measure_generation_performance(session, prompt, vllm_config, gen)
                        )
                        for _ in range(bs)
                    ]
                    try:
                        results = await asyncio.gather(*tasks)
                    except asyncio.CancelledError:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        raise

                    successful = [result for result in results if result]
                    if not successful:
                        print(f"  Repeat {repeat_idx + 1}: no valid samples")
                        continue

                    avg_e2e = float(np.mean([result["total_time"] for result in successful]))
                    avg_ttft = float(np.mean([result["ttft"] for result in successful]))
                    avg_gen_tokens = float(np.mean([result["output_tokens"] for result in successful]))
                    generation_time = max(0.001, avg_e2e - avg_ttft)

                    if avg_gen_tokens > 1:
                        data_point = {
                            "bs": bs,
                            "pl": pl,
                            "gen": int(round(avg_gen_tokens)),
                            "gen_time": generation_time,
                        }
                        self.add_data(
                            bs,
                            pl,
                            int(round(avg_gen_tokens)),
                            generation_time,
                        )
                        new_data_points.append(data_point)
                        print(
                            f"  Repeat {repeat_idx + 1}: gen_time={generation_time * 1000:.2f}ms, "
                            f"tokens={avg_gen_tokens:.1f}"
                        )
                    await asyncio.sleep(0.5)

        if new_data_points:
            print(f"[TPOT Regressor] Added {len(new_data_points)} warmup samples.")
        else:
            print("[TPOT Regressor] No valid warmup data collected.")

    def predict_tpot(self, batchsize: int, context_length: int) -> float:
        if not self._is_fitted:
            return 0.0
        d1 = self.coeffs["d1"]
        d2 = self.coeffs["d2"]
        d3 = self.coeffs["d3"]
        d4 = self.coeffs["d4"]
        pred = d1 * batchsize * context_length + d2 * batchsize + d3 * context_length + d4
        return max(0.0001, float(pred))

    def predict_generation_time(self, batchsize: int, prompt_length: int, generation_length: int) -> float:
        if not self._is_fitted:
            return 0.0
        features = self._transform_full_generation_to_features(
            batchsize,
            prompt_length,
            generation_length,
        )[0]
        coeffs = np.array([self.coeffs["d1"], self.coeffs["d2"], self.coeffs["d3"], self.coeffs["d4"]])
        return max(0.0001, float(np.dot(features, coeffs)))

    def update(self, batchsize: int, prompt_length: int, generated_tokens: int, generation_time: float):
        if not self._is_fitted:
            raise RuntimeError("TPOT regressor is not fitted.")
        if generated_tokens <= 0:
            return

        self.add_data(batchsize, prompt_length, generated_tokens, generation_time)
        X = self._transform_full_generation_to_features(batchsize, prompt_length, generated_tokens)
        y = np.array([generation_time])
        X_scaled = self.scaler.partial_fit(X).transform(X)
        self.online_model.partial_fit(X_scaled, y)
        self._update_coeffs_from_online_model()

    def _update_coeffs_from_online_model(self):
        if not self._is_fitted:
            return
        w_scaled = self.online_model.coef_
        scale = self.scaler.scale_
        scale[scale == 0] = 1
        d1, d2, d3, d4 = w_scaled / scale
        self.coeffs = {"d1": d1, "d2": d2, "d3": d3, "d4": d4}

    def get_coefficients(self) -> dict:
        return self.coeffs

    def save_to_file(self, file_path: str) -> None:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "online_model": self.online_model,
            "scaler": self.scaler,
            "is_fitted": self._is_fitted,
            "coeffs": self.coeffs,
            "training_data": self._training_data,
        }
        with path.open("wb") as f:
            pickle.dump(state, f)

    def load_from_file(self, file_path: str) -> bool:
        path = Path(file_path)
        if not path.exists():
            return False

        with path.open("rb") as f:
            state = pickle.load(f)

        scaler = state["scaler"]
        if getattr(scaler, "n_features_in_", 4) != 4:
            return False

        self.online_model = state["online_model"]
        self.scaler = scaler
        self._is_fitted = bool(state.get("is_fitted", False))
        self.coeffs = state.get("coeffs", {"d1": 0.0, "d2": 0.0, "d3": 0.0, "d4": 0.0})
        self._training_data = state.get("training_data", [])
        return self._is_fitted
