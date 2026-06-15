#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.pipeline import run_comparison  # noqa: E402
from simulator.schedule_log import render_schedule_log, save_schedule_log  # noqa: E402


class ScheduleLogTest(unittest.TestCase):
    def test_render_schedule_log_contains_dispatch_and_vllm_sections(self) -> None:
        result = run_comparison(
            seed=1,
            num_requests=6,
            pso_particles=4,
            pso_iterations=2,
        )

        text = render_schedule_log(result)

        self.assertIn("PSO Rolling Dispatch", text)
        self.assertIn("Dispatch 1", text)
        self.assertIn("request_ids", text)
        self.assertIn("total_tokens", text)
        self.assertIn("vLLM Batches", text)

    def test_save_schedule_log_writes_markdown(self) -> None:
        result = run_comparison(
            seed=2,
            num_requests=4,
            pso_particles=4,
            pso_iterations=2,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "latest_schedule_log.md"
            saved_path = save_schedule_log(result, output_path)

            self.assertEqual(saved_path, output_path)
            self.assertIn("Simulator Schedule Log", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
