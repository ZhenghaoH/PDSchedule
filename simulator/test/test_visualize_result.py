#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.experiment.pipeline import run_comparison  # noqa: E402
from simulator.result.visualize_result import render_strategy_svg, save_strategy_svgs  # noqa: E402


class VisualizeResultTest(unittest.TestCase):
    def test_render_strategy_svg_contains_timeline_and_slo_table(self) -> None:
        result = run_comparison(seed=11, num_requests=6, pso_particles=4, pso_iterations=2)

        svg = render_strategy_svg(result, "pso", max_requests=5)

        self.assertIn("<svg", svg)
        self.assertIn("pso execution timeline", svg)
        self.assertIn("request ID", svg)
        self.assertIn("prompt tokens", svg)
        self.assertIn("SLO", svg)
        self.assertIn("actual latency", svg)
        self.assertIn("SLO met?", svg)
        self.assertTrue("yes" in svg or "no" in svg)

    def test_save_strategy_svgs_uses_default_path_for_first_strategy(self) -> None:
        result = run_comparison(seed=12, num_requests=6, pso_particles=4, pso_iterations=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "latest_result.svg"

            paths = save_strategy_svgs(
                result,
                strategies=("pso", "direct"),
                output_path=output_path,
                max_requests=5,
            )

            self.assertEqual(paths[0], output_path)
            self.assertEqual(paths[1], Path(tmpdir) / "latest_result_direct.svg")
            self.assertTrue(paths[0].exists())
            self.assertTrue(paths[1].exists())
            self.assertIn("<svg", paths[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
