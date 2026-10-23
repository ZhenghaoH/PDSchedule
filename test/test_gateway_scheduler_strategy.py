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

from fastapi import HTTPException

from core.consoles.gateway import (
    build_gateway_completer,
    configure_gateway_readline,
    gateway_completion_words,
    run_gateway_cli,
    run_gateway_server,
    switch_gateway_scheduler,
)
from core.handlers.gateway.decode.proxy import (
    _bind_and_validate_connector,
    _select_decode_instance,
)
from core.schemas.request import BaseRequest, Prompt, Service, Task
from proxy.forward import forward_request_trace
from proxy.sync import RequestBatcher, storage as shared_storage
from scheduler.gateway.decode.manager import SchedulerManager as DecodeSchedulerManager
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
    manager._distserve_tie_break_cursor = {"prefill": 0, "decode": 0}
    manager._decode_active_batches = {}
    manager.TPOT_STEP_PREDICT_FUNC = None
    manager.TPOT_STEP_PREDICTOR_READY = False
    manager.SCHEDULER_STRATEGY = "slo_aware_prefill"
    manager.DECODE_SCHEDULER_STRATEGY = "slo_aware_decode"
    return manager


def make_request(request_id, token_length=10):
    return BaseRequest(
        Request_ID=request_id,
        Request_type="request",
        Prompt=Prompt("model", "hello", token_length, 1, 16, True, 1.0, 1.0),
        Service=Service(arrival_time=float(request_id)),
        Task=Task(
            User_addr="",
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
        self.assertEqual(
            self.store.set_prefill_scheduler_strategy("jsq"),
            "slo_aware_prefill",
        )
        self.assertEqual(
            self.store.set_decode_scheduler_strategy("round_robin"),
            "slo_aware_decode",
        )
        self.assertEqual(self.store.set_prefill_scheduler_strategy("round_robin"), "jsq")
        for retired_strategy in ("distserve", "least_load", "pso", "random"):
            with self.subTest(strategy=retired_strategy), self.assertRaises(ValueError):
                self.store.set_prefill_scheduler_strategy(retired_strategy)
        for retired_strategy in (
            "distserve",
            "lowest_generate_throughput",
            "slo_tpot_admission",
            "least_load",
            "random",
        ):
            with self.subTest(strategy=retired_strategy), self.assertRaises(ValueError):
                self.store.set_decode_scheduler_strategy(retired_strategy)


class PrefillSchedulerManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = make_store(["p2", "p1"])
        self.store._instance_table = {
            "p1": Instance("p1", prompt_throughput=3.0, online=True),
            "p2": Instance("p2", prompt_throughput=3.0, online=True),
        }
        self.scheduler = SchedulerManager(storage=self.store)

    async def test_registry_contains_only_public_prefill_strategies(self):
        self.assertEqual(
            self.scheduler.strategies,
            ("slo_aware_prefill", "jsq", "round_robin"),
        )

    async def test_retired_strategy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported Prefill"):
            await self.scheduler.schedule_batch([make_request(1)], strategy="distserve")

    async def test_round_robin_is_immediate(self):
        requests = [make_request(1), make_request(2), make_request(3)]
        scheduled, policy = await self.scheduler.schedule_batch(requests, strategy="round_robin")
        self.assertEqual(policy, "IMMEDIATE")
        self.assertEqual(
            [request.Task.prefill_instance for request in scheduled],
            ["p2", "p1", "p2"],
        )

    async def test_slo_aware_prefill_keeps_existing_queue_policy(self):
        request = make_request(1)
        strategy = self.scheduler.get_strategy("slo_aware_prefill")
        strategy._schedule_requests = AsyncMock(return_value=[request])
        scheduled, policy = await self.scheduler.schedule_batch(
            [request],
            strategy="slo_aware_prefill",
        )
        strategy._schedule_requests.assert_awaited_once()
        self.assertEqual(scheduled, [request])
        self.assertEqual(policy, "FULL_OR_IDLE_WAIT")

    async def test_jsq_dispatches_every_request_independently(self):
        requests = [
            make_request(1, 1000),
            make_request(2, 1000),
            make_request(3, 100),
        ]
        scheduled, policy = await self.scheduler.schedule_batch(
            requests, strategy="jsq"
        )

        self.assertEqual(policy, "IMMEDIATE")
        self.assertEqual([request.Request_ID for request in scheduled], [1, 2, 3])
        self.assertEqual(
            [request.Task.prefill_instance for request in scheduled],
            ["p2", "p1", "p2"],
        )
        self.assertTrue(all(request.Task.batch_order == 1 for request in scheduled))
        self.assertEqual(
            scheduled[0].Task.scheduler_metadata["gateway_prefill_selection"]["reason"],
            "minimum_unfinished_prompt_tokens",
        )
        self.assertTrue(
            all(
                request.Task.scheduler_metadata["jsq"]["dispatch_mode"] == "direct_jsq"
                for request in scheduled
            )
        )
        self.assertTrue(
            all(
                request.Task.scheduler_metadata["jsq"]["load_metric"]
                == "unfinished_prompt_tokens"
                for request in scheduled
            )
        )
        self.assertEqual(
            [
                request.Task.scheduler_metadata["jsq"]["prompt_tokens"]
                for request in scheduled
            ],
            [1000, 1000, 100],
        )
        self.assertNotIn("batch_tokens", scheduled[0].Task.scheduler_metadata["jsq"])

    async def test_jsq_does_not_reorder_or_group_pending_requests(self):
        later = make_request(2, 1000)
        earlier = make_request(1, 1000)
        scheduled, _ = await self.scheduler.schedule_batch(
            [later, earlier], strategy="jsq"
        )
        self.assertEqual([request.Request_ID for request in scheduled], [2, 1])
        self.assertEqual(
            [request.Task.prefill_instance for request in scheduled], ["p2", "p1"]
        )

    async def test_jsq_uses_unfinished_prompt_tokens(self):
        long_request = make_request(1, 2048)
        scheduled, _ = await self.scheduler.schedule_batch(
            [long_request, make_request(2, 10)], strategy="jsq"
        )
        self.assertEqual([request.Request_ID for request in scheduled], [1, 2])
        self.assertEqual(
            [request.Task.prefill_instance for request in scheduled], ["p2", "p1"]
        )

        next_scheduled, _ = await self.scheduler.schedule_batch(
            [make_request(3, 10)], strategy="jsq"
        )
        self.assertEqual(next_scheduled[0].Task.prefill_instance, "p1")
        self.assertEqual(self.store.get_distserve_effective_load("prefill", "p1"), 20)
        self.assertEqual(self.store.get_distserve_effective_load("prefill", "p2"), 2048)

    async def test_jsq_lifecycle_hooks_release_reservations(self):
        request = make_request(1, 100)
        scheduled, _ = await self.scheduler.schedule_batch(
            [request],
            strategy="jsq",
        )
        instance = scheduled[0].Task.prefill_instance
        self.assertEqual(
            self.store.get_distserve_effective_load("prefill", instance),
            100,
        )

        self.scheduler.on_schedule_discarded(scheduled)
        self.assertEqual(
            self.store.get_distserve_effective_load("prefill", instance),
            0,
        )

        scheduled, _ = await self.scheduler.schedule_batch(
            [request],
            strategy="jsq",
        )
        self.scheduler.on_request_finished(scheduled[0])
        self.assertEqual(
            self.store.get_distserve_effective_load(
                "prefill", scheduled[0].Task.prefill_instance
            ),
            0,
        )

    async def test_jsq_capabilities_drive_runtime_behavior(self):
        jsq = self.scheduler.get_capabilities("jsq")
        self.assertTrue(jsq.trigger_on_arrival)
        self.assertFalse(jsq.allow_preemption)
        self.assertFalse(jsq.allow_adaptive_switch)

        for strategy in ("slo_aware_prefill", "round_robin"):
            capabilities = self.scheduler.get_capabilities(strategy)
            self.assertFalse(capabilities.trigger_on_arrival)
            self.assertTrue(capabilities.allow_preemption)
            self.assertTrue(capabilities.allow_adaptive_switch)


class DecodeSchedulerManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store = make_store(decodes=["d2", "d1"])
        store._instance_table = {
            "d1": Instance("d1", generate_throughput=5.0, online=True),
            "d2": Instance("d2", generate_throughput=10.0, online=True),
        }
        self.store = store
        self.scheduler = DecodeSchedulerManager(storage=store)

    async def test_registry_contains_only_public_decode_strategies(self):
        self.assertEqual(
            self.scheduler.strategies,
            (
                "round_robin",
                "lowest_generation_throughput",
                "slo_aware_decode",
            ),
        )

    async def test_round_robin_uses_manager_entrypoint(self):
        first = await self.scheduler.schedule(
            make_request(1),
            strategy="round_robin",
        )
        second = await self.scheduler.schedule(
            make_request(2),
            strategy="round_robin",
        )
        self.assertEqual((first, second), ("d2", "d1"))

    async def test_lowest_generation_throughput_selects_minimum(self):
        selected = await self.scheduler.schedule(
            make_request(1),
            strategy="lowest_generation_throughput",
        )
        self.assertEqual(selected, "d1")
        self.assertEqual(
            self.scheduler.last_decision["reason"],
            "minimum_generation_throughput",
        )
        self.assertTrue(self.scheduler.last_decision["candidates"])

    async def test_slo_aware_decode_owns_predictor_fallback(self):
        selected = await self.scheduler.schedule(
            make_request(1),
            strategy="slo_aware_decode",
        )
        self.assertEqual(selected, "d1")
        self.assertEqual(
            self.scheduler.last_decision["reason"],
            "tpot_prediction_unavailable_"
            "fallback_minimum_generation_throughput",
        )

    async def test_retired_decode_strategy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported Decode"):
            await self.scheduler.schedule(make_request(1), strategy="distserve")

    async def test_decode_proxy_selects_with_configured_strategy_without_preselection(self):
        self.store.set_decode_scheduler_strategy("lowest_generation_throughput")
        request = make_request(1)

        with (
            patch(
                "core.handlers.gateway.decode.proxy.decode_observer.record_schedule"
            ) as record_schedule,
            patch("core.handlers.gateway.decode.proxy._log_decode_instance_status"),
        ):
            selected = await _select_decode_instance(self.store, request)

        self.assertEqual(selected, "d1")
        self.assertEqual(request.Task.decode_instance, "d1")
        decision = request.Task.scheduler_metadata["gateway_decode_selection"]
        self.assertEqual(decision["strategy"], "lowest_generation_throughput")
        self.assertEqual(decision["selected_instance"], "d1")
        record_schedule.assert_called_once()

    async def test_decode_proxy_keeps_active_preselected_instance(self):
        request = make_request(1)
        request.Task.decode_instance = "d2"

        with (
            patch(
                "core.handlers.gateway.decode.proxy.SchedulerManager"
            ) as scheduler_manager,
            patch(
                "core.handlers.gateway.decode.proxy.decode_observer.record_schedule"
            ) as record_schedule,
            patch("core.handlers.gateway.decode.proxy._log_decode_instance_status"),
        ):
            selected = await _select_decode_instance(self.store, request)

        self.assertEqual(selected, "d2")
        scheduler_manager.assert_not_called()
        decision = request.Task.scheduler_metadata["gateway_decode_selection"]
        self.assertEqual(decision["selection_source"], "preselected_request")
        self.assertEqual(decision["selected_instance"], "d2")
        record_schedule.assert_called_once()

    def test_decode_connector_validation_logs_success(self):
        descriptor = {
            "name": "nixl",
            "version": "1",
            "metadata": {},
        }
        self.store._instance_table["d1"].role = "decode"
        self.store._instance_table["d1"].connector = descriptor
        request = make_request(42)
        request.Task.prefill_instance = "p1"
        request.Task.pd_connector = "nixl"
        request.Task.connector_session_id = "nixl-42"
        request.Task.connector_context = {
            "prefill_endpoint": {
                "role": "prefill",
                "descriptor": descriptor,
            }
        }

        with self.assertLogs(
            "core.handlers.gateway.decode.proxy", level="INFO"
        ) as captured:
            _bind_and_validate_connector(self.store, request, "d1")

        self.assertEqual(
            request.Task.connector_context["decode_endpoint"],
            self.store.get_connector_metadata("d1"),
        )
        self.assertIn(
            "[Decode-Connector] validation succeeded "
            "request_id=42 connector=nixl prefill_instance=p1 "
            "decode_instance=d1 session_id=nixl-42",
            captured.output[0],
        )

    def test_decode_connector_validation_failure_does_not_log_success(self):
        decode_descriptor = {
            "name": "nixl",
            "version": "1",
            "metadata": {},
        }
        self.store._instance_table["d1"].role = "decode"
        self.store._instance_table["d1"].connector = decode_descriptor
        request = make_request(43)
        request.Task.prefill_instance = "p1"
        request.Task.pd_connector = "nixl"
        request.Task.connector_session_id = "nixl-43"
        request.Task.connector_context = {
            "prefill_endpoint": {
                "role": "prefill",
                "descriptor": {
                    "name": "lmcache",
                    "version": "1",
                    "metadata": {
                        "backend": "redis",
                        "namespace": "model-a",
                        "config_fingerprint": "abc123",
                    },
                },
            }
        }

        with patch(
            "core.handlers.gateway.decode.proxy.logger.info"
        ) as success_log:
            with self.assertRaisesRegex(
                HTTPException,
                "Selected instances use incompatible connectors",
            ):
                _bind_and_validate_connector(self.store, request, "d1")

        success_log.assert_not_called()


class ForwardRequestErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_trace_preserves_upstream_http_status(self):
        class ErrorResponse:
            status = 503

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def text(self):
                return '{"detail":"decode unavailable"}'

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            def post(self, **kwargs):
                return ErrorResponse()

        with patch("proxy.forward.aiohttp.ClientSession", return_value=FakeSession()):
            with self.assertRaises(HTTPException) as raised:
                async for _ in forward_request_trace(
                    "http://decode.test/v1/chat/completions",
                    {"stream": True},
                ):
                    pass

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail,
            "Request failed: {'detail': 'decode unavailable'}",
        )


class RequestBatcherSwitchTest(unittest.IsolatedAsyncioTestCase):
    async def test_jsq_arrival_triggers_immediately_in_batch_size_mode(self):
        batcher = RequestBatcher(default_strategy="jsq")
        batcher.trigger_mode = "BATCH_SIZE"
        batcher.syn_batch_size = 30
        batcher.pending_queue.append(make_request(1))

        batcher._unified_trigger(is_new_arrival=True)

        self.assertTrue(batcher.schedule_event.is_set())

    async def test_switch_resets_only_pending_requests(self):
        batcher = RequestBatcher(default_strategy="slo_aware_prefill")
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
            result = await batcher.switch_strategy("round_robin")
            self.assertEqual(result["previous"], "slo_aware_prefill")
            self.assertEqual(result["pending_count"], 1)
            self.assertEqual(pending.Task.batch_order, -1)
            self.assertEqual(pending.Task.prefill_instance, "")
            self.assertEqual(running.Task.prefill_instance, "p2")
            self.assertEqual(batcher.strategy_version, 1)
            self.assertTrue(batcher.schedule_event.is_set())
        finally:
            shared_storage.set_prefill_scheduler_strategy(previous)
            await batcher.running_tasks_map["2"][0]

    async def test_jsq_disables_prefill_preemption(self):
        batcher = RequestBatcher(default_strategy="jsq")
        task = asyncio.create_task(asyncio.sleep(0))
        batcher.running_tasks_map["1"] = (task, make_request(1, 4096), 0.0)
        await batcher.preempt_heavy_requests()
        self.assertIn("1", batcher.running_tasks_map)
        await task


class GatewayConsoleStrategyTest(unittest.TestCase):
    def test_completion_words_are_role_specific_and_stable(self):
        prefill_words = gateway_completion_words("prefill")
        decode_words = gateway_completion_words("decode")
        self.assertIn("show", prefill_words)
        self.assertIn("slo_aware_prefill", prefill_words)
        self.assertIn("jsq", prefill_words)
        self.assertIn("round_robin", prefill_words)
        self.assertNotIn("least_load", prefill_words)
        self.assertNotIn("distserve", prefill_words)
        self.assertNotIn("slo_aware_decode", prefill_words)
        self.assertIn("slo_aware_decode", decode_words)
        self.assertIn("lowest_generation_throughput", decode_words)
        self.assertNotIn("distserve", decode_words)
        self.assertNotIn("slo_tpot_admission", decode_words)
        self.assertNotIn("slo_aware_prefill", decode_words)
        self.assertEqual(len(prefill_words), len(set(prefill_words)))
        self.assertEqual(len(decode_words), len(set(decode_words)))

    def test_completer_matches_current_word_prefix(self):
        completer = build_gateway_completer("prefill")
        self.assertEqual(completer("j", 0), "jsq")
        self.assertIsNone(completer("j", 1))
        self.assertEqual(completer("s", 0), "show")
        self.assertEqual(completer("s", 1), "switch")

    def test_readline_completion_is_registered_and_can_degrade(self):
        fake_readline = Mock()
        self.assertTrue(configure_gateway_readline("decode", fake_readline))
        fake_readline.set_completer.assert_called_once()
        fake_readline.parse_and_bind.assert_called_once_with("tab: complete")

        broken_readline = Mock()
        broken_readline.set_completer.side_effect = RuntimeError("unsupported")
        self.assertFalse(configure_gateway_readline("decode", broken_readline))
        with patch("core.consoles.gateway.readline", None):
            self.assertFalse(configure_gateway_readline("decode"))

    def test_decode_switch_uses_runtime_store(self):
        store = make_store()
        result = switch_gateway_scheduler("decode", "round_robin", store)
        self.assertEqual(result["previous"], "slo_aware_decode")
        self.assertEqual(store.get_decode_scheduler_strategy(), "round_robin")

    def test_prefill_switch_is_submitted_to_batcher(self):
        store = make_store()
        batcher = Mock()
        batcher.switch_strategy_threadsafe.return_value = {
            "previous": "slo_aware_prefill",
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

    def test_entrypoint_arguments_expose_three_strategies_per_gateway(self):
        prefill_source = (ROOT_DIR / "entrypoints" / "gatewayP.py").read_text(
            encoding="utf-8"
        )
        decode_source = (ROOT_DIR / "entrypoints" / "gatewayD.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('"--distserve-prefill-batch-tokens"', prefill_source)
        self.assertIn('"jsq"', prefill_source)
        self.assertNotIn('"distserve"', prefill_source)
        self.assertIn('"slo_aware_decode"', decode_source)
        self.assertIn('"lowest_generation_throughput"', decode_source)
        self.assertNotIn('"distserve"', decode_source)


if __name__ == "__main__":
    unittest.main()
