import threading
import unittest
import sys
import importlib
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from store.gateway.manager import GatewayStoreManager
from store.gateway.instance import Instance
import config.ADDRESS_CONFIG as address_config


def nixl_connector(host="10.0.0.1", port=5557, tp_size=1):
    return {
        "name": "nixl",
        "version": "1",
        "metadata": {
            "side_channel_host": host,
            "side_channel_port": port,
            "tp_size": tp_size,
        },
    }


def import_update_handler(module_name):
    return importlib.import_module(module_name)


class RuntimeInstanceRegistrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = object.__new__(GatewayStoreManager)
        self.manager._data_lock = threading.RLock()
        self.manager._prefill_objs = []
        self.manager._decode_objs = []
        self.manager._instance_table = {}
        self.manager._pool_id = None
        self.manager.connector = {
            "name": "nixl", "version": "1", "metadata": {}
        }

    def test_registers_nixl_metadata_without_static_topology(self) -> None:
        instance = self.manager.register_instance(
            instance_ip="10.0.0.2:7001",
            role="prefill",
            connector=nixl_connector("10.0.0.2", 5557, 2),
            vllm_port=8001,
            gpu_indices=[2, 3],
        )

        self.assertEqual(instance.role, "prefill")
        self.assertFalse(instance.online)
        self.assertIsNone(instance.last_heartbeat_monotonic)
        self.assertEqual(instance.gpu_indices, [2, 3])
        self.assertEqual(self.manager.prefill_objs, ["10.0.0.2:7001"])
        self.assertEqual(self.manager.active_prefill_objs, [])
        self.assertEqual(
            self.manager.get_connector_metadata("10.0.0.2:7001"),
            {
                "role": "prefill",
                "descriptor": nixl_connector("10.0.0.2", 5557, 2),
            },
        )

    def test_duplicate_registration_is_idempotent(self) -> None:
        kwargs = {
            "instance_ip": "10.0.0.3:7002",
            "role": "decode",
            "connector": nixl_connector("10.0.0.3", 5558, 1),
        }
        self.manager.register_instance(**kwargs)
        self.manager.register_instance(**kwargs)

        self.assertEqual(self.manager.decode_objs, ["10.0.0.3:7002"])

    def test_rejects_role_conflict(self) -> None:
        self.manager.register_instance(
            "10.0.0.4:7001", "prefill", nixl_connector("10.0.0.4")
        )

        with self.assertRaisesRegex(ValueError, "already registered as prefill"):
            self.manager.register_instance(
                "10.0.0.4:7001", "decode", nixl_connector("10.0.0.4")
            )

    def test_rejects_incomplete_metadata(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.register_instance(
                "10.0.0.5:7001", "prefill",
                {"name": "nixl", "version": "1", "metadata": {}},
            )

    def test_rejects_unsupported_connector_without_registering_instance(self) -> None:
        for name in ("mooncake_p2p_hccl", "unknown"):
            with self.subTest(connector=name):
                with self.assertRaisesRegex(ValueError, "unsupported connector"):
                    self.manager.register_instance(
                        "10.0.0.1:7001",
                        "prefill",
                        {"name": name, "version": "1", "metadata": {}},
                    )
        self.assertEqual(self.manager.instance_table, {})
        self.assertEqual(self.manager.prefill_objs, [])

    def test_lmcache_registration_requires_matching_shared_configuration(self) -> None:
        self.manager.connector = {
            "name": "lmcache",
            "version": "1",
            "metadata": {
                "backend": "redis",
                "namespace": "model-a",
                "config_fingerprint": "config-a",
            },
        }
        instance = self.manager.register_instance(
            "10.0.0.1:7001", "prefill", self.manager.connector
        )
        self.assertEqual(instance.connector, self.manager.connector)
        for key in ("backend", "namespace", "config_fingerprint"):
            with self.subTest(field=key):
                changed = {
                    **self.manager.connector,
                    "metadata": {
                        **self.manager.connector["metadata"],
                        key: "different",
                    },
                }
                with self.assertRaisesRegex(ValueError, "does not match"):
                    self.manager.register_instance(
                        "10.0.0.2:7001", "prefill", changed
                    )
        self.assertEqual(self.manager.prefill_objs, ["10.0.0.1:7001"])
        self.assertNotIn("10.0.0.2:7001", self.manager.instance_table)

    def test_distserve_reservation_covers_heartbeat_lag_and_can_be_released(self) -> None:
        self.manager._decode_objs = ["d1", "d2"]
        self.manager._instance_table = {
            "d1": Instance("d1", role="decode", online=True),
            "d2": Instance("d2", role="decode", online=True),
        }

        self.assertEqual(self.manager.reserve_distserve_batch("decode", ["r1"]), "d1")
        self.assertEqual(self.manager.get_distserve_effective_load("decode", "d1"), 1)
        self.assertEqual(self.manager.reserve_distserve_batch("decode", ["r2"]), "d2")
        self.assertTrue(self.manager.release_distserve_reservation("decode", "r1", "d1"))
        self.assertEqual(self.manager.get_distserve_effective_load("decode", "d1"), 0)

    def test_distserve_prefill_ties_round_robin_in_configuration_order(self) -> None:
        self.manager._prefill_objs = ["p1", "p2", "p3"]
        self.manager._instance_table = {
            instance: Instance(instance, role="prefill", online=True)
            for instance in self.manager._prefill_objs
        }

        selected = []
        for index in range(6):
            request_id = f"p-{index}"
            instance = self.manager.reserve_distserve_batch(
                "prefill", [request_id], request_loads=[10]
            )
            selected.append(instance)
            self.manager.release_distserve_reservation(
                "prefill", request_id, instance
            )

        self.assertEqual(selected, ["p1", "p2", "p3", "p1", "p2", "p3"])

    def test_distserve_decode_ties_use_an_independent_round_robin_cursor(self) -> None:
        self.manager._prefill_objs = ["p1", "p2"]
        self.manager._decode_objs = ["d1", "d2"]
        self.manager._instance_table = {
            **{
                instance: Instance(instance, role="prefill", online=True)
                for instance in self.manager._prefill_objs
            },
            **{
                instance: Instance(instance, role="decode", online=True)
                for instance in self.manager._decode_objs
            },
        }

        selected_prefill = self.manager.reserve_distserve_batch(
            "prefill", ["p-request"], request_loads=[10]
        )
        self.manager.release_distserve_reservation(
            "prefill", "p-request", selected_prefill
        )
        selected_decode = self.manager.reserve_distserve_batch(
            "decode", ["d-request"]
        )

        self.assertEqual(selected_prefill, "p1")
        self.assertEqual(selected_decode, "d1")

    def test_distserve_tie_break_never_overrides_unique_minimum_load(self) -> None:
        self.manager._decode_objs = ["d1", "d2", "d3"]
        self.manager._instance_table = {
            "d1": Instance("d1", role="decode", online=True, running_reqs=3),
            "d2": Instance("d2", role="decode", online=True, running_reqs=1),
            "d3": Instance("d3", role="decode", online=True, running_reqs=2),
        }
        self.manager._distserve_tie_break_cursor = {"prefill": 0, "decode": 2}

        selected = self.manager.reserve_distserve_batch("decode", ["r1"])

        self.assertEqual(selected, "d2")

    def test_distserve_tie_break_skips_offline_candidates(self) -> None:
        self.manager._prefill_objs = ["p1", "p2", "p3"]
        self.manager._instance_table = {
            instance: Instance(instance, role="prefill", online=True)
            for instance in self.manager._prefill_objs
        }
        self.manager.mark_instance_offline("p2")

        selected = []
        for index in range(4):
            request_id = f"online-{index}"
            instance = self.manager.reserve_distserve_batch(
                "prefill", [request_id], request_loads=[10]
            )
            selected.append(instance)
            self.manager.release_distserve_reservation(
                "prefill", request_id, instance
            )

        self.assertEqual(selected, ["p1", "p3", "p1", "p3"])

    def test_distserve_prefill_tie_break_is_atomic_under_concurrency(self) -> None:
        self.manager._prefill_objs = ["p1", "p2", "p3"]
        self.manager._instance_table = {
            instance: Instance(instance, role="prefill", online=True)
            for instance in self.manager._prefill_objs
        }

        def reserve(index):
            return self.manager.reserve_distserve_batch(
                "prefill", [f"concurrent-{index}"], request_loads=[1]
            )

        with ThreadPoolExecutor(max_workers=12) as executor:
            selected = list(executor.map(reserve, range(120)))

        self.assertEqual(selected.count("p1"), 40)
        self.assertEqual(selected.count("p2"), 40)
        self.assertEqual(selected.count("p3"), 40)

    def test_prefill_distserve_keeps_reservation_until_explicit_release(self) -> None:
        self.manager._prefill_objs = ["p1"]
        self.manager._instance_table = {"p1": Instance("p1", role="prefill", online=True)}
        self.manager.reserve_distserve_batch(
            "prefill", ["r1", "r2"], request_loads=[1200, 300]
        )

        self.manager._instance_table["p1"].running_reqs = 2
        self.manager.confirm_distserve_heartbeat("prefill", "p1", 2)
        self.assertEqual(
            set(self.manager._distserve_reservations["prefill"]["p1"]),
            {"r1", "r2"},
        )
        self.assertEqual(
            self.manager.get_distserve_effective_load("prefill", "p1"), 1500
        )
        self.assertEqual(self.manager.DIST_SERVE_RESERVATION_TTL, 240.0)

        self.assertTrue(self.manager.release_distserve_reservation("prefill", "r1", "p1"))
        self.assertEqual(
            self.manager.get_distserve_effective_load("prefill", "p1"), 300
        )
        self.assertTrue(self.manager.release_distserve_reservation("prefill", "r2", "p1"))
        self.manager._instance_table["p1"].running_reqs = 0
        self.assertEqual(self.manager.get_distserve_effective_load("prefill", "p1"), 0)

    def test_prefill_distserve_requires_prompt_token_loads(self) -> None:
        self.manager._prefill_objs = ["p1"]
        self.manager._instance_table = {"p1": Instance("p1", role="prefill", online=True)}

        with self.assertRaisesRegex(ValueError, "prompt-token load"):
            self.manager.reserve_distserve_batch("prefill", ["r1"])

    def test_decode_distserve_still_confirms_reservation_from_heartbeat(self) -> None:
        self.manager._decode_objs = ["d1"]
        self.manager._instance_table = {"d1": Instance("d1", role="decode", online=True)}
        self.manager.reserve_distserve_batch("decode", ["r1"])

        self.manager._instance_table["d1"].running_reqs = 1
        self.manager.confirm_distserve_heartbeat("decode", "d1", 1)

        self.assertNotIn("d1", self.manager._distserve_reservations["decode"])

    def test_distserve_offline_clears_prefill_reservations(self) -> None:
        self.manager._prefill_objs = ["p1"]
        self.manager._instance_table = {"p1": Instance("p1", role="prefill", online=True)}
        self.manager.reserve_distserve_batch(
            "prefill", ["r1", "r2"], request_loads=[100, 200]
        )

        self.assertTrue(self.manager.mark_instance_offline("p1"))
        self.assertEqual(self.manager.get_distserve_effective_load("prefill", "p1"), 0)

    def test_new_heartbeat_updates_per_gpu_and_network_resources(self) -> None:
        prefill_update = import_update_handler(
            "core.handlers.gateway.prefill.update"
        )

        self.manager.register_instance(
            "p1", "prefill", nixl_connector("10.0.0.1", 5557, 2),
            gpu_indices=[2, 3],
        )

        class JsonRequest:
            async def json(self):
                return {
                    "report_label": "p1",
                    "running_reqs": 1,
                    "waiting_reqs": 2,
                    "gpu_devices": [
                        {"gpu_index": 2, "gpu_util_percent": 10, "gpu_memory_util_percent": 30},
                        {"gpu_index": 3, "gpu_util_percent": 20, "gpu_memory_util_percent": 40},
                    ],
                    "network_metric": {"interface": "eth0", "tx_mbps": 5},
                }

        with (
            patch.object(prefill_update, "GatewayStoreManager", return_value=self.manager),
            patch.object(prefill_update, "observer") as observer,
        ):
            response = asyncio.run(prefill_update.update_instance_metric(JsonRequest()))

        instance = self.manager.instance_table["p1"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(instance.gpu_indices, [2, 3])
        self.assertEqual(len(instance.gpu_devices), 2)
        self.assertEqual(instance.network_metric["interface"], "eth0")
        observer.record_instance_resources.assert_called_once()

    def test_old_heartbeat_remains_accepted_and_preserves_registered_gpu_indices(self) -> None:
        decode_update = import_update_handler(
            "core.handlers.gateway.decode.update"
        )

        self.manager.register_instance(
            "d1", "decode", nixl_connector("10.0.0.2", 5558, 1),
            gpu_indices=[4],
        )

        class JsonRequest:
            async def json(self):
                return {
                    "report_label": "d1",
                    "running_reqs": 0,
                    "waiting_reqs": 1,
                    "gpu_util_percent": 12.5,
                    "gpu_memory_util_percent": 50.0,
                }

        with (
            patch.object(decode_update, "GatewayStoreManager", return_value=self.manager),
            patch.object(decode_update, "observer") as observer,
        ):
            response = asyncio.run(decode_update.update_instance_metric(JsonRequest()))

        instance = self.manager.instance_table["d1"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(instance.gpu_indices, [4])
        self.assertEqual(instance.gpu_devices, [])
        self.assertEqual(instance.network_metric, {})
        observer.record_instance_resources.assert_called_once()

    def test_monotonic_heartbeat_expires_at_exact_ttl_and_preserves_static_identity(self) -> None:
        self.manager.INSTANCE_HEARTBEAT_TTL_SEC = 30.0
        self.manager.register_instance(
            "p1", "prefill", nixl_connector("10.0.0.1", 5557, 2), gpu_indices=[2, 3]
        )
        with patch("store.gateway.manager.time.monotonic", return_value=100.0):
            result = self.manager.update_instance_heartbeat(
                "p1", "prefill", {"running_reqs": 1, "waiting_reqs": 2, "prompt_throughput": 40.0}
            )
        self.assertEqual(result[0], "updated")

        with patch("store.gateway.manager.time.monotonic", return_value=129.999):
            self.assertEqual(self.manager.active_prefill_objs, ["p1"])
            self.assertEqual(self.manager.expire_stale_instances(now=129.999), [])
        with patch("store.gateway.manager.time.monotonic", return_value=130.0):
            self.assertEqual(self.manager.active_prefill_objs, [])
            expired = self.manager.expire_stale_instances(now=130.0)

        self.assertEqual(expired, [("prefill", "p1", 30.0)])
        instance = self.manager.instance_table["p1"]
        self.assertFalse(instance.online)
        self.assertEqual(instance.running_reqs, 0)
        self.assertEqual(instance.prompt_throughput, 0.0)
        self.assertEqual(instance.gpu_indices, [2, 3])
        self.assertEqual(instance.role, "prefill")
        self.assertEqual(instance.connector, nixl_connector("10.0.0.1", 5557, 2))

    def test_wall_clock_timeline_updates_do_not_extend_heartbeat_ttl(self) -> None:
        self.manager.INSTANCE_HEARTBEAT_TTL_SEC = 30.0
        self.manager.register_instance("p1", "prefill", nixl_connector())
        with patch("store.gateway.manager.time.monotonic", return_value=100.0):
            self.manager.update_instance_heartbeat(
                "p1", "prefill", {"running_reqs": 0, "waiting_reqs": 0}
            )
        self.manager.update_predicted_availability("p1", 10_000.0)
        with patch("store.gateway.manager.time.monotonic", return_value=130.0):
            self.assertFalse(self.manager.is_instance_schedulable("p1", "prefill"))

    def test_valid_heartbeat_restores_expired_instance_without_losing_timeline_or_active_ledger(self) -> None:
        self.manager.INSTANCE_HEARTBEAT_TTL_SEC = 30.0
        self.manager.register_instance(
            "d1", "decode", nixl_connector("10.0.0.2", 5558, 1), gpu_indices=[4]
        )
        self.manager._decode_active_batches = {"d1": {"request-1": {"request_id": "request-1"}}}
        with patch("store.gateway.manager.time.monotonic", return_value=100.0):
            _, _, was_schedulable, had_heartbeat = self.manager.update_instance_heartbeat(
                "d1", "decode", {"running_reqs": 1, "waiting_reqs": 1}
            )
        self.assertFalse(was_schedulable)
        self.assertFalse(had_heartbeat)
        self.manager.update_predicted_availability("d1", 5000.0)
        self.assertEqual(self.manager.expire_stale_instances(now=130.0), [("decode", "d1", 30.0)])

        with patch("store.gateway.manager.time.monotonic", return_value=131.0):
            status, instance, was_schedulable, had_heartbeat = self.manager.update_instance_heartbeat(
                "d1", "decode", {"running_reqs": 0, "waiting_reqs": 0}
            )
            self.assertEqual(status, "updated")
            self.assertFalse(was_schedulable)
            self.assertTrue(had_heartbeat)
            self.assertEqual(self.manager.active_decode_objs, ["d1"])
            self.assertEqual(instance.predicted_available_time, 5000.0)
            self.assertEqual(instance.gpu_indices, [4])
            self.assertEqual(self.manager.get_decode_active_batch("d1"), [{"request_id": "request-1"}])

    def test_concurrent_metric_and_scheduler_updates_preserve_both_state_groups(self) -> None:
        self.manager.register_instance("p1", "prefill", nixl_connector())

        def report_metrics():
            for index in range(100):
                self.manager.update_instance_heartbeat(
                    "p1",
                    "prefill",
                    {"running_reqs": index, "waiting_reqs": 0, "prompt_throughput": float(index)},
                )

        def update_timeline():
            for index in range(100):
                self.manager.update_predicted_availability("p1", float(index))

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda function: function(), (report_metrics, update_timeline)))

        instance = self.manager.instance_table["p1"]
        self.assertEqual(instance.running_reqs, 99)
        self.assertEqual(instance.predicted_available_time, 99.0)
        self.assertIsNotNone(instance.last_heartbeat_monotonic)
        self.assertTrue(self.manager.is_instance_schedulable("p1", "prefill"))

    def test_gateway_registration_loop_re_registers_then_restores_compute_and_network_state(self) -> None:
        from coord.gateway import registration

        stop_event = threading.Event()
        registered_event = threading.Event()
        calls = {"register": 0, "update": 0, "network": 0}

        def register():
            calls["register"] += 1
            self.manager.set_pool_id(7)
            return "success"

        def update():
            calls["update"] += 1
            if calls["update"] == 1:
                self.manager.set_pool_id_if_current(7, None)
                return "registration_lost"
            return "success"

        def report_network():
            calls["network"] += 1
            stop_event.set()

        with (
            patch.object(registration, "prefill_compute_register", side_effect=register),
            patch.object(registration, "prefill_compute_update", side_effect=update),
            patch.object(registration, "network_update", side_effect=report_network),
        ):
            registration.run_gateway_management_loop(
                "prefill", stop_event, registered_event, 10.0, 10.0, self.manager
            )

        self.assertTrue(registered_event.is_set())
        self.assertEqual(calls, {"register": 2, "update": 2, "network": 1})
        self.assertEqual(self.manager.get_pool_id(), 7)

    def test_gateway_management_stops_before_updating_if_registration_finishes_after_shutdown(self) -> None:
        from coord.gateway import registration

        stop_event = threading.Event()
        registered_event = threading.Event()

        def register():
            self.manager.set_pool_id(8)
            stop_event.set()
            return "success"

        with (
            patch.object(registration, "prefill_compute_register", side_effect=register),
            patch.object(registration, "prefill_compute_update") as update,
            patch.object(registration, "network_update") as report_network,
        ):
            registration.run_gateway_management_loop(
                "prefill", stop_event, registered_event, 10.0, 10.0, self.manager
            )

        self.assertTrue(registered_event.is_set())
        update.assert_not_called()
        report_network.assert_not_called()

    def test_instance_metric_loop_stops_before_reporting_if_registration_finishes_after_shutdown(self) -> None:
        from core.lifespans import instance as instance_lifespan

        stop_event = threading.Event()

        def register():
            stop_event.set()
            return "success"

        with (
            patch.object(instance_lifespan, "instance_register", side_effect=register),
            patch.object(instance_lifespan, "metric_update") as update,
        ):
            instance_lifespan.metric_update_loop_task(
                None, stop_event, False, 0.02, 10.0
            )

        update.assert_not_called()

    def test_controller_200_with_missing_or_invalid_pool_id_is_not_registration_success(self) -> None:
        from coord.gateway import up_store

        self.manager.controller = "127.0.0.1:5000"
        self.manager.host = "10.0.0.1"
        self.manager.port = 8081
        for body in (
            {},
            {"pool_id": None},
            {"pool_id": 0},
            {"pool_id": True},
            {"pool_id": "not-an-id"},
        ):
            with self.subTest(body=body):
                response = type("Response", (), {"status_code": 200, "json": lambda self: body})()
                with (
                    patch.object(up_store, "GatewayStoreManager", return_value=self.manager),
                    patch.object(up_store.requests, "post", return_value=response) as post,
                ):
                    result = up_store.prefill_compute_register()

                self.assertEqual(result, "configuration_error")
                self.assertIsNone(self.manager.get_pool_id())
                self.assertEqual(post.call_args.kwargs["timeout"], (2.0, 3.0))


class PublicAddressConfigTest(unittest.TestCase):
    def test_environment_supplies_deployment_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "FLEXPD_CONTROLLER_HOST": "10.1.0.1",
                "FLEXPD_CONTROLLER_PORT": "5500",
                "FLEXPD_PREFILL_INSTANCES": "10.1.0.2:7001,10.1.0.3:7001",
            },
        ):
            module = importlib.reload(address_config)
            self.assertEqual(module.CONFIG_CONTROLLER_IP, "10.1.0.1")
            self.assertEqual(module.CONFIG_CONTROLLER_PORT, 5500)
            self.assertEqual(
                module.CONFIG_INSTANCE_P_ADDRS,
                ["10.1.0.2:7001", "10.1.0.3:7001"],
            )
        importlib.reload(address_config)


if __name__ == "__main__":
    unittest.main()
