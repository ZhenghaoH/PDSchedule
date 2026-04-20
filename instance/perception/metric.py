import re
import requests
import os
from config.network.default import NetworkConfig
# pip install prometheus-client -i https://pypi.tuna.tsinghua.edu.cn/simple/
from prometheus_client.parser import text_string_to_metric_families

try:
    import pynvml
except Exception:
    pynvml = None


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
        response = requests.get(url)
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


def get_gpu_bandwidth_utilization():
    """
    读取 GPU memory utilization，作为测试期的“GPU 带宽占用率”代理指标。
    仅用于实例侧日志观测，不参与当前正式上报链路。
    """
    gpu_index = int(os.getenv("MY_PCIE_GPU_INDEX", "0"))

    if pynvml is None:
        return {
            "gpu_index": gpu_index,
            "device_name": "unknown",
            "memory_util_percent": 0.0,
            "gpu_util_percent": 0.0,
            "source": "pynvml_unavailable",
        }

    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
        device_name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(device_name, bytes):
            device_name = device_name.decode("utf-8", errors="ignore")

        utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return {
            "gpu_index": gpu_index,
            "device_name": device_name,
            "memory_util_percent": float(getattr(utilization, "memory", 0.0)),
            "gpu_util_percent": float(getattr(utilization, "gpu", 0.0)),
            "source": "nvml",
        }
    except Exception as exc:
        return {
            "gpu_index": gpu_index,
            "device_name": "unknown",
            "memory_util_percent": 0.0,
            "gpu_util_percent": 0.0,
            "source": f"nvml_error:{type(exc).__name__}",
        }
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


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
