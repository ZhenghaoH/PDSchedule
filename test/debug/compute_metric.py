import re
import time
import sys
import requests
import psutil
from prometheus_client.parser import text_string_to_metric_families

REPORT_LABEL = "172.18.0.166:8001"
REPORT_URL = "http://172.18.0.166:8081/status/compute"
PROMETHEUS_URL = "http://172.18.0.166:8001/metrics"  # vLLM实例暴露的测量指标接口
LOG_PATH = "workspace/workspace/prefill_log.txt"  # 读取vLLM日志的地址
MEASURE_INTERVAL = 1  # 测量带宽使用的时间间隔（秒）
UPDATE_INTERVAL = 5  # 更新显示的间隔时间（秒）
NETWORK_INTERFACE = "eno2np1"  # 上报哪个网卡的带宽

GPU_MEMORY_MIB = 24576
MODEL_PARAMS = {
    "num_layers": 28,
    "num_kv_heads": 2,
    "head_size": 128,
    "dtype_bytes": 2  # fp16 = 2 bytes
}


def get_gpu_memory_bytes():
    return GPU_MEMORY_MIB * 1024 * 1024


def get_bytes_per_token():
    return 2 * MODEL_PARAMS["num_layers"] * MODEL_PARAMS["num_kv_heads"] * MODEL_PARAMS["head_size"] * MODEL_PARAMS["dtype_bytes"]


def calculate_vllm_memory_usage(gpu_memory_bytes, gpu_memory_utilization, current_kv_cache_usage_percent):
    vllm_total_memory_bytes = gpu_memory_bytes * gpu_memory_utilization
    used_kv_cache_memory_bytes = vllm_total_memory_bytes * current_kv_cache_usage_percent
    remaining_vllm_memory_bytes = vllm_total_memory_bytes - used_kv_cache_memory_bytes
    return int(remaining_vllm_memory_bytes)


def calculate_remaining_tokens_by_memory(vllm_memory_bytes, bytes_per_token):
    return int(vllm_memory_bytes / bytes_per_token)


def calculate_remaining_tokens_by_blocks(block_size, gpu_memory_block, current_kv_cache_usage_percent):
    return int(block_size * gpu_memory_block * (1 - current_kv_cache_usage_percent))


def get_bandwidth_usage(interval=1):
    """获取指定时间间隔内的带宽使用情况（上行/下行）"""
    old = psutil.net_io_counters()
    time.sleep(interval)
    new = psutil.net_io_counters()

    # 计算每秒的平均带宽使用（Mbps）
    upload = (new.bytes_sent - old.bytes_sent) / interval / 1024 / 1024 * 8
    download = (new.bytes_recv - old.bytes_recv) / interval / 1024 / 1024 * 8

    return upload, download


def get_max_bandwidth():
    """获取网络接口的理论最大带宽"""
    stats = psutil.net_if_stats()
    max_bandwidths = {}

    for name, info in stats.items():
        if info.isup and info.speed > 0:
            max_bandwidths[name] = info.speed

    return max_bandwidths


def calculate_available_bandwidth(current_upload, current_download, max_bandwidth):
    """计算剩余可用带宽"""
    # 不再假设上下行对称，而是使用总带宽
    total_used = current_upload + current_download
    total_available = max(0, max_bandwidth - total_used)

    # 返回总可用带宽，也可以根据需要返回上下行分别的可用带宽
    return total_available


def format_bandwidth_info(name, current_up, current_down, max_bw, available_total):
    """格式化带宽信息输出"""
    print(f"\n--- {name} ---")
    print(f"Total Bandwidth: {max_bw:.2f} Mbps")
    print(
        f"Current Usage: Upload {current_up:.2f} Mbps / Download {current_down:.2f} Mbps / Total {current_up + current_down:.2f} Mbps")
    print(f"Available Bandwidth: Total {available_total:.2f} Mbps")

    # 添加使用率百分比
    total_used = current_up + current_down
    usage_total_pct = (total_used / max_bw) * 100 if max_bw > 0 else 0
    usage_up_pct = (current_up / max_bw) * 100 if max_bw > 0 else 0
    usage_down_pct = (current_down / max_bw) * 100 if max_bw > 0 else 0

    print(f"Usage Rate: Upload {usage_up_pct:.1f}% / Download {usage_down_pct:.1f}% / Total {usage_total_pct:.1f}%")


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


def fetch_prometheus_metrics(url=PROMETHEUS_URL):
    """获取所有vLLM指标"""
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()

        metrics_data = {}
        for family in text_string_to_metric_families(response.text):
            metrics_data[family.name] = {
                'type': family.type,
                'samples': []
            }
            for sample in family.samples:
                metrics_data[family.name]['samples'].append({
                    'value': sample.value,
                    'labels': sample.labels
                })
        return metrics_data
    except Exception as e:
        print(f"❌ error: {str(e)}")
        return None


