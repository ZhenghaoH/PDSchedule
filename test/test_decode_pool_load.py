import sys
import threading
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from store.gateway.instance import Instance
from store.gateway.manager import GatewayStoreManager


class DecodePoolLoadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = object.__new__(GatewayStoreManager)
        self.manager._data_lock = threading.RLock()
        self.manager._decode_objs = ["decode-1", "decode-2", "decode-offline"]
        self.manager._instance_table = {
            "decode-1": Instance(
                instance_ip="decode-1",
                running_reqs=2,
                waiting_reqs=3,
                online=True,
            ),
            "decode-2": Instance(
                instance_ip="decode-2",
                running_reqs=4,
                waiting_reqs=5,
                online=True,
            ),
            "decode-offline": Instance(
                instance_ip="decode-offline",
                running_reqs=100,
                waiting_reqs=100,
                online=False,
            ),
        }

    def test_decode_load_counts_running_and_waiting_for_online_instances(self) -> None:
        self.assertEqual(self.manager.get_decode_load(), 14)

    def test_decode_load_does_not_become_negative(self) -> None:
        self.manager._instance_table["decode-1"].running_reqs = -2
        self.manager._instance_table["decode-1"].waiting_reqs = -3

        self.assertEqual(self.manager.get_decode_load(), 9)


if __name__ == "__main__":
    unittest.main()
