#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.gateway.queue_manager import QueueManager  # noqa: E402
from simulator.dataset.request_workload import GeneratedRequest, generate_requests  # noqa: E402
from simulator.gateway.scheduling_strategies import DirectSchedulingStrategy, PSOSchedulingStrategy, QueueRequestView  # noqa: E402
from simulator.instance.vllm_execution_calculator import VllmQueuedRequestSnapshot, VllmQueueSnapshot  # noqa: E402


class QueueManagerTest(unittest.TestCase):
    def test_direct_strategy_dispatches_arrived_pending_requests(self) -> None:
        requests = generate_requests(seed=0, num_requests=5, arrival_mode="all_at_once")
        result = QueueManager(DirectSchedulingStrategy()).run(requests)

        self.assertEqual(len(result.dispatch_records), 1)
        self.assertEqual(result.dispatch_records[0].dispatch_time_ms, 0.0)
        self.assertEqual(
            result.dispatch_records[0].request_ids,
            [req.request_id for req in requests],
        )
        self.assertEqual(len(result.vllm_requests), 5)

    def test_pso_strategy_dispatches_through_queue_records(self) -> None:
        requests = generate_requests(seed=1, num_requests=6, arrival_mode="all_at_once")
        result = QueueManager(
            PSOSchedulingStrategy(pso_particles=4, pso_iterations=2, random_seed=1)
        ).run(requests)

        self.assertGreaterEqual(len(result.dispatch_records), 1)
        self.assertEqual(
            sorted(req.request_id for req in result.vllm_requests),
            sorted(req.request_id for req in requests),
        )
        self.assertTrue(result.dispatch_records)
        self.assertTrue(all(state.dispatch_time_ms is not None for state in result.request_states))

    def test_request_level_pso_dispatches_complete_requests_only(self) -> None:
        requests = generate_requests(seed=2, num_requests=8, arrival_mode="all_at_once")
        request_tokens = {req.request_id: req.prompt_tokens for req in requests}
        result = QueueManager(
            PSOSchedulingStrategy(pso_particles=4, pso_iterations=2, random_seed=2)
        ).run(requests)

        self.assertTrue(result.dispatch_records)
        for record in result.dispatch_records:
            self.assertEqual(
                record.total_tokens,
                sum(request_tokens[request_id] for request_id in record.request_ids),
            )
        self.assertEqual(
            sorted(req.request_id for req in result.vllm_requests),
            sorted(request_tokens),
        )

    def test_pso_rolling_dispatches_waiting_before_running_finishes(self) -> None:
        requests = generate_requests(
            seed=3,
            num_requests=8,
            arrival_mode="all_at_once",
            prompt_token_source="synthetic",
        )
        result = QueueManager(
            PSOSchedulingStrategy(
                max_batch_tokens=3000,
                pso_particles=4,
                pso_iterations=2,
                random_seed=3,
            ),
            max_num_batched_tokens=3000,
        ).run(requests)

        self.assertGreaterEqual(len(result.dispatch_records), 2)
        self.assertEqual(result.dispatch_records[0].role, "running")
        waiting_records = [
            record
            for record in result.dispatch_records
            if record.role == "waiting"
        ]
        self.assertTrue(waiting_records)
        self.assertTrue(
            any(
                waiting.dispatch_time_ms <= running.actual_finish_ms
                for waiting in waiting_records
                for running in result.dispatch_records
                if running.role == "running" and running.actual_finish_ms is not None
            )
        )
        self.assertIsNotNone(result.vllm_result)
        self.assertTrue(
            all(record.actual_finish_ms is not None for record in result.dispatch_records)
        )

    def test_dispatch_actual_finish_aligns_with_vllm_result(self) -> None:
        requests = generate_requests(
            seed=5,
            num_requests=8,
            arrival_mode="all_at_once",
            prompt_token_source="synthetic",
        )
        result = QueueManager(
            PSOSchedulingStrategy(pso_particles=4, pso_iterations=2, random_seed=5)
        ).run(requests)
        complete_by_id = {
            request.request_id: request.complete_time_ms
            for request in result.vllm_result.requests
        }

        for record in result.dispatch_records:
            self.assertEqual(
                record.actual_finish_ms,
                round(max(complete_by_id[request_id] for request_id in record.request_ids), 6),
            )

    def test_pso_dispatches_underfilled_batch_when_idle(self) -> None:
        requests = generate_requests(
            seed=4,
            num_requests=1,
            arrival_mode="qps",
            qps=1,
            prompt_token_source="synthetic",
        )
        result = QueueManager(
            PSOSchedulingStrategy(pso_particles=4, pso_iterations=2, random_seed=4)
        ).run(requests)

        self.assertEqual(len(result.dispatch_records), 1)
        self.assertEqual(result.dispatch_records[0].role, "running")
        self.assertLess(result.dispatch_records[0].total_tokens, 10240)
        self.assertEqual(
            result.dispatch_records[0].dispatch_time_ms,
            requests[0].arrival_ms,
        )

    def test_rolling_waiting_absorbs_ready_requests_until_budget(self) -> None:
        requests = [
            GeneratedRequest("req-001", 0.0, 1000, 5000.0),
            GeneratedRequest("req-002", 10.0, 100, 5000.0),
            GeneratedRequest("req-003", 20.0, 100, 5000.0),
            GeneratedRequest("req-004", 30.0, 100, 5000.0),
        ]
        result = QueueManager(
            PSOSchedulingStrategy(
                max_batch_tokens=10000,
                token_time_ms=1.0,
                pso_particles=1,
                pso_iterations=1,
                random_seed=0,
                ttft_model_path=None,
            ),
            max_num_batched_tokens=10000,
            token_time_ms=1.0,
            ttft_model_path=None,
        ).run(requests)
        batch_chunks = {
            batch.batch_id: [chunk.request_id for chunk in batch.chunks]
            for batch in result.vllm_result.batches
        }

        self.assertEqual(batch_chunks[1], ["req-001"])
        self.assertEqual(batch_chunks[2], ["req-002", "req-003", "req-004"])

    def test_pso_plan_accounts_for_vllm_snapshot_waiting_work(self) -> None:
        strategy = PSOSchedulingStrategy(
            max_batch_tokens=10240,
            token_time_ms=1.0,
            batch_overhead_ms=0.0,
            pso_particles=1,
            pso_iterations=1,
            random_seed=0,
            ttft_model_path=None,
        )
        pending = [
            QueueRequestView(
                request_id="new",
                original_arrival_ms=0.0,
                prompt_tokens=100,
                slo_ttft_ms=5000.0,
            )
        ]
        snapshot = VllmQueueSnapshot(
            current_time_ms=0.0,
            running_end_ms=500.0,
            queued_requests=[
                VllmQueuedRequestSnapshot(
                    request_id="waiting",
                    arrival_ms=0.0,
                    remaining_tokens=1000,
                    sequence=1,
                )
            ],
        )

        no_snapshot = strategy.plan(pending, 0.0)
        with_snapshot = strategy.plan(pending, 0.0, vllm_snapshot=snapshot)

        self.assertEqual(no_snapshot.planned_requests[0].planned_finish_ms, 100.0)
        self.assertEqual(with_snapshot.planned_requests[0].planned_finish_ms, 1600.0)


if __name__ == "__main__":
    unittest.main()
