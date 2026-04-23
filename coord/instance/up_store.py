import logging
import requests
from typing import Optional

from store.instance.store import InstanceStore
from instance.perception.metric import (
    fetch_prometheus_metrics,
    get_gpu_bandwidth_utilization,
    get_mock_prometheus_metrics,
)
from instance.perception.compute_metric_h import ThroughputMonitor


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
        else:
            prometheus_metrics = fetch_prometheus_metrics(storage.vllm_addr())
            gpu_bandwidth_metrics = get_gpu_bandwidth_utilization()

            if prometheus_metrics:
                running_reqs = int(prometheus_metrics['vllm:num_requests_running']['samples'][0]['value'])
                waiting_reqs = int(prometheus_metrics['vllm:num_requests_waiting']['samples'][0]['value'])
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
            "generate_throughput": generate_throughput
        }
        resp = requests.post(target_url, json=payload)
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
