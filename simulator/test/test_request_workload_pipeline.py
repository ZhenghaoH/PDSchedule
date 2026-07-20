#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from collections import deque
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.dataset.request_workload import generate_requests, load_burstgpt_request_tokens, requests_to_dicts  # noqa: E402
from simulator.experiment.pipeline import load_latest_result, run_comparison, save_latest_result  # noqa: E402
from simulator.controller.joint_controller_instance_simulation import (  # noqa: E402
    DIST_SERVE_DIRECT_PREFILL_BATCH_TOKENS,
    POLICIES,
    simulate,
    take_distserve_prefill_batch,
)


class RequestWorkloadPipelineTest(unittest.TestCase):
    def test_distserve_is_available_in_joint_comparison(self) -> None:
        self.assertIn("distserve", POLICIES)
        self.assertIn("distserve_batched", POLICIES)

    def test_distserve_lm_batching_is_fcfs_and_does_not_skip(self) -> None:
        pending = deque(
            [
                {"request_id": "req-1", "prompt_tokens": 1000},
                {"request_id": "req-2", "prompt_tokens": 1200},
                {"request_id": "req-3", "prompt_tokens": 500},
            ]
        )

        first = take_distserve_prefill_batch(pending, 2048)
        second = take_distserve_prefill_batch(pending, 2048)

        self.assertEqual([item["request_id"] for item in first], ["req-1"])
        self.assertEqual([item["request_id"] for item in second], ["req-2", "req-3"])

    def test_distserve_request_at_lm_or_above_runs_alone(self) -> None:
        pending = deque(
            [
                {"request_id": "long", "prompt_tokens": 2048},
                {"request_id": "short", "prompt_tokens": 64},
            ]
        )

        batch = take_distserve_prefill_batch(pending, 2048)

        self.assertEqual([item["request_id"] for item in batch], ["long"])
        self.assertEqual(pending[0]["request_id"], "short")

    def _distserve_simulation_args(self, dataset: Path, policy: str) -> Namespace:
        return Namespace(
            dataset=dataset,
            limit=None,
            scenario="dynamic",
            policy=policy,
            qps=None,
            time_scale=1.0,
            slo_ttft_ms=6000.0,
            slo_tpot_ms=40.0,
            slo_e2e_ms=21500.0,
            max_num_batched_tokens=4096,
            distserve_prefill_batch_tokens=2048,
            local_bandwidth_mbps=36000.0,
            cross_bandwidth_mbps=1000.0,
            compress_factor=1.0,
        )

    def test_distserve_batched_joint_simulation_keeps_gateway_lm_batching(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset = Path(tmpdir) / "requests.csv"
            dataset.write_text(
                "Timestamp,Request tokens,Response tokens\n"
                "0,1000,2\n"
                "0,800,2\n"
                "0,400,2\n"
                "0,2500,2\n",
                encoding="utf-8",
            )
            result = simulate(self._distserve_simulation_args(dataset, "distserve_batched"))

        self.assertEqual(result["requests"], 4)
        self.assertEqual(result["distserve"]["mode"], "gateway_fcfs_batching")
        self.assertEqual(result["distserve"]["prefill_max_num_batched_tokens"], 4096)
        self.assertEqual(len(result["request_records"]), 4)
        self.assertEqual(sum(result["pair_counts"].values()), 4)
        self.assertEqual(
            sum(result["decode_instance_counts"].values()),
            4,
        )

    def test_distserve_direct_jsq_uses_configured_vllm_budget_without_gateway_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset = Path(tmpdir) / "requests.csv"
            dataset.write_text(
                "Timestamp,Request tokens,Response tokens\n"
                "0,1000,2\n"
                "0,800,2\n"
                "0,400,2\n"
                "0,2500,2\n",
                encoding="utf-8",
            )
            result = simulate(self._distserve_simulation_args(dataset, "distserve"))

        self.assertEqual(result["requests"], 4)
        self.assertEqual(result["distserve"]["mode"], "direct_jsq_vllm_batching")
        self.assertEqual(
            result["distserve"]["prefill_max_num_batched_tokens"],
            DIST_SERVE_DIRECT_PREFILL_BATCH_TOKENS,
        )
        self.assertNotIn("duration_s", result)
        self.assertNotIn("prefill_batches", result)
        self.assertNotIn("gateway_batch_count", result["distserve"])
        self.assertNotIn("gateway_batches", result["distserve"])
        self.assertEqual(sum(result["prefill_instance_counts"].values()), 4)
        self.assertEqual(sorted(result["prefill_instance_counts"].values()), [1, 1, 1, 1])
        self.assertEqual(sum(result["decode_instance_counts"].values()), 4)
        self.assertEqual(len(result["request_records"]), 4)

    def test_seed_reproduces_same_50_requests(self) -> None:
        first = generate_requests(seed=42)
        second = generate_requests(seed=42)

        self.assertEqual(len(first), 50)
        self.assertEqual(requests_to_dicts(first), requests_to_dicts(second))

    def test_burstgpt_loader_reads_only_requested_valid_request_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "burst.csv"
            csv_path.write_text(
                "Timestamp,Request tokens\n"
                "1,0\n"
                "2,128\n"
                "3,bad\n"
                "4,256\n"
                "5,512\n",
                encoding="utf-8",
            )

            tokens = load_burstgpt_request_tokens(2, csv_path)

        self.assertEqual(tokens, [128, 256])

    def test_generate_requests_uses_burstgpt_tokens_in_csv_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "burst.csv"
            csv_path.write_text(
                "Timestamp,Request tokens\n"
                "1,101\n"
                "2,202\n"
                "3,303\n",
                encoding="utf-8",
            )

            requests = generate_requests(
                seed=0,
                num_requests=3,
                prompt_token_source="burstgpt",
                burstgpt_csv_path=csv_path,
            )

        self.assertEqual([req.prompt_tokens for req in requests], [101, 202, 303])

    def test_different_seed_changes_requests(self) -> None:
        first = requests_to_dicts(generate_requests(seed=1))
        second = requests_to_dicts(generate_requests(seed=2))

        self.assertNotEqual(first, second)

    def test_prompt_lengths_are_heterogeneous(self) -> None:
        requests = generate_requests(seed=0, prompt_token_source="synthetic")
        lengths = [req.prompt_tokens for req in requests]

        self.assertGreater(len(set(lengths)), 10)
        self.assertTrue(any(length <= 2048 for length in lengths))
        self.assertTrue(any(2049 <= length <= 8192 for length in lengths))
        self.assertTrue(any(length >= 8193 for length in lengths))

    def test_all_at_once_arrival_mode_sets_all_arrivals_to_zero(self) -> None:
        requests = generate_requests(seed=0, num_requests=12, arrival_mode="all_at_once")

        self.assertEqual({req.arrival_ms for req in requests}, {0.0})

    def test_qps_arrival_mode_keeps_each_second_window_at_qps(self) -> None:
        requests = generate_requests(seed=0, num_requests=20, arrival_mode="qps", qps=10)
        first_window = requests[:10]
        second_window = requests[10:20]

        self.assertTrue(all(0.0 <= req.arrival_ms < 1000.0 for req in first_window))
        self.assertTrue(all(1000.0 <= req.arrival_ms < 2000.0 for req in second_window))
        self.assertEqual(
            [req.arrival_ms for req in first_window],
            sorted(req.arrival_ms for req in first_window),
        )
        self.assertEqual(
            [req.arrival_ms for req in second_window],
            sorted(req.arrival_ms for req in second_window),
        )

    def test_arrival_mode_does_not_change_prompt_or_slo_samples(self) -> None:
        all_at_once = generate_requests(seed=13, num_requests=8, arrival_mode="all_at_once")
        qps = generate_requests(seed=13, num_requests=8, arrival_mode="qps", qps=10)

        self.assertEqual(
            [(req.prompt_tokens, req.slo_ttft_ms) for req in all_at_once],
            [(req.prompt_tokens, req.slo_ttft_ms) for req in qps],
        )

    def test_pipeline_records_arrival_config(self) -> None:
        result = run_comparison(
            seed=2,
            num_requests=10,
            arrival_mode="all_at_once",
            prompt_token_source="synthetic",
            pso_particles=4,
            pso_iterations=2,
        )

        self.assertEqual(result["arrival"], {"mode": "all_at_once", "qps": 10})
        self.assertEqual({req["arrival_ms"] for req in result["workload"]}, {0.0})

    def test_pipeline_can_set_uniform_slo(self) -> None:
        result = run_comparison(
            seed=2,
            num_requests=6,
            min_slo_ttft_ms=500.0,
            max_slo_ttft_ms=500.0,
            strategy="direct",
        )

        self.assertEqual(result["slo"], {"min_ttft_ms": 500.0, "max_ttft_ms": 500.0})
        self.assertEqual({req["slo_ttft_ms"] for req in result["workload"]}, {500.0})

    def test_pipeline_outputs_direct_and_pso_comparison(self) -> None:
        result = run_comparison(
            seed=3,
            num_requests=50,
            prompt_token_source="synthetic",
            pso_particles=8,
            pso_iterations=4,
        )

        self.assertEqual(result["num_requests"], 50)
        self.assertIn("direct", result["runs"])
        self.assertIn("pso", result["runs"])
        self.assertIn("queue_result", result["runs"]["direct"])
        self.assertIn("queue_result", result["runs"]["pso"])
        self.assertIn("dispatch_records", result["runs"]["pso"]["queue_result"])
        self.assertEqual(
            result["runs"]["pso"]["vllm_result"],
            result["runs"]["pso"]["queue_result"]["vllm_result"],
        )
        self.assertIn("comparison", result)
        self.assertIn("goodput_tokens_delta", result["comparison"])
        self.assertIn("goodput_ratio_delta", result["comparison"])
        self.assertIn("slo_attainment_rate_delta", result["comparison"])

    def test_direct_dispatches_immediately_on_original_arrival(self) -> None:
        result = run_comparison(
            seed=10,
            num_requests=12,
            arrival_mode="qps",
            qps=10,
            prompt_token_source="synthetic",
            strategy="direct",
        )
        workload = {req["request_id"]: req for req in result["workload"]}
        direct_requests = result["runs"]["direct"]["requests"]
        dispatch_records = result["runs"]["direct"]["queue_result"]["dispatch_records"]

        self.assertTrue(
            all(
                req["dispatch_time_ms"] == workload[req["request_id"]]["arrival_ms"]
                for req in direct_requests
            )
        )
        self.assertTrue(
            all(
                record["dispatch_time_ms"] == workload[record["request_ids"][0]]["arrival_ms"]
                for record in dispatch_records
            )
        )

    def test_pso_uses_scheduled_arrivals_while_direct_uses_original_arrivals(self) -> None:
        result = run_comparison(
            seed=4,
            num_requests=12,
            arrival_mode="all_at_once",
            prompt_token_source="synthetic",
            pso_particles=6,
            pso_iterations=3,
        )
        workload = {req["request_id"]: req for req in result["workload"]}
        direct_requests = result["runs"]["direct"]["requests"]
        pso_requests = result["runs"]["pso"]["requests"]

        self.assertTrue(
            all(
                req["scheduled_arrival_ms"] == workload[req["request_id"]]["arrival_ms"]
                for req in direct_requests
            )
        )
        self.assertTrue(
            any(
                req["scheduled_arrival_ms"] != workload[req["request_id"]]["arrival_ms"]
                for req in pso_requests
            )
        )
        self.assertTrue(
            all(
                req["scheduled_arrival_ms"] >= workload[req["request_id"]]["arrival_ms"]
                for req in pso_requests
            )
        )
        self.assertTrue(all(req["ttft_ms"] >= 0 for req in pso_requests))

    def test_pso_queue_dispatches_complete_requests_when_vllm_spans_chunks(self) -> None:
        result = run_comparison(
            seed=0,
            num_requests=20,
            arrival_mode="all_at_once",
            prompt_token_source="synthetic",
            max_num_batched_tokens=10240,
            pso_particles=6,
            pso_iterations=3,
        )
        workload = {req["request_id"]: req for req in result["workload"]}
        pso_requests = result["runs"]["pso"]["requests"]
        queue_result = result["runs"]["pso"]["queue_result"]
        pso_dispatches = queue_result["dispatch_records"]
        vllm_requests = result["runs"]["pso"]["vllm_result"]["requests"]

        self.assertEqual(len(pso_requests), len({req["request_id"] for req in pso_requests}))
        self.assertEqual(
            sorted(
                request_id
                for dispatch in pso_dispatches
                for request_id in dispatch["request_ids"]
            ),
            sorted(workload),
        )
        self.assertTrue(
            all(
                dispatch["total_tokens"]
                == sum(workload[request_id]["prompt_tokens"] for request_id in dispatch["request_ids"])
                for dispatch in pso_dispatches
            )
        )
        long_request_ids = {
            request_id
            for request_id, req in workload.items()
            if req["prompt_tokens"] > 10240
        }
        self.assertTrue(long_request_ids)
        self.assertTrue(
            all(
                req["chunk_count"] > 1
                for req in vllm_requests
                if req["request_id"] in long_request_ids
            )
        )

    def test_same_seed_reproduces_goodput_summary(self) -> None:
        first = run_comparison(seed=5, num_requests=20, prompt_token_source="synthetic", pso_particles=6, pso_iterations=3)
        second = run_comparison(seed=5, num_requests=20, prompt_token_source="synthetic", pso_particles=6, pso_iterations=3)

        self.assertEqual(first["runs"]["direct"]["summary"], second["runs"]["direct"]["summary"])
        self.assertEqual(first["runs"]["pso"]["summary"], second["runs"]["pso"]["summary"])
        self.assertEqual(first["comparison"], second["comparison"])

    def test_save_and_load_latest_result(self) -> None:
        result = run_comparison(seed=7, num_requests=8, prompt_token_source="synthetic", pso_particles=4, pso_iterations=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "latest_result.json"
            saved_path = save_latest_result(result, output_path)
            loaded = load_latest_result(saved_path)

        self.assertEqual(saved_path, output_path)
        self.assertEqual(loaded["seed"], 7)
        self.assertEqual(loaded["num_requests"], 8)
        self.assertIn("runs", loaded)

    def test_save_latest_result_overwrites_previous_file(self) -> None:
        first = run_comparison(seed=8, num_requests=6, prompt_token_source="synthetic", pso_particles=4, pso_iterations=2)
        second = run_comparison(seed=9, num_requests=6, prompt_token_source="synthetic", pso_particles=4, pso_iterations=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "latest_result.json"
            save_latest_result(first, output_path)
            save_latest_result(second, output_path)
            loaded = load_latest_result(output_path)

        self.assertEqual(loaded["seed"], 9)


if __name__ == "__main__":
    unittest.main()
