import ast
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scheduler.controller.manager import StrategyManager
from scheduler.controller.resource_state import (
    ResourceSnapshot,
    ResourceStateManager,
)
from scheduler.controller.strategy import (
    GlobalScheduleContext,
    GlobalScheduleDecision,
)
from scheduler.controller.strategies.demand_aware import DemandAwareStrategy
from scheduler.controller.strategies.least_load import LeastLoadStrategy
from scheduler.controller.strategies.round_robin import RoundRobinStrategy
from store.controller.network import Network
from store.controller.pool_base import DecodePool, PoolType, PrefillPool


def make_context():
    return GlobalScheduleContext(
        token_length=128,
        max_tokens=32,
        slo_ttft=100.0,
        slo_tpot=20.0,
        compress_factor=1.0,
        request_id=1,
    )


class FakePoolManager:
    def __init__(self, prefills=(), decodes=()):
        self.db_lock = threading.RLock()
        self.prefills = list(prefills)
        self.decodes = list(decodes)

    def get_active_Pools(self, pool_type):
        if pool_type == PoolType.PREFILL:
            return list(self.prefills)
        return list(self.decodes)


class FakeNetworkManager:
    def __init__(self, networks=()):
        self.db_lock = threading.RLock()
        self.networks = list(networks)

    def get_all_networks(self):
        return list(self.networks)


class RecordingStrategy:
    name = "round_robin"

    def __init__(self):
        self.calls = []

    def schedule(self, context, resources):
        self.calls.append((context, resources))
        prefill = resources.prefill_pools[0]
        decode = resources.decode_pools[0]
        return GlobalScheduleDecision(
            prefill.proxy_ip_address,
            prefill.proxy_port,
            decode.proxy_ip_address,
            decode.proxy_port,
            self.name,
        )


class StrategyManagerTest(unittest.TestCase):
    def test_only_expected_strategies_are_available(self):
        manager = StrategyManager()

        self.assertEqual(manager.strategy, "demand_aware")
        self.assertEqual(
            set(manager.available_strategies),
            {"round_robin", "least_load", "demand_aware"},
        )
        for removed in (
            "pso",
            "random",
            "paired_round_robin",
            "adaptive_variance",
        ):
            with self.subTest(removed=removed):
                self.assertFalse(manager.switch_strategy(removed))
                with self.assertRaises(ValueError):
                    manager.get_strategy(removed)

    def test_manager_dispatches_through_common_contract(self):
        prefill = PrefillPool(1, "p", 8001, flops=1.0)
        decode = DecodePool(201, "d", 9001, mem_bandwidth=1.0)
        resources = ResourceSnapshot(
            version=1,
            captured_at=0.0,
            prefill_pools=(prefill,),
            decode_pools=(decode,),
            networks=(),
            network_map={},
        )
        implementation = RecordingStrategy()
        manager = StrategyManager("round_robin")
        manager.replace_strategy("round_robin", implementation)

        result = manager.schedule(make_context(), resources)

        self.assertEqual(result.prefill_address, "p")
        self.assertEqual(len(implementation.calls), 1)


