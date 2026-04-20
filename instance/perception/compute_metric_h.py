import time
import requests
import threading
import re  
from collections import deque
from typing import Optional, Deque, Tuple

# 假设 config.network.default 在你的项目中存在
try:
    from config.network.default import NetworkConfig
except ImportError:
    class NetworkConfig: # Mock for standalone run
        bottleneck_bandwidth = 1000
        round_trip_time = 0.1

from prometheus_client.parser import text_string_to_metric_families


def parse_metric_log(filename="metric_log.txt"):
    """
    Read and parse metrics from the last non-empty line of metric_log.txt
    """
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()
            if not lines:
                print("File is empty")
                return None

            last_valid_line = None
            for line in reversed(lines):
                stripped_line = line.strip()
                if stripped_line:
                    last_valid_line = stripped_line
                    break

            if last_valid_line is None:
                print("No valid data found in file")
                return None

            pattern = r"Avg prompt throughput: ([\d.]+) tokens/s, Avg generation throughput: ([\d.]+) tokens/s, Running: ([\d.]+) reqs, Swapped: ([\d.]+) reqs, Pending: ([\d.]+) reqs, GPU KV cache usage: ([\d.]+)%, CPU KV cache usage: ([\d.]+)%"
            match = re.match(pattern, last_valid_line)

            if match:
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
        # 设置超时，防止卡死
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
                    'value': sample.value,
                    'labels': sample.labels
                })
        return metrics_data
    except Exception as e:
        # print(f"❌ fetch {url} prometheus error: {e}")
        return None


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


class ThroughputMonitor:
    def __init__(self, host: str, interval: float = 0.1, window_size: float = 2.0):
        """
        :param host: vLLM 地址 (ip:port)
        :param interval: 采样间隔 (秒)，默认 0.1s
        :param window_size: 统计窗口大小 (秒)，建议 >= 1.0s 以平滑波动
        """
        self.host = host
        self.interval = interval
        self.window_size = window_size
        
        # 滑动窗口数据: 存储 (timestamp, prompt_tokens, generation_tokens) 的原始采样点
        # 修改：Tuple 从 (float, float) 变为 (float, float, float)
        self._history: Deque[Tuple[float, float, float]] = deque()
        self._lock = threading.Lock()
        
        # 线程控制
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _get_token_counts(self, metrics) -> Tuple[float, float]:
        """从 metrics 字典中分别提取 prompt 和 generation 的累积 token 数"""
        try:
            p_data = metrics.get('vllm:prompt_tokens_total', metrics.get('vllm:prompt_tokens', {})).get('samples', [])
            p_val = p_data[0]['value'] if p_data else 0.0
            
            g_data = metrics.get('vllm:generation_tokens_total', metrics.get('vllm:generation_tokens', {})).get('samples', [])
            g_val = g_data[0]['value'] if g_data else 0.0
            
            return p_val, g_val
        except Exception:
            return 0.0, 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print(f"🚀 [Monitor] Started for {self.host} | Sampling: {self.interval}s | Window: {self.window_size}s")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()
        print("🛑 [Monitor] Stopped")

    def _monitor_loop(self):
        """后台循环：仅负责高频采集原始数据"""
        while self._running:
            cycle_start = time.time()
            
            # 1. 获取数据
            metrics = fetch_prometheus_metrics(self.host)
            
            if metrics:
                p_val, g_val = self._get_token_counts(metrics)
                curr_time = time.time()

                with self._lock:
                    # 2. 存入新采样点 (Time, Prompt, Generation)
                    self._history.append((curr_time, p_val, g_val))
                    
                    # 3. 清理过期数据
                    self._cleanup_history(curr_time)

            # 4. 控制频率
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _cleanup_history(self, current_time: float):
        """内部函数：清理超出窗口时间的数据"""
        threshold = current_time - self.window_size
        while self._history and self._history[0][0] < threshold:
            self._history.popleft()

    def get_prometheus_throughput(self) -> Tuple[float, float]:
        """
        [对外接口] 计算滑动窗口内的平均吞吐量，分别返回 Prompt 和 Generation 的速率
        :return: (prompt_tokens/s, generation_tokens/s)
        """
        current_time = time.time()
        
        with self._lock:
            # 1. 再次清理以确保数据新鲜度
            self._cleanup_history(current_time)
            
            # 2. 数据点不足无法计算
            if len(self._history) < 2:
                return 0.0, 0.0
            
            # 3. 取首尾数据点
            # 历史数据结构: (timestamp, prompt_accum, generation_accum)
            start_time, start_p, start_g = self._history[0]
            end_time, end_p, end_g = self._history[-1]
            
            # 4. 计算吞吐量
            delta_p = end_p - start_p
            delta_g = end_g - start_g
            delta_time = end_time - start_time
            
            if delta_time <= 0:
                return 0.0, 0.0
            
            return delta_p / delta_time, delta_g / delta_time

if __name__ == '__main__':
    # 测试代码
    VLLM_HOST = "172.18.0.168:8001" 
    
    monitor = ThroughputMonitor(host=VLLM_HOST, interval=0.1, window_size=2.0)
    monitor.start()
    
    try:
        while True:
            # 获取分别的吞吐量
            p_tps, g_tps = monitor.get_prometheus_throughput()
            # 打印当前时间以便观察平滑效果
            print(f"[{time.strftime('%H:%M:%S')}] Prompt: {p_tps:.2f} tok/s | Gen: {g_tps:.2f} tok/s | Total: {p_tps+g_tps:.2f} tok/s")
            time.sleep(0.5)
    except KeyboardInterrupt:
        monitor.stop()