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
        self.assertEqual(instance.gpu_indices, [2, 3])
        self.assertEqual(self.manager.prefill_objs, ["10.0.0.2:7001"])
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
            "d1": Instance("d1", online=True),
            "d2": Instance("d2", online=True),
        }

        self.assertEqual(self.manager.reserve_distserve_batch("decode", ["r1"]), "d1")
        self.assertEqual(self.manager.get_distserve_effective_load("decode", "d1"), 1)
        self.assertEqual(self.manager.reserve_distserve_batch("decode", ["r2"]), "d2")
        self.assertTrue(self.manager.release_distserve_reservation("decode", "r1", "d1"))
        self.assertEqual(self.manager.get_distserve_effective_load("decode", "d1"), 0)

    def test_distserve_prefill_ties_round_robin_in_configuration_order(self) -> None:
        self.manager._prefill_objs = ["p1", "p2", "p3"]
        self.manager._instance_table = {
            instance: Instance(instance, online=True)
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
            instance: Instance(instance, online=True)
            for instance in [*self.manager._prefill_objs, *self.manager._decode_objs]
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
            "d1": Instance("d1", online=True, running_reqs=3),
            "d2": Instance("d2", online=True, running_reqs=1),
            "d3": Instance("d3", online=True, running_reqs=2),
        }
        self.manager._distserve_tie_break_cursor = {"prefill": 0, "decode": 2}

        selected = self.manager.reserve_distserve_batch("decode", ["r1"])

        self.assertEqual(selected, "d2")

    def test_distserve_tie_break_skips_offline_candidates(self) -> None:
        self.manager._prefill_objs = ["p1", "p2", "p3"]
        self.manager._instance_table = {
            instance: Instance(instance, online=True)
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
            instance: Instance(instance, online=True)
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
        self.manager._instance_table = {"p1": Instance("p1", online=True)}
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
        self.manager._instance_table = {"p1": Instance("p1", online=True)}

        with self.assertRaisesRegex(ValueError, "prompt-token load"):
            self.manager.reserve_distserve_batch("prefill", ["r1"])

    def test_decode_distserve_still_confirms_reservation_from_heartbeat(self) -> None:
        self.manager._decode_objs = ["d1"]
        self.manager._instance_table = {"d1": Instance("d1", online=True)}
        self.manager.reserve_distserve_batch("decode", ["r1"])

        self.manager._instance_table["d1"].running_reqs = 1
        self.manager.confirm_distserve_heartbeat("decode", "d1", 1)

        self.assertNotIn("d1", self.manager._distserve_reservations["decode"])

    def test_distserve_offline_clears_prefill_reservations(self) -> None:
        self.manager._prefill_objs = ["p1"]
        self.manager._instance_table = {"p1": Instance("p1", online=True)}
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
