#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.gateway.prefill_pso_scheduler import (  # noqa: E402
    PrefillPSOScheduler,
    PrefillSimInstance,
    PrefillSimRequest,
)


class PrefillPSOSchedulerTest(unittest.TestCase):
    def test_schedules_requests_to_single_prefill_instance(self) -> None:
        scheduler = PrefillPSOScheduler(
            pso_particles=6,
            pso_iterations=3,
            random_seed=7,
            token_time_ms=0.01,
        )
        instance = PrefillSimInstance("prefill-a", available_time_ms=20.0)
        result = scheduler.schedule(
            [
                PrefillSimRequest("id1", 0.0, 4096, 200.0),
                PrefillSimRequest("id2", 5.0, 2048, 160.0),
                PrefillSimRequest("id3", 8.0, 1024, 120.0),
            ],
            instance,
            current_time_ms=10.0,
        )

        self.assertEqual(result.target_instance, "prefill-a")
        self.assertTrue(result.batches)
        self.assertEqual({req.prefill_instance for req in result.requests}, {"prefill-a"})
        self.assertGreaterEqual(result.batches[0].start_ms, 20.0)

    def test_batch_token_budget_is_enforced_after_pso_decode(self) -> None:
        scheduler = PrefillPSOScheduler(
            max_batch_tokens=10000,
            pso_particles=4,
            pso_iterations=2,
            random_seed=1,
            token_time_ms=0.01,
        )
        result = scheduler.schedule(
            [
                PrefillSimRequest("id1", 0.0, 7000, 500.0),
                PrefillSimRequest("id2", 1.0, 6000, 500.0),
                PrefillSimRequest("id3", 2.0, 1000, 500.0),
            ],
            PrefillSimInstance("prefill-a"),
        )

        self.assertTrue(result.batches)
        self.assertTrue(all(batch.total_tokens <= 10000 for batch in result.batches))
        self.assertEqual(
            sorted(req.request_id for req in result.requests),
            ["id1", "id2", "id3"],
        )

    def test_chunks_do_not_wait_for_future_requests_to_fill_budget(self) -> None:
        scheduler = PrefillPSOScheduler(
            max_batch_tokens=10240,
            pso_particles=1,
            pso_iterations=1,
            random_seed=3,
            token_time_ms=0.01,
        )
        result = scheduler.schedule(
            [
                PrefillSimRequest("id1", 0.0, 1000, 1000.0),
                PrefillSimRequest("id2", 100.0, 1000, 1000.0),
            ],
            PrefillSimInstance("prefill-a"),
        )

        self.assertEqual(result.batches[0].request_ids, ["id1"])
        self.assertEqual(result.batches[0].total_tokens, 1000)
        self.assertLess(result.batches[0].total_tokens, 10240)
        self.assertGreaterEqual(result.batches[1].start_ms, 100.0)

    def test_pso_batches_only_include_arrived_requests(self) -> None:
        scheduler = PrefillPSOScheduler(
            max_batch_tokens=10240,
            pso_particles=6,
            pso_iterations=3,
            random_seed=3,
            token_time_ms=0.01,
        )
        requests = [
            PrefillSimRequest("id1", 0.0, 7000, 1000.0),
            PrefillSimRequest("id2", 100.0, 6000, 1000.0),
            PrefillSimRequest("id3", 200.0, 3000, 1000.0),
        ]
        arrivals = {req.request_id: req.arrival_ms for req in requests}
        result = scheduler.schedule(requests, PrefillSimInstance("prefill-a"))

        for batch in result.batches:
            for request_id in batch.request_ids:
                self.assertLessEqual(arrivals[request_id], batch.start_ms)

    def test_oversized_request_can_span_multiple_full_chunks(self) -> None:
        scheduler = PrefillPSOScheduler(
            max_batch_tokens=10240,
            pso_particles=4,
            pso_iterations=2,
            random_seed=0,
            token_time_ms=0.01,
        )
        result = scheduler.schedule(
            [PrefillSimRequest("long", 0.0, 23000, 1000.0)],
            PrefillSimInstance("prefill-a"),
        )

        self.assertEqual([batch.total_tokens for batch in result.batches], [10240, 10240, 2520])
        self.assertEqual([req.request_id for req in result.requests], ["long"])
        self.assertEqual(result.requests[0].batch_order, 3)

    def test_fixed_random_seed_is_reproducible(self) -> None:
        requests = [
            PrefillSimRequest("id1", 0.0, 4096, 200.0),
            PrefillSimRequest("id2", 5.0, 8192, 300.0),
            PrefillSimRequest("id3", 10.0, 1024, 150.0),
        ]
        scheduler_a = PrefillPSOScheduler(pso_particles=8, pso_iterations=4, random_seed=42)
        scheduler_b = PrefillPSOScheduler(pso_particles=8, pso_iterations=4, random_seed=42)

        result_a = scheduler_a.schedule(requests, PrefillSimInstance("prefill-a"))
        result_b = scheduler_b.schedule(requests, PrefillSimInstance("prefill-a"))

        self.assertEqual(result_a.to_dict(), result_b.to_dict())

    def test_last_plan_can_seed_batch_order(self) -> None:
        scheduler = PrefillPSOScheduler(
            max_batch_tokens=100,
            pso_particles=1,
            pso_iterations=1,
            random_seed=0,
            token_time_ms=0.0,
        )
        result = scheduler.schedule(
            [
                PrefillSimRequest("old-a", 0.0, 100, 1000.0),
                PrefillSimRequest("old-b", 1.0, 100, 1000.0),
                PrefillSimRequest("new-c", 2.0, 100, 1000.0),
            ],
            PrefillSimInstance("prefill-a"),
            last_plan={"old-a": 1, "old-b": 2},
        )

        orders = {req.request_id: req.batch_order for req in result.requests}
        self.assertLess(orders["old-a"], orders["old-b"])
        self.assertLess(orders["old-b"], orders["new-c"])

    def test_inactive_instance_is_rejected(self) -> None:
        scheduler = PrefillPSOScheduler()
        with self.assertRaises(ValueError):
            scheduler.schedule(
                [PrefillSimRequest("id1", 0.0, 100, 1000.0)],
                PrefillSimInstance("prefill-a", active=False),
            )


if __name__ == "__main__":
    unittest.main()
