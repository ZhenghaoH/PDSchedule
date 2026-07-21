import threading
import unittest
import sys
import importlib
import os
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from store.gateway.manager import GatewayStoreManager
from store.gateway.instance import Instance
import config.ADDRESS_CONFIG as address_config


class RuntimeInstanceRegistrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = object.__new__(GatewayStoreManager)
        self.manager._data_lock = threading.RLock()
        self.manager._prefill_objs = []
        self.manager._decode_objs = []
        self.manager._instance_table = {}
        self.manager._link_table = {}

    def test_registers_nixl_metadata_without_static_topology(self) -> None:
        instance = self.manager.register_instance(
            instance_ip="10.0.0.2:7001",
            role="prefill",
            connector="NixlConnector",
            side_channel_host="10.0.0.2",
            side_channel_port=5557,
            tp_size=2,
            vllm_port=8001,
        )

        self.assertEqual(instance.role, "prefill")
        self.assertEqual(self.manager.prefill_objs, ["10.0.0.2:7001"])
        self.assertEqual(
            self.manager.get_nixl_metadata("10.0.0.2:7001"),
            {
                "connector": "NixlConnector",
                "role": "prefill",
                "side_channel_host": "10.0.0.2",
                "side_channel_port": 5557,
                "tp_size": 2,
            },
        )
        self.assertEqual(
            self.manager.get_side_channel_addr("10.0.0.2:7001"),
            "10.0.0.2:5557",
        )

    def test_duplicate_registration_is_idempotent(self) -> None:
        kwargs = {
            "instance_ip": "10.0.0.3:7002",
            "role": "decode",
            "connector": "NixlConnector",
            "side_channel_host": "10.0.0.3",
            "side_channel_port": 5558,
            "tp_size": 1,
        }
        self.manager.register_instance(**kwargs)
        self.manager.register_instance(**kwargs)

        self.assertEqual(self.manager.decode_objs, ["10.0.0.3:7002"])

    def test_rejects_role_conflict(self) -> None:
        self.manager.register_instance(
            "10.0.0.4:7001", "prefill", "NixlConnector", "10.0.0.4", 5557, 1
        )

        with self.assertRaisesRegex(ValueError, "already registered as prefill"):
            self.manager.register_instance(
                "10.0.0.4:7001", "decode", "NixlConnector", "10.0.0.4", 5557, 1
            )

    def test_rejects_incomplete_metadata(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.register_instance(
                "10.0.0.5:7001", "prefill", "NixlConnector", "", 0, 0
            )

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