class ConcreteStrategyTest(unittest.TestCase):
    def setUp(self):
        self.prefills = (
            PrefillPool(
                1,
                "172.18.0.167",
                8001,
                current_load=0.8,
                flops=100.0,
            ),
            PrefillPool(
                2,
                "172.18.0.169",
                8002,
                current_load=0.2,
                flops=100.0,
            ),
        )
        self.decodes = (
            DecodePool(
                201,
                "172.18.0.166",
                9001,
                current_load=0.7,
                mem_bandwidth=500.0,
            ),
            DecodePool(
                202,
                "172.18.0.170",
                9002,
                current_load=0.1,
                mem_bandwidth=500.0,
            ),
        )
        networks = tuple(
            Network(prefill, decode, bandwidth=1000.0, rtt=1.0)
            for prefill in self.prefills
            for decode in self.decodes
        )
        self.resources = ResourceSnapshot(
            version=1,
            captured_at=0.0,
            prefill_pools=self.prefills,
            decode_pools=self.decodes,
            networks=networks,
            network_map={
                (
                    network.prefill_pool.pool_id,
                    network.decode_pool.pool_id,
                ): network
                for network in networks
            },
        )

    def test_round_robin_and_least_load_keep_selection_behavior(self):
        round_robin = RoundRobinStrategy()
        first = round_robin.schedule(make_context(), self.resources)
        second = round_robin.schedule(make_context(), self.resources)
        least_load = LeastLoadStrategy().schedule(
            make_context(),
            self.resources,
        )

        self.assertEqual(
            (first.prefill_address, first.decode_address),
            ("172.18.0.167", "172.18.0.166"),
        )
        self.assertEqual(
            (second.prefill_address, second.decode_address),
            ("172.18.0.167", "172.18.0.170"),
        )
        self.assertEqual(
            (least_load.prefill_address, least_load.decode_address),
            ("172.18.0.169", "172.18.0.170"),
        )

    def test_demand_aware_accepts_the_common_contract(self):
        strategy = DemandAwareStrategy(num_particles=2, max_iterations=1)

        with patch(
            "scheduler.controller.strategies.demand_aware._emit_debug_log"
        ):
            decision = strategy.schedule(make_context(), self.resources)

        self.assertEqual(decision.strategy, "demand_aware")
        self.assertIn(
            decision.prefill_address,
            {pool.proxy_ip_address for pool in self.prefills},
        )
        self.assertIn(
            decision.decode_address,
            {pool.proxy_ip_address for pool in self.decodes},
        )


class ResourceStateManagerTest(unittest.TestCase):
    def test_refresh_publishes_versioned_consistent_copy(self):
        prefill = PrefillPool(1, "p", 8001, flops=1.0)
        decode = DecodePool(201, "d", 9001, mem_bandwidth=1.0)
        network = Network(prefill, decode, bandwidth=1000.0, rtt=1.0)
        pool_manager = FakePoolManager([prefill], [decode])
        network_manager = FakeNetworkManager([network])
        state = ResourceStateManager(pool_manager, network_manager)

        first = state.refresh()
        prefill.current_load = 0.75
        second = state.refresh()

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(first.prefill_pools[0].current_load, 0.0)
        self.assertEqual(second.prefill_pools[0].current_load, 0.75)
        copied_network = second.network_map[(1, 201)]
        self.assertIs(copied_network.prefill_pool, second.prefill_pools[0])
        self.assertIs(copied_network.decode_pool, second.decode_pools[0])

    def test_reader_never_observes_half_published_snapshot(self):
        prefill = PrefillPool(1, "p", 8001, flops=1.0)
        decode = DecodePool(201, "d", 9001, mem_bandwidth=1.0)
        pools = FakePoolManager([prefill], [decode])
        networks = FakeNetworkManager([Network(prefill, decode, 1.0, 1.0)])
        state = ResourceStateManager(pools, networks)
        state.refresh()
        failures = []

        def refresh_many():
            for _ in range(50):
                state.refresh()

        thread = threading.Thread(target=refresh_many)
        thread.start()
        while thread.is_alive():
            snapshot = state.snapshot()
            if len(snapshot.prefill_pools) != len(snapshot.decode_pools):
                failures.append(snapshot.version)
            if snapshot.networks and not snapshot.network_map:
                failures.append(snapshot.version)
        thread.join()

        self.assertEqual(failures, [])


class ResourceUpdateNotificationTest(unittest.TestCase):
    def test_all_resource_mutation_handlers_publish_after_success(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "handlers"
            / "controller"
            / "update.py"
        )
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        expected = (
            "_unregister_pool",
            "update_decode_pool_status",
            "register_decode_pool",
            "update_prefill_pool_status",
            "register_prefill_pool",
            "update_network_status",
        )
        for name in expected:
            with self.subTest(handler=name):
                calls = [
                    node
                    for node in ast.walk(functions[name])
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_refresh_scheduler_resources"
                ]
                self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
