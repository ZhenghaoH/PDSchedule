import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "profile" / "profile_distserve_lm.py"
SPEC = importlib.util.spec_from_file_location("profile_distserve_lm", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
lm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lm
SPEC.loader.exec_module(lm)


def make_measurement(prompt_tokens, throughput, gpu_utils, repeat_index=0):
    ttft = prompt_tokens / throughput
    samples = [
        lm.GpuSample(
            timestamp_perf=float(sample_index),
            gpu_index=gpu_index,
            gpu_util_percent=float(util),
            memory_util_percent=50.0,
            memory_used_bytes=1024,
            memory_total_bytes=2048,
            power_watts=200.0,
            temperature_c=60.0,
            graphics_clock_mhz=1500.0,
            sm_clock_mhz=1500.0,
        )
        for gpu_index, values in gpu_utils.items()
        for sample_index, util in enumerate(values)
    ]
    return lm.RequestMeasurement(
        prompt_tokens=prompt_tokens,
        repeat_index=repeat_index,
        request_id=f"request-{prompt_tokens}-{repeat_index}",
        status=200,
        ttft_seconds=ttft,
        throughput_tokens_per_second=throughput,
        error="",
        gpu_samples=samples,
    )


def make_summary(prompt_tokens, throughput, gpu_p95, stable=True):
    return {
        "prompt_tokens": prompt_tokens,
        "success_count": 8,
        "failure_count": 0,
        "ttft_seconds": lm.numeric_summary([prompt_tokens / throughput]),
        "throughput_tokens_per_second": lm.numeric_summary([throughput]),
        "gpu": {
            str(index): {
                "sample_count": 8,
                "gpu_util_sample_count": 8,
                "gpu_util_percent": lm.numeric_summary([util]),
                "memory_util_percent": lm.numeric_summary([50]),
                "memory_used_bytes": lm.numeric_summary([1024]),
                "power_watts": lm.numeric_summary([200]),
                "temperature_c": lm.numeric_summary([60]),
                "graphics_clock_mhz": lm.numeric_summary([1500]),
                "sm_clock_mhz": lm.numeric_summary([1500]),
            }
            for index, util in gpu_p95.items()
        },
        "stable": stable,
    }


def test_parser_defaults_to_20_ms_sampling():
    args = lm.build_parser().parse_args(["--host", "localhost:8000"])
    assert args.gpu_sample_interval_ms == 20
    assert args.host == "localhost:8000"
    assert args.model == lm.DEFAULT_MODEL_PATH
    assert args.tokenizer == lm.DEFAULT_MODEL_PATH
    assert lm.parse_prompt_lengths(args.prompt_lengths) == [128, 256, 512, 1024, 2048, 4096, 8192]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1:8001", "http://127.0.0.1:8001/v1"),
        ("prefill.local:8100", "http://prefill.local:8100/v1"),
        ("[2001:db8::1]:8001", "http://[2001:db8::1]:8001/v1"),
    ],
)
def test_build_base_url_from_host(host, expected):
    assert lm.build_base_url(host) == expected


@pytest.mark.parametrize(
    "host",
    ["http://127.0.0.1:8001", "127.0.0.1:8001/v1", "127.0.0.1", "2001:db8::1:8001", "host:0", "host:65536"],
)
def test_host_rejects_protocol_paths_and_invalid_ports(host):
    with pytest.raises(Exception):
        lm.parse_host(host)


def test_prompt_lengths_must_be_strictly_increasing():
    with pytest.raises(Exception, match="strictly increasing"):
        lm.parse_prompt_lengths("128,128,256")
    with pytest.raises(Exception, match="positive"):
        lm.parse_prompt_lengths("0,128")


def test_gpu_indices_reject_duplicates_and_negative_values():
    assert lm.parse_gpu_indices("0,2") == [0, 2]
    with pytest.raises(Exception, match="duplicates"):
        lm.parse_gpu_indices("0,0")
    with pytest.raises(Exception, match="non-negative"):
        lm.parse_gpu_indices("-1")


def test_summary_rejects_insufficient_gpu_samples():
    measurements = [make_measurement(512, 1000, {0: [95]}, repeat_index=index) for index in range(3)]
    summary = lm.summarize_length(512, measurements, [0], 3, 0.10, min_gpu_samples=4)
    assert summary["success_count"] == 3
    assert summary["stable"] is False


