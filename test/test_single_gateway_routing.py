import unittest
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if "fastapi" not in sys.modules:
    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.HTTPException = type("HTTPException", (Exception,), {})
    sys.modules["fastapi"] = fastapi_stub

from core.schemas.request import BaseRequest, Prompt, Service, Task
from scheduler.controller.global_scheduler import GlobalScheduler
from scheduler.controller.strategies.demand_aware import (
    DemandAwareStrategy,
    PROFILE_ROOT,
)
from scheduler.controller.strategy import GlobalScheduleDecision
from store.controller.pool_base import DecodePool, PrefillPool, PoolType
from utils.nixl_request import build_nixl_decode_payload, build_nixl_prefill_payload


def make_request(metadata=None):
    return BaseRequest(
        Request_ID=1,
        Request_type="request",
        Prompt=Prompt("model", "hello", 5, 1, 16, True, 1.0, 1.0),
        Service=Service(Endpoint_type="chat/completions"),
        Task=Task(
            User_addr="",
            P_proxy_addr="10.0.0.1",
            P_proxy_port=8081,
            D_proxy_addr="10.0.0.2",
            D_proxy_port=8082,
            prefill_instance="",
            decode_instance="",
            batch_order=0,
            User_url_path="/v1/chat/completions",
            nixl_kv_transfer_params={"remote_engine_id": "engine"},
            scheduler_metadata=dict(metadata or {}),
        ),
    )


def make_prefill(pool_id, host, port):
    return PrefillPool(
        pool_id=pool_id,
        proxy_ip_address=host,
        proxy_port=port,
        flops=100.0,
    )


def make_decode(pool_id, host, port):
    return DecodePool(
        pool_id=pool_id,
        proxy_ip_address=host,
        proxy_port=port,
        mem_bandwidth=500.0,
    )


def make_scheduler(prefill_pools, decode_pools):
    pool_manager = Mock()
    pool_manager.get_active_Pools.side_effect = lambda pool_type: (
        list(prefill_pools)
        if pool_type == PoolType.PREFILL
        else list(decode_pools)
    )
    network_manager = Mock()
    network_manager.get_all_networks.return_value = []
    scheduler = GlobalScheduler(pool_manager, network_manager)
    scheduler.sync_state()
    return scheduler


class SingleGatewaySchedulerTest(unittest.TestCase):
    def setUp(self):
        self.prefill = make_prefill(1, "10.0.0.1", 8081)
        self.decode = make_decode(201, "10.0.0.2", 8082)

    def test_single_gateway_routes_directly_without_network_or_strategy(self):
        scheduler = make_scheduler([self.prefill], [self.decode])
        strategy = Mock()
        scheduler.strategy_manager.replace_strategy(
            scheduler.current_mode,
            strategy,
        )

        result = scheduler.schedule(Mock(), Mock(), 1)

        self.assertEqual(
            result,
            {
                "P_proxy_addr": "10.0.0.1",
                "P_proxy_port": 8081,
                "D_proxy_addr": "10.0.0.2",
                "D_proxy_port": 8082,
            },
        )
        self.assertTrue(scheduler.uses_single_gateway_route())
        self.assertIn("topology=single_gateway_direct", scheduler.routing_status())
        strategy.schedule.assert_not_called()

    def test_missing_pool_keeps_resource_validation(self):
        for prefill_pools, decode_pools in (([], [self.decode]), ([self.prefill], [])):
            with self.subTest(
                prefill_count=len(prefill_pools),
                decode_count=len(decode_pools),
            ):
                scheduler = make_scheduler(prefill_pools, decode_pools)
                with self.assertRaisesRegex(RuntimeError, "Resources empty"):
                    scheduler.schedule(Mock(), Mock(), 1)

    def test_multi_gateway_uses_selected_global_strategy(self):
        scheduler = make_scheduler(
            [self.prefill, make_prefill(2, "10.0.0.3", 8083)],
            [self.decode, make_decode(202, "10.0.0.4", 8084)],
        )
        strategy = Mock()
        strategy.schedule.return_value = GlobalScheduleDecision(
            "10.0.0.3",
            8083,
            "10.0.0.4",
            8084,
            "round_robin",
        )
        scheduler.strategy_manager.replace_strategy("round_robin", strategy)
        self.assertTrue(scheduler.switch_mode("round_robin"))

        result = scheduler.schedule(Mock(), Mock(), 2)

        self.assertEqual(result["P_proxy_addr"], "10.0.0.3")
        self.assertEqual(result["D_proxy_addr"], "10.0.0.4")
        strategy.schedule.assert_called_once()
        self.assertIn("topology=multi_gateway_scheduled", scheduler.routing_status())

    def test_topology_changes_enter_and_leave_direct_route(self):
        active_prefills = [
            self.prefill,
            make_prefill(2, "10.0.0.3", 8083),
        ]
        active_decodes = [
            self.decode,
            make_decode(202, "10.0.0.4", 8084),
        ]
        scheduler = make_scheduler(active_prefills, active_decodes)
        strategy = Mock()
        strategy.schedule.return_value = GlobalScheduleDecision(
            "10.0.0.3",
            8083,
            "10.0.0.4",
            8084,
            "round_robin",
        )
        scheduler.strategy_manager.replace_strategy("round_robin", strategy)
        scheduler.switch_mode("round_robin")

        scheduler.schedule(Mock(), Mock(), 1)
        self.assertEqual(strategy.schedule.call_count, 1)

        scheduler.pool_manager.get_active_Pools.side_effect = lambda pool_type: (
            [self.prefill] if pool_type == PoolType.PREFILL else [self.decode]
        )
        scheduler.sync_state()
        direct = scheduler.schedule(Mock(), Mock(), 2)
        self.assertEqual(direct["P_proxy_addr"], self.prefill.proxy_ip_address)
        self.assertEqual(direct["D_proxy_addr"], self.decode.proxy_ip_address)
        self.assertEqual(strategy.schedule.call_count, 1)

        scheduler.pool_manager.get_active_Pools.side_effect = lambda pool_type: (
            list(active_prefills)
            if pool_type == PoolType.PREFILL
            else list(active_decodes)
        )
        scheduler.sync_state()
        scheduler.schedule(Mock(), Mock(), 3)
        self.assertEqual(strategy.schedule.call_count, 2)

    def test_strategy_can_switch_while_single_gateway_route_is_active(self):
        scheduler = make_scheduler([self.prefill], [self.decode])

        self.assertTrue(scheduler.switch_mode("round_robin"))
        self.assertEqual(scheduler.current_mode, "round_robin")
        result = scheduler.schedule(Mock(), Mock(), 1)
        self.assertEqual(result["P_proxy_addr"], self.prefill.proxy_ip_address)

    def test_single_gateway_ignores_cached_affinity_pool_route(self):
        scheduler = make_scheduler([self.prefill], [self.decode])
        affinity_store = {
            ("user", "session"): {
                "P_proxy_addr": "old-prefill",
                "P_proxy_port": 9001,
                "D_proxy_addr": "old-decode",
                "D_proxy_port": 9002,
            }
        }
        payload = {
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 16,
            "stream": False,
            "Enable_Affinity": True,
            "user_id": "user",
            "session_id": "session",
        }

        with patch("core.schemas.request.Prompt.extract_prompt_info", return_value=5):
            request = BaseRequest.build_request(
                url_path="/v1/chat/completions",
                payload=payload,
                user_addr="127.0.0.1",
                request_id=1,
                scheduler_instance=scheduler,
                affinity_store=affinity_store,
            )

        self.assertEqual(request.Task.P_proxy_addr, "10.0.0.1")
        self.assertEqual(request.Task.D_proxy_addr, "10.0.0.2")
        self.assertEqual(affinity_store[("user", "session")]["P_proxy_addr"], "old-prefill")


