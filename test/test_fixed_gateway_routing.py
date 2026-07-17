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
from scheduler.controller.global_scheduler import GlobalScheduler, parse_gateway_endpoint
from utils.nixl_request import build_nixl_decode_payload, build_nixl_prefill_payload


def make_request(metadata=None):
    return BaseRequest(
        Request_ID=1,
        Request_type="request",
        Prompt=Prompt("model", "hello", 5, 1, 16, True, 1.0, 1.0),
        Service=Service(Endpoint_type="chat/completions"),
        Task=Task(
            User_addr="",
            KDN_server_addr="",
            default_know_addr="",
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


class GatewayEndpointParserTest(unittest.TestCase):
    def test_parses_ipv4_hostname_url_and_ipv6(self):
        self.assertEqual(parse_gateway_endpoint("10.0.0.1:8081"), ("10.0.0.1", 8081))
        self.assertEqual(parse_gateway_endpoint("gateway-p:8081"), ("gateway-p", 8081))
        self.assertEqual(parse_gateway_endpoint("http://gateway-d:8082"), ("gateway-d", 8082))
        self.assertEqual(parse_gateway_endpoint("[2001:db8::1]:8081"), ("2001:db8::1", 8081))

    def test_rejects_missing_or_invalid_port(self):
        for value in ("", "gateway", "gateway:not-a-port", "gateway:70000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_gateway_endpoint(value)


class FixedGatewaySchedulerTest(unittest.TestCase):
    def test_fixed_mode_routes_without_registered_pools(self):
        scheduler = GlobalScheduler(
            Mock(),
            Mock(),
            routing_mode="fixed_gateway",
            fixed_prefill_gateway="10.0.0.1:8081",
            fixed_decode_gateway="10.0.0.2:8082",
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
        self.assertIn("mode=fixed_gateway", scheduler.routing_status())
        self.assertFalse(scheduler.switch_mode("random"))
        self.assertEqual(scheduler.current_mode, "pso")

    def test_fixed_mode_requires_both_gateways(self):
        with self.assertRaises(ValueError):
            GlobalScheduler(
                Mock(),
                Mock(),
                routing_mode="fixed_gateway",
                fixed_prefill_gateway="10.0.0.1:8081",
                fixed_decode_gateway="",
            )

    def test_global_mode_keeps_resource_validation(self):
        scheduler = GlobalScheduler(Mock(), Mock())
        with self.assertRaisesRegex(RuntimeError, "Resources empty"):
            scheduler.schedule(Mock(), Mock(), 1)

    def test_fixed_mode_ignores_cached_affinity_pool_route(self):
        scheduler = GlobalScheduler(
            Mock(),
            Mock(),
            routing_mode="fixed_gateway",
            fixed_prefill_gateway="10.0.0.1:8081",
            fixed_decode_gateway="10.0.0.2:8082",
        )
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
            "RAG": False,
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
