import os
import sys
import time


DEFAULT_PCIE_BANDWIDTH = 64000.0
DEFAULT_PCIE_RTT = 0.1
DEFAULT_GPU_INDEX = 0
DEFAULT_SAMPLE_COUNT = 1
DEFAULT_SAMPLE_INTERVAL = 0.0

try:
    import pynvml  # Provided by the official nvidia-ml-py package.
except Exception:
    pynvml = None


def _to_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _decode_if_bytes(value):
    return value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else value


def _clamp_non_negative(value):
    return value if value > 0 else 0.0


def _sample_nvml_pcie_throughput(handle, sample_count, sample_interval):
    """
    读取 PCIe 吞吐并换算为 Mbps。
    NVML 返回值近似为 KB/s，这里统一换算为 Mbps。
    """
    tx_samples = []
    rx_samples = []

    for idx in range(sample_count):
        tx_kbps = float(
            pynvml.nvmlDeviceGetPcieThroughput(handle, pynvml.NVML_PCIE_UTIL_TX_BYTES)
        )
        rx_kbps = float(
            pynvml.nvmlDeviceGetPcieThroughput(handle, pynvml.NVML_PCIE_UTIL_RX_BYTES)
        )
        tx_samples.append(tx_kbps)
        rx_samples.append(rx_kbps)

        if idx < sample_count - 1 and sample_interval > 0:
            time.sleep(sample_interval)

    avg_tx_kbps = sum(tx_samples) / len(tx_samples) if tx_samples else 0.0
    avg_rx_kbps = sum(rx_samples) / len(rx_samples) if rx_samples else 0.0
    observed_tx_mbps = avg_tx_kbps * 8 / 1000.0
    observed_rx_mbps = avg_rx_kbps * 8 / 1000.0

    return avg_tx_kbps, avg_rx_kbps, observed_tx_mbps, observed_rx_mbps


def _get_config():
    return {
        "gpu_index": _to_int(os.getenv("MY_PCIE_GPU_INDEX"), DEFAULT_GPU_INDEX),
        "rtt": _to_float(os.getenv("MY_PCIE_RTT"), DEFAULT_PCIE_RTT),
        "sample_count": max(1, _to_int(os.getenv("MY_PCIE_SAMPLE_COUNT"), DEFAULT_SAMPLE_COUNT)),
        "sample_interval": max(0.0, _to_float(os.getenv("MY_PCIE_SAMPLE_INTERVAL"), DEFAULT_SAMPLE_INTERVAL)),
        "bandwidth_floor": _to_float(os.getenv("MY_PCIE_BANDWIDTH_FLOOR"), 0.0),
    }


def _build_metrics(
    *,
    config,
    source,
    tx_kbps,
    rx_kbps,
    observed_tx_mbps,
    observed_rx_mbps,
    current_link_speed_mbps,
    max_link_speed_mbps,
    max_bandwidth_mbps,
    device_name,
    pci_bus_id,
):
    used_bandwidth_mbps = max(observed_tx_mbps, observed_rx_mbps)
    raw_available_bandwidth_mbps = _clamp_non_negative(max_bandwidth_mbps - used_bandwidth_mbps)
    bandwidth = raw_available_bandwidth_mbps

    return {
        "bandwidth": bandwidth,
        "rtt": config["rtt"],
        "tx_kbps": tx_kbps,
        "rx_kbps": rx_kbps,
        "observed_tx_mbps": observed_tx_mbps,
        "observed_rx_mbps": observed_rx_mbps,
        "observed_mbps": observed_tx_mbps + observed_rx_mbps,
        "used_bandwidth_mbps": used_bandwidth_mbps,
        "current_link_speed_mbps": current_link_speed_mbps,
        "max_link_speed_mbps": max_link_speed_mbps,
        "max_bandwidth_mbps": max_bandwidth_mbps,
        "raw_available_bandwidth_mbps": raw_available_bandwidth_mbps,
        "smoothed_available_bandwidth_mbps": raw_available_bandwidth_mbps,
        "bandwidth_floor_mbps": config["bandwidth_floor"],
        "ema_alpha": None,
        "safety_factor": None,
        "sample_count": config["sample_count"],
        "sample_interval": config["sample_interval"],
        "device_name": device_name,
        "gpu_index": config["gpu_index"],
        "pci_bus_id": pci_bus_id,
        "source": source,
    }


