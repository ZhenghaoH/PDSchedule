import logging
import os
import requests
import time
from typing import List, Optional

from store.instance.store import InstanceStore
from instance.perception.metric import (
    fetch_prometheus_metrics,
    get_gpu_device_metrics,
    get_mock_prometheus_metrics,
)
from instance.perception.compute_metric_h import ThroughputMonitor
from utils.network_metric import get_interface_metrics


def instance_register():
    storage = InstanceStore()
    target_url = f"http://{storage.gateway}/instance/register"
    payload = {
        "report_label": storage.report_label(),
        "role": storage.role,
        "connector": storage.connector,
        "vllm_port": storage.vllm_port,
        "gpu_indices": storage.gpu_indices,
    }
    try:
        response = requests.post(target_url, json=payload)
        if response.status_code == 200:
            return True
        logging.warning("[Instance] 注册失败: %s - %s", response.status_code, response.text)
    except Exception as exc:
        logging.warning("[Instance] 注册错误: %s", exc)
    return False


def _get_metric_sample_value(metrics: dict, names: List[str], default: float = 0.0) -> float:
    for name in names:
        metric = metrics.get(name)
        samples = metric.get("samples", []) if metric else []
        if samples:
            return float(samples[0].get("value", default))
    return default


def _get_kv_cache_usage_percent(metrics: dict) -> float:
    usage = _get_metric_sample_value(
        metrics,
        ["vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"],
        0.0,
    )
    return usage * 100.0 if usage <= 1.0 else usage


def _cached_network_metrics(storage: InstanceStore) -> dict:
    now = time.monotonic()
    sample_interval = max(
        0.1,
        float(os.getenv("FLEXPD_INSTANCE_NETWORK_SAMPLE_SEC", "1")),
    )
    if (
        storage.network_metric
        and now - float(storage.network_metric_sampled_at) < sample_interval
    ):
        return dict(storage.network_metric)

    try:
        sampled = get_interface_metrics(storage.host, interval=min(0.05, sample_interval))
    except Exception as exc:
        logging.debug("[Instance] Network metric collection failed: %s", exc)
        sampled = None
    storage.network_metric = dict(sampled or {})
    storage.network_metric_sampled_at = now
    return dict(storage.network_metric)


def _aggregate_gpu_metrics(gpu_devices: List[dict]) -> tuple[float, float]:
    if not gpu_devices:
        return 0.0, 0.0
    gpu_util = sum(float(item.get("gpu_util_percent", 0.0) or 0.0) for item in gpu_devices)
    memory_util = sum(
        float(item.get("gpu_memory_util_percent", 0.0) or 0.0)
        for item in gpu_devices
    )
    count = len(gpu_devices)
    return gpu_util / count, memory_util / count


def metric_update(monitor: Optional[ThroughputMonitor] = None):
    endpoint = "instance/update"
    storage = InstanceStore()
    target_url = f"http://{storage.gateway}/{endpoint}"
    try:
        if storage.USE_MOCK:
            metrics = get_mock_prometheus_metrics()
            running_reqs = metrics["running_reqs"]
            waiting_reqs = metrics["waiting_reqs"]
            prompt_throughput = 0.0
            generate_throughput = 0.0
            kv_cache_usage_percent = 0.0
            gpu_devices = [
                {
                    "gpu_index": index,
                    "device_name": "mock",
                    "gpu_util_percent": 0.0,
                    "gpu_memory_used_bytes": 0,
                    "gpu_memory_total_bytes": 0,
                    "gpu_memory_util_percent": 0.0,
                    "metric_source": "mock",
                }
                for index in storage.gpu_indices
            ]
        else:
            prometheus_metrics = fetch_prometheus_metrics(storage.vllm_addr())
            gpu_devices = get_gpu_device_metrics(storage.gpu_indices)

            if prometheus_metrics:
                running_reqs = int(prometheus_metrics['vllm:num_requests_running']['samples'][0]['value'])
                waiting_reqs = int(prometheus_metrics['vllm:num_requests_waiting']['samples'][0]['value'])
                kv_cache_usage_percent = _get_kv_cache_usage_percent(prometheus_metrics)
                # logging.info(
                #     "[Instance] running_reqs=%s waiting_reqs=%s",
                #     running_reqs,
                #     waiting_reqs,
                # )
                # logging.info(
                #     "[Instance] gpu_index=%s device=%s memory_util_percent=%.2f gpu_util_percent=%.2f source=%s",
                #     gpu_bandwidth_metrics["gpu_index"],
                #     gpu_bandwidth_metrics["device_name"],
                #     gpu_bandwidth_metrics["memory_util_percent"],
                #     gpu_bandwidth_metrics["gpu_util_percent"],
                #     gpu_bandwidth_metrics["source"],
                # )
            else:
                running_reqs = 0
                waiting_reqs = 0
                kv_cache_usage_percent = 0.0
                # logging.info(
                #     "[Instance] running_reqs=%s waiting_reqs=%s",
                #     running_reqs,
                #     waiting_reqs,
                # )
                # logging.info(
                #     "[Instance] gpu_index=%s device=%s memory_util_percent=%.2f gpu_util_percent=%.2f source=%s",
                #     gpu_bandwidth_metrics["gpu_index"],
                #     gpu_bandwidth_metrics["device_name"],
                #     gpu_bandwidth_metrics["memory_util_percent"],
                #     gpu_bandwidth_metrics["gpu_util_percent"],
                #     gpu_bandwidth_metrics["source"],
                # )

            if monitor:
                prompt_throughput, generate_throughput = monitor.get_prometheus_throughput()
            else:
                prompt_throughput = 0.0
                generate_throughput = 0.0

        gpu_util_percent, gpu_memory_util_percent = _aggregate_gpu_metrics(gpu_devices)
        network_metric = _cached_network_metrics(storage)

        payload = {
            "report_label": storage.report_label(),
            "running_reqs": running_reqs,
            "waiting_reqs": waiting_reqs,
            "prompt_throughput": prompt_throughput,
            "generate_throughput": generate_throughput,
            "kv_cache_usage_percent": kv_cache_usage_percent,
            "gpu_util_percent": gpu_util_percent,
            "gpu_memory_util_percent": gpu_memory_util_percent,
            "gpu_devices": gpu_devices,
            "network_metric": network_metric,
        }
        resp = requests.post(target_url, json=payload)
        if resp.status_code == 404:
            instance_register()
        if resp.status_code != 200:
            logging.warning(f"[Instance] 心跳异常: {resp.status_code}")
    except Exception as e:
        logging.error(f"[Instance] 心跳错误: {e}")


def instance_unregister():
    endpoint = "instance/unregister"
    storage = InstanceStore()
    target_url = f"http://{storage.gateway}/{endpoint}"
    try:
        payload = {
            "report_label": storage.report_label(),
        }
        resp = requests.post(target_url, json=payload)
        if resp.status_code != 200:
            logging.warning(f"[Instance] 注销异常: {resp.status_code}")
    except Exception as e:
        logging.warning(f"[Instance] 注销错误: {e}")