def test_recommendation_selects_first_consecutive_saturated_length():
    summaries = [
        make_summary(128, 500, {0: 70}),
        make_summary(256, 930, {0: 94}),
        make_summary(512, 970, {0: 96}),
        make_summary(1024, 990, {0: 97}),
        make_summary(2048, 1000, {0: 98}),
    ]
    result = lm.recommend_lm(summaries, 3, 0.95, 2, 90, [0])
    assert result["recommended_lm_tokens"] == 512
    assert result["plateau_throughput_tokens_per_second"] == 990


def test_recommendation_requires_every_gpu_to_saturate():
    summaries = [
        make_summary(128, 500, {0: 70, 1: 70}),
        make_summary(256, 960, {0: 95, 1: 75}),
        make_summary(512, 980, {0: 96, 1: 80}),
        make_summary(1024, 990, {0: 97, 1: 95}),
        make_summary(2048, 1000, {0: 98, 1: 96}),
    ]
    result = lm.recommend_lm(summaries, 3, 0.95, 2, 90, [0, 1])
    assert result["recommended_lm_tokens"] == 1024


def test_recommendation_rejects_a_still_rising_tail():
    summaries = [
        make_summary(128, 300, {0: 95}),
        make_summary(256, 500, {0: 95}),
        make_summary(512, 700, {0: 95}),
        make_summary(1024, 900, {0: 95}),
    ]
    result = lm.recommend_lm(summaries, 3, 0.95, 2, 90, [0])
    assert result["recommended_lm_tokens"] is None
    assert "plateau" in result["reason"]


def test_write_outputs_creates_timestamped_and_latest_files(tmp_path):
    summary = make_summary(512, 1000, {0: 95})
    result = {
        "parameters": {"gpu_util_threshold": 90},
        "summaries": [summary],
        "recommendation": {
            "recommended_lm_tokens": 512,
            "plateau_throughput_tokens_per_second": 1000,
            "saturation_threshold_tokens_per_second": 950,
            "reason": "",
        },
    }
    json_path, csv_path = lm.write_outputs(result, tmp_path)
    assert json_path.exists() and csv_path.exists()
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))["recommendation"]["recommended_lm_tokens"] == 512
    assert (tmp_path / "latest.csv").read_text(encoding="utf-8").count("\n") == 2


class FakeUtilization:
    gpu = 95
    memory = 60


class FakeMemory:
    used = 1024
    total = 2048


class FakeNvml:
    NVML_TEMPERATURE_GPU = 0
    NVML_CLOCK_GRAPHICS = 0
    NVML_CLOCK_SM = 1

    def __init__(self):
        self.shutdown_called = False

    def nvmlInit(self):
        pass

    def nvmlShutdown(self):
        self.shutdown_called = True

    def nvmlDeviceGetCount(self):
        return 1

    def nvmlDeviceGetHandleByIndex(self, index):
        return index

    def nvmlDeviceGetUUID(self, handle):
        return b"GPU-fake"

    def nvmlDeviceGetName(self, handle):
        return b"Fake GPU"

    def nvmlDeviceGetMemoryInfo(self, handle):
        return FakeMemory()

    def nvmlSystemGetDriverVersion(self):
        return b"test-driver"

    def nvmlSystemGetNVMLVersion(self):
        return b"test-nvml"

    def nvmlDeviceGetUtilizationRates(self, handle):
        return FakeUtilization()

    def nvmlDeviceGetPowerUsage(self, handle):
        return 200000

    def nvmlDeviceGetTemperature(self, handle, sensor):
        return 60

    def nvmlDeviceGetClockInfo(self, handle, clock_type):
        return 1500


def test_nvml_monitor_samples_and_cleans_up():
    backend = FakeNvml()
    monitor = lm.NvmlMonitor([0], interval_ms=5, nvml=backend)
    monitor.initialize()
    monitor.start()
    time.sleep(0.025)
    samples = monitor.stop()
    monitor.close()
    assert len(samples) >= 2
    assert all(sample.gpu_util_percent == 95 for sample in samples)
    assert backend.shutdown_called


@pytest.mark.loopback
def test_measure_request_handles_real_sse_framing():
    async def exercise():
        import aiohttp
        from aiohttp import web

        async def handler(request):
            response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b'data: {"id": "response-1", "choices": []}\r\n\r\n')
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets
        port = sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                result = await lm.measure_request(
                    session,
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    "model",
                    "prompt",
                    128,
                    0,
                    {"Authorization": "Bearer EMPTY"},
                    None,
                    2,
                )
            assert result.succeeded
            assert result.throughput_tokens_per_second is not None
        finally:
            await runner.cleanup()

    asyncio.run(exercise())
