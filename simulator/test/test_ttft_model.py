#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH, LinearTTFTModel, load_prefill_ttft_model  # noqa: E402
from simulator.vllm_v1_scheduler import VllmRequest, VllmV1SchedulerSimulator  # noqa: E402


class TTFTModelTest(unittest.TestCase):
    def test_load_default_prefill_ttft_model(self) -> None:
        model = load_prefill_ttft_model(DEFAULT_PREFILL_TTFT_MODEL_PATH)

        self.assertIsNotNone(model)
        self.assertGreater(model.slope_ms_per_token, 0)
        self.assertGreater(model.intercept_ms, 0)

    def test_vllm_uses_loaded_linear_ttft_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.pkl"
            import pickle

            with model_path.open("wb") as file:
                pickle.dump({"a": 0.001, "b": 0.5}, file)

            simulator = VllmV1SchedulerSimulator(
                max_num_batched_tokens=10240,
                ttft_model_path=model_path,
            )
            result = simulator.simulate([VllmRequest("id1", 0.0, 1000)])

        self.assertAlmostEqual(result.batches[0].end_ms, 1500.0)

    def test_linear_ttft_model_clamps_negative_prediction(self) -> None:
        model = LinearTTFTModel(slope_ms_per_token=0.1, intercept_ms=-1000.0)

        self.assertEqual(model.predict_ms(1), 0.0)


if __name__ == "__main__":
    unittest.main()
