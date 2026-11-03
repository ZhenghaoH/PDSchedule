import re
import requests
import os
from typing import Iterable, List
from config.network.default import NetworkConfig
# pip install prometheus-client -i https://pypi.tuna.tsinghua.edu.cn/simple/
from prometheus_client.parser import text_string_to_metric_families

try:
    import pynvml
except Exception:
    pynvml = None

GPU_UTIL_EMA_ALPHA = 0.3
_GPU_UTIL_EMA_STATE = {}


def _smooth_gpu_util_percent(gpu_index: int, current_value: float) -> float:
    previous_value = _GPU_UTIL_EMA_STATE.get(gpu_index)
    if previous_value is None:
        smoothed_value = current_value
    else:
        smoothed_value = (
            GPU_UTIL_EMA_ALPHA * current_value
            + (1 - GPU_UTIL_EMA_ALPHA) * previous_value
        )
    _GPU_UTIL_EMA_STATE[gpu_index] = smoothed_value
    return smoothed_value


def parse_metric_log(filename="metric_log.txt"):
    """
    Read and parse metrics from the last non-empty line of metric_log.txt
    Args:
        filename (str): Log filename
    Returns:
        dict: Dictionary containing all parsed metrics, or None if parsing fails
    """
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()
            if not lines:
                print("File is empty")
                return None

            # Find the last non-empty line
            last_valid_line = None
            for line in reversed(lines):
                stripped_line = line.strip()
                if stripped_line:  # Check if line is not empty
                    last_valid_line = stripped_line
                    break

            if last_valid_line is None:
                print("No valid data found in file")
                return None

            # print(f"Last valid line content: {last_valid_line}")

            # Regular expression pattern
            pattern = r"Avg prompt throughput: ([\d.]+) tokens/s, Avg generation throughput: ([\d.]+) tokens/s, Running: ([\d.]+) reqs, Swapped: ([\d.]+) reqs, Pending: ([\d.]+) reqs, GPU KV cache usage: ([\d.]+)%, CPU KV cache usage: ([\d.]+)%"

            # Match regular expression
            match = re.match(pattern, last_valid_line)

            if match:
                # Extract all matched groups
                metrics = {
                    'avg_prompt_throughput': float(match.group(1)),
                    'avg_generation_throughput': float(match.group(2)),
                    'running': int(match.group(3)),
                    'swapped': int(match.group(4)),
                    'pending': int(match.group(5)),
                    'gpu_kv_cache_usage': float(match.group(6)),
                    'cpu_kv_cache_usage': float(match.group(7))
                }
                return metrics
            else:
                print("Regular expression matching failed")
                return None

    except FileNotFoundError:
        print(f"File {filename} not found")
        return None
    except Exception as e:
        print(f"Error reading file: {e}")
        return None


def fetch_prometheus_metrics(host: str):
    """获取所有vLLM指标, 输入host为vLLM IP地址带端口"""
    url = f"http://{host}/metrics"
    try:
        response = requests.get(url, timeout=2)
        response.raise_for_status()

        metrics_data = {}
        for family in text_string_to_metric_families(response.text):
            metrics_data[family.name] = {
                'type': family.type,
                'samples': []
            }
            for sample in family.samples:
                metrics_data[family.name]['samples'].append({
                    'name': sample.name,
                    'value': sample.value,
                    'labels': sample.labels
                })
        return metrics_data
    except Exception as e:
        print(f"❌ fetch {url} prometheus error: {e}")
        return None


def _normalize_gpu_indices(gpu_indices: Iterable[int] | None) -> List[int]:
    if gpu_indices is None:
        gpu_indices = [int(os.getenv("MY_PCIE_GPU_INDEX", "0"))]
    normalized = []
    for value in gpu_indices:
        index = int(value)
        if index < 0:
            raise ValueError("GPU indices must be non-negative")
        if index not in normalized:
            normalized.append(index)
    return normalized or [0]


def get_gpu_device_metrics(gpu_indices: Iterable[int] | None = None):
    """Return per-device GPU utilization and real VRAM capacity occupancy."""
    indices = _normalize_gpu_indices(gpu_indices)
    if pynvml is None:
        return [
            {
                "gpu_index": index,
                "device_name": "unknown",
                "gpu_util_percent": 0.0,
                "gpu_memory_used_bytes": 0,
                "gpu_memory_total_bytes": 0,
                "gpu_memory_util_percent": 0.0,
                "metric_source": "pynvml_unavailable",
            }
            for index in indices
        ]

    results = []
    try:
        pynvml.nvmlInit()
        for index in indices:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                device_name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(device_name, bytes):
                    device_name = device_name.decode("utf-8", errors="ignore")
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                memory_total = max(0, int(getattr(memory, "total", 0) or 0))
                memory_used = max(0, int(getattr(memory, "used", 0) or 0))
                memory_percent = (
                    memory_used / memory_total * 100.0 if memory_total > 0 else 0.0
                )
                results.append(
                    {
                        "gpu_index": index,
                        "device_name": str(device_name),
                        "gpu_util_percent": _smooth_gpu_util_percent(
                            index,
                            float(getattr(utilization, "gpu", 0.0)),
                        ),
                        "gpu_memory_used_bytes": memory_used,
                        "gpu_memory_total_bytes": memory_total,
                        "gpu_memory_util_percent": memory_percent,
                        "metric_source": "nvml",
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "gpu_index": index,
                        "device_name": "unknown",
                        "gpu_util_percent": 0.0,
                        "gpu_memory_used_bytes": 0,
                        "gpu_memory_total_bytes": 0,
                        "gpu_memory_util_percent": 0.0,
                        "metric_source": f"nvml_error:{type(exc).__name__}",
                    }
                )
        return results
    except Exception as exc:
        return [
            {
                "gpu_index": index,
                "device_name": "unknown",
                "gpu_util_percent": 0.0,
                "gpu_memory_used_bytes": 0,
                "gpu_memory_total_bytes": 0,
                "gpu_memory_util_percent": 0.0,
                "metric_source": f"nvml_error:{type(exc).__name__}",
            }
            for index in indices
        ]
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def get_gpu_bandwidth_utilization():
    """Backward-compatible single-device aggregate for existing callers."""
    device = get_gpu_device_metrics()[0]
    return {
        "gpu_index": device["gpu_index"],
        "device_name": device["device_name"],
        "memory_util_percent": device["gpu_memory_util_percent"],
        "gpu_util_percent": device["gpu_util_percent"],
        "source": device["metric_source"],
    }


def get_mock_network_metrics():
    network_config = NetworkConfig()
    return {
        "bandwidth": network_config.bottleneck_bandwidth,
        "rtt": network_config.round_trip_time
    }


def get_mock_prometheus_metrics():
    return {
        "running_reqs": 0,
        "waiting_reqs": 0
    }


if __name__ == '__main__':
    vllm_host = "172.18.0.166:8001"  # vLLM实例暴露的接口
    prometheus_metrics = fetch_prometheus_metrics(vllm_host)

    running_reqs = int(prometheus_metrics['vllm:num_requests_running']['samples'][0]['value'])
    waiting_reqs = int(prometheus_metrics['vllm:num_requests_waiting']['samples'][0]['value'])

    print(f"{running_reqs} reqs running")
    print(f"{waiting_reqs} reqs waiting")
