#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.vllm_v1_scheduler import (  # noqa: E402
    VllmRequest,
    VllmV1SchedulerSimulator,
)


class VllmV1SchedulerSimulatorTest(unittest.TestCase):
    def test_idle_first_request_is_single_batch(self) -> None:
        simulator = VllmV1SchedulerSimulator(
            max_num_batched_tokens=10240,
            token_time_ms=0.01,
            idle_batching="first",
            ttft_model_path=None,
        )
        result = simulator.simulate(
            [
                VllmRequest("id1", 0.0, 1000),
                VllmRequest("id2", 5.0, 1000),
            ]
        )

        self.assertEqual([chunk.request_id for chunk in result.batches[0].chunks], ["id1"])
        self.assertEqual(result.requests[0].final_batch_id, 1)
        self.assertEqual(result.requests[1].first_batch_id, 2)

    def test_waiting_requests_are_fcfs_batched_after_running_batch(self) -> None:
        simulator = VllmV1SchedulerSimulator(
            max_num_batched_tokens=10240,
            token_time_ms=0.01,
            idle_batching="first",
            ttft_model_path=None,
        )
        result = simulator.simulate(
            [
                VllmRequest("id1", 0.0, 10000),
                VllmRequest("id2", 10.0, 3000),
                VllmRequest("id3", 50.0, 4000),
                VllmRequest("id4", 90.0, 5000),
            ]
        )

        self.assertEqual(result.batches[0].end_ms, 100.0)
        self.assertEqual(
            [(chunk.request_id, chunk.tokens, chunk.completed) for chunk in result.batches[1].chunks],
            [("id2", 3000, True), ("id3", 4000, True), ("id4", 3240, False)],
        )
        self.assertEqual(result.batches[2].chunks[0].request_id, "id4")
        self.assertEqual(result.batches[2].chunks[0].tokens, 1760)
        self.assertEqual(result.requests[3].chunk_count, 2)

    def test_oversized_request_is_chunked_across_batches(self) -> None:
        simulator = VllmV1SchedulerSimulator(
            max_num_batched_tokens=10240,
            token_time_ms=0.0,
            idle_batching="first",
            ttft_model_path=None,
        )
        result = simulator.simulate([VllmRequest("long", 0.0, 23000)])

        self.assertEqual([batch.scheduled_tokens for batch in result.batches], [10240, 10240, 2520])
        self.assertEqual(result.requests[0].final_batch_id, 3)
        self.assertEqual(result.requests[0].chunk_count, 3)

    def test_unfinished_prefix_keeps_fcfs_position_before_later_arrivals(self) -> None:
        simulator = VllmV1SchedulerSimulator(
            max_num_batched_tokens=10240,
            token_time_ms=0.01,
            idle_batching="first",
            ttft_model_path=None,
        )
        result = simulator.simulate(
            [
                VllmRequest("id1", 0.0, 15000),
                VllmRequest("id2", 20.0, 1000),
            ]
        )

        self.assertEqual(result.batches[1].chunks[0].request_id, "id1")
        self.assertEqual(result.batches[1].chunks[0].tokens, 4760)
        self.assertEqual(result.batches[1].chunks[1].request_id, "id2")
        self.assertGreater(result.requests[0].complete_time_ms, result.requests[0].arrival_ms)

    def test_optional_window_idle_batching_is_available_for_threshold_fitting(self) -> None:
        simulator = VllmV1SchedulerSimulator(
            max_num_batched_tokens=10240,
            arrival_window_ms=20.0,
            token_time_ms=0.0,
            idle_batching="window",
            ttft_model_path=None,
        )
        result = simulator.simulate(
            [
                VllmRequest("id1", 0.0, 1000),
                VllmRequest("id2", 10.0, 1000),
                VllmRequest("id3", 30.0, 1000),
            ]
        )

        self.assertEqual([chunk.request_id for chunk in result.batches[0].chunks], ["id1", "id2"])
        self.assertEqual(result.requests[2].first_batch_id, 2)


if __name__ == "__main__":
    unittest.main()