def _get_nvml_pcie_metrics():
    """
    通过 NVML 实时获取 PCIe 指标。

    即时上报策略：
    - 最大带宽基线优先使用探测值
    - 当前占用带宽使用 max(TX, RX)
    - 本次采集得到的原始剩余可用带宽直接作为最终上报带宽
    """
    if pynvml is None:
        return None

    config = _get_config()

    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(config["gpu_index"])

        device_name = _decode_if_bytes(pynvml.nvmlDeviceGetName(handle))
        pci_info = pynvml.nvmlDeviceGetPciInfo(handle)
        pci_bus_id = _decode_if_bytes(getattr(pci_info, "busId", "unknown"))

        tx_kbps, rx_kbps, observed_tx_mbps, observed_rx_mbps = _sample_nvml_pcie_throughput(
            handle,
            config["sample_count"],
            config["sample_interval"],
        )

        current_link_speed_mbps = 0.0
        max_link_speed_mbps = 0.0

        try:
            current_link_speed_mbps = float(pynvml.nvmlDeviceGetPcieSpeed(handle))
        except Exception:
            current_link_speed_mbps = 0.0

        try:
            max_link_speed_mbps = float(pynvml.nvmlDeviceGetPcieLinkMaxSpeed(handle))
        except Exception:
            max_link_speed_mbps = 0.0

        max_bandwidth_mbps = (
            current_link_speed_mbps
            or max_link_speed_mbps
            or DEFAULT_PCIE_BANDWIDTH
        )

        return _build_metrics(
            config=config,
            source="nvml",
            tx_kbps=tx_kbps,
            rx_kbps=rx_kbps,
            observed_tx_mbps=observed_tx_mbps,
            observed_rx_mbps=observed_rx_mbps,
            current_link_speed_mbps=current_link_speed_mbps,
            max_link_speed_mbps=max_link_speed_mbps,
            max_bandwidth_mbps=max_bandwidth_mbps,
            device_name=device_name,
            pci_bus_id=pci_bus_id,
        )
    except Exception as exc:
        fallback_max_bandwidth = max(DEFAULT_PCIE_BANDWIDTH, config["bandwidth_floor"])
        return _build_metrics(
            config=config,
            source=f"fallback_after_nvml_error:{type(exc).__name__}",
            tx_kbps=0.0,
            rx_kbps=0.0,
            observed_tx_mbps=0.0,
            observed_rx_mbps=0.0,
            current_link_speed_mbps=0.0,
            max_link_speed_mbps=0.0,
            max_bandwidth_mbps=fallback_max_bandwidth,
            device_name="unknown",
            pci_bus_id="unknown",
        )
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def _get_fallback_metrics():
    config = _get_config()
    fallback_max_bandwidth = max(DEFAULT_PCIE_BANDWIDTH, config["bandwidth_floor"])
    return _build_metrics(
        config=config,
        source="env_default",
        tx_kbps=0.0,
        rx_kbps=0.0,
        observed_tx_mbps=0.0,
        observed_rx_mbps=0.0,
        current_link_speed_mbps=0.0,
        max_link_speed_mbps=0.0,
        max_bandwidth_mbps=fallback_max_bandwidth,
        device_name="unknown",
        pci_bus_id="unknown",
    )


def get_pcie_metrics():
    """
    获取本机 PCIe 链路指标。

    优先尝试通过 NVML 实时探测；
    若当前环境没有 NVML Python 绑定或探测失败，再回退到环境变量/默认值。
    """
    metrics = _get_nvml_pcie_metrics()
    if metrics is not None:
        return metrics
    return _get_fallback_metrics()


def monitor_pcie_metrics(interval=1):
    """单独运行时持续输出 PCIe 指标，使用单行刷新避免刷屏。"""
    print("正在持续监控 PCIe 指标 (按 Ctrl+C 停止)")
    print("-" * 190)
    print(
        f"{'时间':<10} | {'GPU':<4} | {'设备':<18} | {'TX(Mbps)':<10} | {'RX(Mbps)':<10} | "
        f"{'占用(Mbps)':<10} | {'带宽基线(Mbps)':<16} | {'原始剩余(Mbps)':<16} | "
        f"{'上报带宽(Mbps)':<16} | {'来源':<18}"
    )
    print("-" * 190)

    try:
        while True:
            data = get_pcie_metrics()
            current_time = time.strftime("%H:%M:%S")
            device_name = str(data.get("device_name", "unknown"))[:18]
            source = str(data.get("source", "unknown"))[:18]
            output = (
                f"\r{current_time:<10} | "
                f"{data.get('gpu_index', 0):<4} | "
                f"{device_name:<18} | "
                f"{data.get('observed_tx_mbps', 0.0):>8.2f}   | "
                f"{data.get('observed_rx_mbps', 0.0):>8.2f}   | "
                f"{data.get('used_bandwidth_mbps', 0.0):>8.2f}   | "
                f"{data.get('max_bandwidth_mbps', 0.0):>14.2f}   | "
                f"{data.get('raw_available_bandwidth_mbps', 0.0):>14.2f}   | "
                f"{data.get('bandwidth', 0.0):>14.2f}   | "
                f"{source:<18}"
            )
            sys.stdout.write(output)
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\n监控已停止。")


if __name__ == "__main__":
    monitor_pcie_metrics(interval=1)
