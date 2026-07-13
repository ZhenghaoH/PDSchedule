import asyncio
import sys
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if "fastapi" not in sys.modules:
    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.HTTPException = type("HTTPException", (Exception,), {})
    sys.modules["fastapi"] = fastapi_stub

from core.consoles.gateway import run_gateway_cli, run_gateway_server, switch_gateway_scheduler
from core.schemas.request import BaseRequest, Prompt, Service, Task
from proxy.sync import RequestBatcher, storage as shared_storage
from scheduler.gateway.prefill.manager import SchedulerManager
from store.gateway.instance import Instance
from store.gateway.manager import GatewayStoreManager


def make_store(prefills=None, decodes=None):
    manager = object.__new__(GatewayStoreManager)
    manager._data_lock = threading.RLock()
    manager._prefill_objs = list(prefills or [])
    manager._decode_objs = list(decodes or [])
    manager._instance_table = {}
    manager._prefill_round_robin_index = 0
    manager._decode_round_robin_index = 0
    manager.SCHEDULER_STRATEGY = "pso_new"
    manager.DECODE_SCHEDULER_STRATEGY = "slo_tpot_admission"
    return manager


def make_request(request_id):
    return BaseRequest(
        Request_ID=request_id,
        Request_type="request",
        Prompt=Prompt("model", "hello", 10, 1, 16, True, 1.0, 1.0),
        Service=Service(arrival_time=float(request_id)),
        Task=Task(
            User_addr="",
            KDN_server_addr="",
            default_know_addr="",
            P_proxy_addr="",
            P_proxy_port=0,
            D_proxy_addr="",
            D_proxy_port=0,
            prefill_instance="",
            decode_instance="",
            batch_order=-1,
            User_url_path="",
        ),
    )


class GatewayStoreStrategyTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store(["p1", "p2"], ["d1"])
        self.store._instance_table = {
            "p1": Instance("p1", prompt_throughput=8.0, online=True),
            "p2": Instance("p2", prompt_throughput=3.0, online=True),
            "d1": Instance("d1", online=True),
        }

    def test_prefill_round_robin_is_real_and_thread_safe(self):
        self.assertEqual(
            [self.store.next_prefill_round_robin() for _ in range(4)],
            ["p1", "p2", "p1", "p2"],
        )
        self.store._prefill_round_robin_index = 0
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.store.next_prefill_round_robin(), range(100)))
        self.assertEqual(results.count("p1"), 50)
        self.assertEqual(results.count("p2"), 50)

    def test_strategy_setters_validate_modes(self):
        self.assertEqual(self.store.set_prefill_scheduler_strategy("least_load"), "pso_new")
        self.assertEqual(self.store.set_decode_scheduler_strategy("round_robin"), "slo_tpot_admission")
        with self.assertRaises(ValueError):
            self.store.set_prefill_scheduler_strategy("random")
        with self.assertRaises(ValueError):
            self.store.set_decode_scheduler_strategy("least_load")


class PrefillSchedulerManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = make_store(["p2", "p1"])
        self.store._instance_table = {
            "p1": Instance("p1", prompt_throughput=3.0, online=True),
            "p2": Instance("p2", prompt_throughput=3.0, online=True),
        }
        self.scheduler = SchedulerManager()
        self.scheduler.basic_scheduler.storage = self.store

    async def test_least_load_is_immediate_and_has_deterministic_tie_break(self):
        self.store._instance_table["p2"].prompt_throughput = 2.0
        requests = [make_request(1), make_request(2)]
        scheduled, policy = await self.scheduler.schedule_batch(requests, strategy="least_load")
        self.assertEqual(policy, "IMMEDIATE")
        self.assertEqual([request.Task.prefill_instance for request in scheduled], ["p2", "p2"])
        self.assertTrue(all(request.Task.batch_order == 1 for request in scheduled))

        self.store._instance_table["p2"].prompt_throughput = 3.0
        tied, _ = await self.scheduler.schedule_batch([make_request(3)], strategy="least_load")
        self.assertEqual(tied[0].Task.prefill_instance, "p1")

    async def test_round_robin_is_immediate(self):
        requests = [make_request(1), make_request(2), make_request(3)]
        scheduled, policy = await self.scheduler.schedule_batch(requests, strategy="round_robin")
        self.assertEqual(policy, "IMMEDIATE")
        self.assertEqual(
            [request.Task.prefill_instance for request in scheduled],
            ["p2", "p1", "p2"],
        )

    async def test_pso_new_keeps_existing_queue_policy(self):
        request = make_request(1)
        self.scheduler.pso_new_scheduler.schedule = AsyncMock(return_value=[request])
        scheduled, policy = await self.scheduler.schedule_batch([request], strategy="pso_new")
        self.scheduler.pso_new_scheduler.schedule.assert_awaited_once()
        self.assertEqual(scheduled, [request])
        self.assertEqual(policy, "FULL_OR_IDLE_WAIT")


class RequestBatcherSwitchTest(unittest.IsolatedAsyncioTestCase):
    async def test_switch_resets_only_pending_requests(self):
        batcher = RequestBatcher(default_strategy="pso_new")
        pending = make_request(1)
        pending.Task.batch_order = 2
        pending.Task.prefill_instance = "p1"
        running = make_request(2)
        running.Task.batch_order = 1
        running.Task.prefill_instance = "p2"
        batcher.pending_queue.append(pending)
        batcher.running_tasks_map["2"] = (asyncio.create_task(asyncio.sleep(0)), running, 0.0)

        previous = shared_storage.get_prefill_scheduler_strategy()
        try:
            result = await batcher.switch_strategy("least_load")
            self.assertEqual(result["previous"], "pso_new")
            self.assertEqual(result["pending_count"], 1)
            self.assertEqual(pending.Task.batch_order, -1)
            self.assertEqual(pending.Task.prefill_instance, "")
            self.assertEqual(running.Task.prefill_instance, "p2")
            self.assertEqual(batcher.strategy_version, 1)
            self.assertTrue(batcher.schedule_event.is_set())
        finally:
            shared_storage.set_prefill_scheduler_strategy(previous)
            await batcher.running_tasks_map["2"][0]


class GatewayConsoleStrategyTest(unittest.TestCase):
    def test_decode_switch_uses_runtime_store(self):
        store = make_store()
        result = switch_gateway_scheduler("decode", "round_robin", store)
        self.assertEqual(result["previous"], "slo_tpot_admission")
        self.assertEqual(store.get_decode_scheduler_strategy(), "round_robin")

    def test_prefill_switch_is_submitted_to_batcher(self):
        store = make_store()
        batcher = Mock()
        batcher.switch_strategy_threadsafe.return_value = {
            "previous": "pso_new",
            "current": "round_robin",
            "pending_count": 2,
            "adaptive_switch": False,
        }
        result = switch_gateway_scheduler("prefill", "round_robin", store, batcher)
        batcher.switch_strategy_threadsafe.assert_called_once_with("round_robin")
        self.assertEqual(result["pending_count"], 2)

    def test_quit_stops_interactive_server(self):
        store = make_store()
        server = Mock(should_exit=False)
        with patch("builtins.input", return_value="quit"):
            run_gateway_cli("decode", store, server)
        self.assertTrue(server.should_exit)

    def test_no_cli_runs_http_server_in_foreground(self):
        fake_server = Mock()
        fake_uvicorn = types.ModuleType("uvicorn")
        fake_uvicorn.Config = Mock(return_value="config")
        fake_uvicorn.Server = Mock(return_value=fake_server)
        with patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
            run_gateway_server(Mock(), "127.0.0.1", 8081, "decode", make_store(), True)
        fake_server.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