def main():
    """Main function"""
    print("🚀 vLLM 指标监控开始（按 Ctrl+C 停止）")

    # 获取最大带宽信息（只需要一次，因为通常不会变化）
    max_bandwidths = get_max_bandwidth()
    gpu_memory_bytes = get_gpu_memory_bytes()
    bytes_per_token = get_bytes_per_token()

    if not max_bandwidths:
        print("未找到有效的网络接口")
        return
    try:
        while True:
            # 记录开始时间
            start_time = time.time()

            log_metrics = parse_metric_log(LOG_PATH)
            prometheus_metrics = fetch_prometheus_metrics(PROMETHEUS_URL)

            avg_prompt_throughput = log_metrics['avg_prompt_throughput']
            avg_generation_throughput = log_metrics['avg_generation_throughput']
            gpu_kv_cache_usage = log_metrics['gpu_kv_cache_usage'] / 100

            running_reqs = int(prometheus_metrics['vllm:num_requests_running']['samples'][0]['value'])
            waiting_reqs = int(prometheus_metrics['vllm:num_requests_waiting']['samples'][0]['value'])
            num_gpu_blocks = int(prometheus_metrics['vllm:cache_config_info']['samples'][0]['labels']['num_gpu_blocks'])
            block_size = int(prometheus_metrics['vllm:cache_config_info']['samples'][0]['labels']['block_size'])
            gpu_memory_utilization = float(prometheus_metrics['vllm:cache_config_info']['samples'][0]['labels']['gpu_memory_utilization'])
            vllm_memory_bytes = calculate_vllm_memory_usage(gpu_memory_bytes, gpu_memory_utilization, gpu_kv_cache_usage)
            remaining_tokens1 = calculate_remaining_tokens_by_memory(vllm_memory_bytes, bytes_per_token)
            remaining_tokens2 = calculate_remaining_tokens_by_blocks(block_size, num_gpu_blocks, gpu_kv_cache_usage)

            network_metric = {}

            print(
                "The following indicators have been detected:",
                f"running_reqs: {running_reqs}",
                f"waiting_reqs: {waiting_reqs}",
                f"avg_prompt_throughput: {avg_prompt_throughput} tokens/s",
                f"avg_generation_throughput: {avg_generation_throughput} tokens/s",
                f"gpu_kv_cache_usage: {gpu_kv_cache_usage * 100: .1f} %",
                f"vllm_memory_bytes: {vllm_memory_bytes} bytes",
                f"remaining_tokens: {remaining_tokens2}",
                sep="\n"
            )

            # 获取当前带宽使用情况（使用测量间隔时间）
            current_upload, current_download = get_bandwidth_usage(MEASURE_INTERVAL)

            # 为每个接口计算并显示带宽信息
            for name, max_bw in max_bandwidths.items():
                available_total = calculate_available_bandwidth(
                    current_upload, current_download, max_bw
                )
                format_bandwidth_info(
                    name, current_upload, current_download,
                    max_bw, available_total
                )
                network_metric[name] = {
                    "available": available_total,
                    "bandwidth": max_bw,
                    "current_upload": current_upload,
                    "current_download": current_download
                }

            if NETWORK_INTERFACE in network_metric.keys():
                payload = {
                    "report_label": REPORT_LABEL,
                    "running_reqs": running_reqs,
                    "waiting_reqs": waiting_reqs,
                    "avg_prompt_throughput": avg_prompt_throughput,
                    "avg_generation_throughput": avg_generation_throughput,
                    "vllm_memory_bytes": vllm_memory_bytes,
                    "remaining_tokens": remaining_tokens2,
                    "available_bw": network_metric[NETWORK_INTERFACE]["available"],
                    "upload_bw": network_metric[NETWORK_INTERFACE]["current_upload"],
                    "download_bw": network_metric[NETWORK_INTERFACE]["current_download"]
                }
            else:
                payload = {
                    "report_label": REPORT_LABEL,
                    "running_reqs": running_reqs,
                    "waiting_reqs": waiting_reqs,
                    "avg_prompt_throughput": avg_prompt_throughput,
                    "avg_generation_throughput": avg_generation_throughput,
                    "vllm_memory_bytes": vllm_memory_bytes,
                    "remaining_tokens": remaining_tokens2,
                }

            headers = {"Content-Type": "application/json"}

            try:
                response = requests.post(REPORT_URL, json=payload, headers=headers)
                if response.status_code == 200:
                    # print("Report Compute Request success.")
                    pass

            except Exception:
                exc_info = sys.exc_info()
                error_messages = [str(e) for e in exc_info if e]
                print("Error occurred in function close_d_instance")
                print(error_messages)

            # 计算剩余等待时间
            elapsed_time = time.time() - start_time
            remaining_wait = max(0, int(UPDATE_INTERVAL - elapsed_time))
            print(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} - Next update in {remaining_wait:.1f} seconds...")
            print("=" * 60)
            # 等待剩余时间
            if remaining_wait > 0:
                time.sleep(remaining_wait)
    except KeyboardInterrupt:
        print("\n监控已停止")
        #  防止日志保存过多，读取完清理一下
        with open(LOG_PATH, 'w') as f:
            pass


if __name__ == "__main__":
    main()
