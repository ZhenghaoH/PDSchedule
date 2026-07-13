import logging
import requests
from typing import List, Optional

from store.instance.store import InstanceStore
from instance.perception.metric import (
    fetch_prometheus_metrics,
    get_gpu_bandwidth_utilization,
    get_mock_prometheus_metrics,
)
from instance.perception.compute_metric_h import ThroughputMonitor


def instance_register():
    storage = InstanceStore()
    target_url = f"http://{storage.gateway}/instance/register"
    payload = {
        "report_label": storage.report_label(),
        "role": storage.role,
        "connector": storage.connector,
        "side_channel_host": storage.side_channel_host,
        "side_channel_port": storage.side_channel_port,
        "tp_size": storage.tp_size,
        "vllm_port": storage.vllm_port,
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
            gpu_util_percent = 0.0
            gpu_memory_util_percent = 0.0
        else:
            prometheus_metrics = fetch_prometheus_metrics(storage.vllm_addr())
            gpu_bandwidth_metrics = get_gpu_bandwidth_utilization()
            gpu_util_percent = float(gpu_bandwidth_metrics.get("gpu_util_percent", 0.0))
            gpu_memory_util_percent = float(gpu_bandwidth_metrics.get("memory_util_percent", 0.0))

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

        payload = {
            "report_label": storage.report_label(),
            "running_reqs": running_reqs,
            "waiting_reqs": waiting_reqs,
            "prompt_throughput": prompt_throughput,
            "generate_throughput": generate_throughput,
            "kv_cache_usage_percent": kv_cache_usage_percent,
            "gpu_util_percent": gpu_util_percent,
            "gpu_memory_util_percent": gpu_memory_util_percent,
        }
        resp = requests.post(target_url, json=payload)
        if resp.status_code == 404:
            instance_register()
        if resp.status_code != 200:
            logging.warning(f"[Instance] 心跳异常: {resp.status_code}")
    except Exception as e:
        logging.error(f"[Instance] 心跳错误: {e}")


def close_instance_pair():
    endpoint = "pd/close"
    storage = InstanceStore()
    target_url = f"http://{storage.gateway}/{endpoint}"
    try:
        payload = {
            "label": storage.report_label(),
        }
        resp = requests.post(target_url, json=payload)
        if resp.status_code != 200:
            logging.warning(f"[Instance] 关闭连接上报异常: {resp.status_code}")
    except Exception as e:
        logging.error(f"[Instance] 关闭连接上报错误: {e}")


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