class DemandAwareProfileTest(unittest.TestCase):
    def test_runtime_profiles_are_independent_from_root_simulator(self):
        self.assertEqual(PROFILE_ROOT, ROOT_DIR / "profile" / "controller")
        self.assertNotIn("simulator", PROFILE_ROOT.parts)

        expected_profiles = (
            ("166", "tpot"),
            ("167", "ttft"),
            ("169", "tpot"),
            ("169", "ttft"),
            ("170", "tpot"),
            ("170", "ttft"),
        )
        scheduler = DemandAwareStrategy(num_particles=2, max_iterations=1)
        for machine_id, kind in expected_profiles:
            with self.subTest(machine_id=machine_id, kind=kind):
                self.assertTrue(scheduler._profile_path(machine_id, kind).is_file())

        self.assertIsNotNone(
            scheduler._load_ttft_profile(make_prefill(1, "172.18.0.167", 8081))
        )
        self.assertIsNotNone(
            scheduler._load_tpot_profile(make_decode(201, "172.18.0.166", 8082))
        )


class SchedulerMetadataTest(unittest.TestCase):
    def test_default_metadata_is_not_shared(self):
        first = make_request()
        second = make_request()
        first.Task.scheduler_metadata["test"] = {"value": 1}
        self.assertEqual(second.Task.scheduler_metadata, {})

    def test_metadata_survives_internal_round_trip(self):
        request = make_request({"paper": {"class": "short", "score": 1.5}})

        recovered = BaseRequest.recover_request(
            request.to_payload(),
            endpoint="chat/completions",
        )

        self.assertEqual(recovered.Task.scheduler_metadata, request.Task.scheduler_metadata)

    def test_metadata_is_excluded_from_instance_payloads(self):
        request = make_request({"paper": {"class": "short"}})

        standard = BaseRequest.to_vllm_payload(request)
        prefill = build_nixl_prefill_payload(request)
        decode = build_nixl_decode_payload(request)

        self.assertNotIn("scheduler_metadata", standard)
        self.assertNotIn("scheduler_metadata", prefill)
        self.assertNotIn("scheduler_metadata", decode)
        self.assertIn("kv_transfer_params", prefill)
        self.assertIn("kv_transfer_params", decode)


if __name__ == "__main__":
    unittest.main()
