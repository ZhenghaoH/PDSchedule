import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from instance.perception import metric as instance_metric
from utils.gateway_observability import GatewayObservability, build_end_to_end_timing


def read_json_lines(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class GatewayObservabilityTest(unittest.TestCase):
    def test_creates_one_directory_and_keeps_log_types_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "nested" / "gateway_logs"
            prefill = GatewayObservability("prefill", log_dir=log_dir)
            decode = GatewayObservability("decode", log_dir=log_dir)
            prefill.record_schedule(request_id="r1", candidates=[{"instance": "p1"}], reason="test")
            prefill.record_timing("request_arrived", request_id="r1")
            decode.record_schedule(request_id="r1", candidates=[{"instance": "d1"}], reason="test")
            decode.record_timing("request_finished", request_id="r1")
            prefill.record_instance_resources(
                "p1",
                network={"interface": "eth0", "tx_mbps": 1, "rx_mbps": 0, "usage_percent": 1},
                now_s=1,
            )
            decode.record_instance_resources(
                "d1",
                gpu_devices=[{"gpu_index": 2, "gpu_util_percent": 10, "gpu_memory_util_percent": 20}],
                now_s=1,
            )

            self.assertEqual(
                {path.name for path in log_dir.iterdir()},
                {
                    "prefill_gateway_schedule.log",
                    "prefill_gateway_timing.log",
                    "prefill_gateway_resource.log",
                    "decode_gateway_schedule.log",
                    "decode_gateway_timing.log",
                    "decode_gateway_resource.log",
                },
            )
            schedule = read_json_lines(prefill.schedule_path)[0]
            timing = read_json_lines(prefill.timing_path)[0]
            self.assertEqual(schedule["log_type"], "schedule")
            self.assertIn("candidates", schedule)
            self.assertNotIn("event", schedule)
            self.assertEqual(timing["log_type"], "timing")
            self.assertEqual(timing["event"], "request_arrived")
            self.assertNotIn("candidates", timing)

    def test_zero_resources_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            observer = GatewayObservability(
                "prefill", log_dir=temp_dir, resource_interval_s=1, zero_pause_s=30
            )
            zero = {"interface": "eth0", "tx_mbps": 0, "rx_mbps": 0, "usage_percent": 0}
            self.assertEqual(observer.record_instance_resources("p1", network=zero, now_s=0), 1)
            self.assertEqual(observer.record_instance_resources("p1", network=zero, now_s=30), 1)
            self.assertEqual(observer.record_instance_resources("p1", network=zero, now_s=31), 0)
            active = {**zero, "tx_mbps": 5, "usage_percent": 2}
            self.assertEqual(observer.record_instance_resources("p1", network=active, now_s=32), 1)
            states = [item["record_state"] for item in read_json_lines(observer.resource_path)]
            self.assertEqual(states, ["sample", "logging_paused", "logging_resumed"])

    def test_builds_end_to_end_timing(self):
        summary = build_end_to_end_timing(
            {"prefill_arrival_s": 1.0, "prefill_start_s": 1.1, "prefill_end_s": 1.6},
            decode_arrival_s=1.7,
            decode_forward_s=1.8,
            decode_first_chunk_s=2.0,
            decode_end_s=3.0,
        )
        self.assertAlmostEqual(summary["prefill_queue_ms"], 100)
        self.assertAlmostEqual(summary["prefill_ms"], 500)
        self.assertAlmostEqual(summary["decode_gateway_queue_ms"], 100)
        self.assertAlmostEqual(summary["observed_network_transfer_ms"], 200)
        self.assertAlmostEqual(summary["decode_stream_ms"], 1000)
        self.assertAlmostEqual(summary["gateway_e2e_ms"], 2000)
        self.assertEqual(
            summary["network_time_scope"],
            "nixl_pull_plus_vllm_admission_plus_first_token",
        )

    def test_missing_first_chunk_keeps_unobservable_durations_null(self):
        summary = build_end_to_end_timing(
            {}, decode_arrival_s=1, decode_forward_s=2, decode_first_chunk_s=None, decode_end_s=3
        )
        self.assertIsNone(summary["observed_network_transfer_ms"])
        self.assertIsNone(summary["decode_stream_ms"])
        self.assertEqual(summary["decode_total_ms"], 1000)

    def test_gpu_metrics_report_each_card_and_real_vram_occupancy(self):
        fake_nvml = SimpleNamespace(
            nvmlInit=lambda: None,
            nvmlShutdown=lambda: None,
            nvmlDeviceGetHandleByIndex=lambda index: index,
            nvmlDeviceGetName=lambda handle: f"GPU-{handle}".encode(),
            nvmlDeviceGetUtilizationRates=lambda handle: SimpleNamespace(gpu=40 + handle),
            nvmlDeviceGetMemoryInfo=lambda handle: SimpleNamespace(
                used=(handle + 1) * 25,
                total=100,
            ),
        )
        with patch.object(instance_metric, "pynvml", fake_nvml):
            devices = instance_metric.get_gpu_device_metrics([0, 1])

        self.assertEqual([item["gpu_index"] for item in devices], [0, 1])
        self.assertEqual([item["device_name"] for item in devices], ["GPU-0", "GPU-1"])
        self.assertEqual(
            [item["gpu_memory_util_percent"] for item in devices], [25.0, 50.0]
        )


if __name__ == "__main__":
    unittest.main()
