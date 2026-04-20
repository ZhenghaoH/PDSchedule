import psutil
import time
import sys
import socket


def get_interface_from_ip(target_ip):
    """
    [辅助函数] 根据 IP 地址反查对应的网卡名称
    """
    interfaces = psutil.net_if_addrs()

    for iface_name, addrs in interfaces.items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address == target_ip:
                return iface_name
    return None


def _calc_mbps(byte_delta, interval):
    return (byte_delta * 8) / (1000000 * interval)


def _classify_interface(interface_name):
    name = interface_name.lower()
    if name == "lo":
        return "loopback"
    if name == "docker0":
        return "docker"
    if name.startswith("br-"):
        return "bridge"
    if name.startswith("veth"):
        return "veth"
    if name.startswith(("eno", "ens", "eth", "enp")):
        return "physical"
    return "unknown"


def _sample_all_interfaces(interval):
    old_per_nic = psutil.net_io_counters(pernic=True)
    time.sleep(interval)
    new_per_nic = psutil.net_io_counters(pernic=True)
    max_bandwidths = get_max_bandwidth()

    all_metrics = []
    for interface_name, old_stats in old_per_nic.items():
        if interface_name not in new_per_nic:
            continue
        new_stats = new_per_nic[interface_name]
        tx_mbps = _calc_mbps(new_stats.bytes_sent - old_stats.bytes_sent, interval)
        rx_mbps = _calc_mbps(new_stats.bytes_recv - old_stats.bytes_recv, interval)
        total_mbps = tx_mbps + rx_mbps
        all_metrics.append({
            "interface": interface_name,
            "tx_mbps": tx_mbps,
            "rx_mbps": rx_mbps,
            "total_mbps": total_mbps,
            "speed_mbps": max_bandwidths.get(interface_name, 0),
            "kind_hint": _classify_interface(interface_name),
        })
    return all_metrics


def _sample_global_metrics(interval):
    old = psutil.net_io_counters()
    time.sleep(interval)
    new = psutil.net_io_counters()

    tx_mbps = _calc_mbps(new.bytes_sent - old.bytes_sent, interval)
    rx_mbps = _calc_mbps(new.bytes_recv - old.bytes_recv, interval)
    total_mbps = tx_mbps + rx_mbps

    return {
        "global_tx_mbps": tx_mbps,
        "global_rx_mbps": rx_mbps,
        "global_total_mbps": total_mbps,
    }


def get_bandwidth_usage(interval=1):
    """获取全局（所有网卡）指定时间间隔内的带宽使用情况"""
    metrics = _sample_global_metrics(interval)
    return metrics["global_tx_mbps"], metrics["global_rx_mbps"]


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
    total_used = current_upload + current_download
    total_available = max(0, max_bandwidth - total_used)
    return total_available


def get_global_metrics(interval=0.05):
    """
    [调试接口] 获取全局（所有网卡汇总）带宽指标。
    返回速率单位统一为 Mbps。
    """
    metrics = _sample_global_metrics(interval)
    return {
        "global_tx_mbps": round(metrics["global_tx_mbps"], 4),
        "global_rx_mbps": round(metrics["global_rx_mbps"], 4),
        "global_total_mbps": round(metrics["global_total_mbps"], 4),
        "unit_hint": "All rate fields are Mbps",
    }


def get_top_interfaces(interval=0.05, top_n=5):
    """
    [调试接口] 返回当前采样窗口内流量最高的前 N 个接口。
    """
    metrics = _sample_all_interfaces(interval)
    metrics.sort(key=lambda item: item["total_mbps"], reverse=True)
    top_metrics = metrics[:max(1, int(top_n))]

    return [
        {
            "interface": item["interface"],
            "tx_mbps": round(item["tx_mbps"], 4),
            "rx_mbps": round(item["rx_mbps"], 4),
            "total_mbps": round(item["total_mbps"], 4),
            "speed_mbps": item["speed_mbps"],
            "kind_hint": item["kind_hint"],
        }
        for item in top_metrics
    ]


# ==========================================
#  修改后的对外接口函数
# ==========================================
def get_interface_metrics(ip_address, interval=0.05):
    """
    [对外接口] 根据 IP 地址获取对应网卡的当前带宽指标。

    Args:
        ip_address (str): 本机的 IP 地址 (如 '192.168.1.5')
        interval (float): 采样时间间隔，单位秒。

    Returns:
        dict: 包含详细指标的字典，如果找不到对应的 IP 则返回 None。
    """
    interface_name = get_interface_from_ip(ip_address)

    if not interface_name:
        print(f"[Error] No network interface found with IP: {ip_address}")
        return None

    per_nic_counts = psutil.net_io_counters(pernic=True)
    if interface_name not in per_nic_counts:
        print(f"[Error] Statistics not available for interface: {interface_name}")
        return None

    max_bandwidths = get_max_bandwidth()
    max_bw = max_bandwidths.get(interface_name, 0)

    old_interface_stats = per_nic_counts[interface_name]
    old_global_stats = psutil.net_io_counters()

    time.sleep(interval)

    new_interface_stats = psutil.net_io_counters(pernic=True)[interface_name]
    new_global_stats = psutil.net_io_counters()

    interface_bytes_sent = new_interface_stats.bytes_sent - old_interface_stats.bytes_sent
    interface_bytes_recv = new_interface_stats.bytes_recv - old_interface_stats.bytes_recv

    tx_mbps = _calc_mbps(interface_bytes_sent, interval)
    rx_mbps = _calc_mbps(interface_bytes_recv, interval)
    total_used = tx_mbps + rx_mbps

    global_tx_mbps = _calc_mbps(new_global_stats.bytes_sent - old_global_stats.bytes_sent, interval)
    global_rx_mbps = _calc_mbps(new_global_stats.bytes_recv - old_global_stats.bytes_recv, interval)
    global_total_mbps = global_tx_mbps + global_rx_mbps

    available_bw = calculate_available_bandwidth(tx_mbps, rx_mbps, max_bw)
    usage_pct = (total_used / max_bw * 100) if max_bw > 0 else 0.0

    return {
        "interface": interface_name,
        "bind_ip": ip_address,
        "tx_mbps": round(tx_mbps, 4),
        "rx_mbps": round(rx_mbps, 4),
        "total_mbps": round(total_used, 4),
        "max_mbps": max_bw,
        "available_mbps": round(available_bw, 4),
        "usage_percent": round(usage_pct, 2),
        "interface_speed_mbps": max_bw,
        "global_tx_mbps": round(global_tx_mbps, 4),
        "global_rx_mbps": round(global_rx_mbps, 4),
        "global_total_mbps": round(global_total_mbps, 4),
        "unit_hint": "All rate fields are Mbps",
    }


def monitor_interface_metrics(ip_address, interval=1):
    """
    持续监控指定 IP 所绑定网卡的带宽指标。

    Args:
        ip_address (str): 本机绑定的 IP 地址
        interval (float): 采样间隔，单位秒
    """
    print(f"正在持续监控 IP: {ip_address} (按 Ctrl+C 停止)")
    print("说明: 本脚本所有速率单位均为 Mbps；若 Ubuntu 监视器显示 MB/s，请乘以 8 再对比。")
    print("-" * 190)
    print(
        f"{'时间':<10} | {'网卡':<16} | {'目标TX':<10} | {'目标RX':<10} | {'目标总':<10} | "
        f"{'全局TX':<10} | {'全局RX':<10} | {'全局总':<10} | {'理论速率':<10} | {'可用带宽':<10} | {'使用率':<8}"
    )
    print("-" * 190)

    refresh_count = 0

    try:
        while True:
            data = get_interface_metrics(ip_address, interval=interval)
            current_time = time.strftime("%H:%M:%S")

            if data:
                output = (
                    f"\r{current_time:<10} | "
                    f"{data['interface']:<16} | "
                    f"{data['tx_mbps']:>8.2f}   | "
                    f"{data['rx_mbps']:>8.2f}   | "
                    f"{data['total_mbps']:>8.2f}   | "
                    f"{data['global_tx_mbps']:>8.2f}   | "
                    f"{data['global_rx_mbps']:>8.2f}   | "
                    f"{data['global_total_mbps']:>8.2f}   | "
                    f"{data['interface_speed_mbps']:>8.2f}   | "
                    f"{data['available_mbps']:>8.2f}   | "
                    f"{data['usage_percent']:>6.2f}%"
                )
                sys.stdout.write(output)
                sys.stdout.flush()
            else:
                output = f"\r{current_time:<10} | 获取失败: 未找到 IP {ip_address} 对应的网卡或网卡不可用"
                sys.stdout.write(output)
                sys.stdout.flush()
                time.sleep(interval)

            refresh_count += 1
            if refresh_count % 10 == 0:
                top_interfaces = get_top_interfaces(interval=max(interval, 0.05), top_n=5)
                print("\n\nTop interfaces:")
                for idx, item in enumerate(top_interfaces, start=1):
                    print(
                        f"{idx}. {item['interface']:<12} "
                        f"TX={item['tx_mbps']:.2f} "
                        f"RX={item['rx_mbps']:.2f} "
                        f"TOTAL={item['total_mbps']:.2f} "
                        f"speed={item['speed_mbps']} "
                        f"kind={item['kind_hint']}"
                    )

    except KeyboardInterrupt:
        print("\n\n监控已停止。")


def monitor_passive_bandwidth(interface_name, interval=1):
    """(保留旧函数) 实时监控指定网卡的带宽使用情况"""
    if interface_name not in psutil.net_io_counters(pernic=True):
        print(f"错误: 找不到网口 '{interface_name}'")
        return

    max_bandwidths = get_max_bandwidth()
    max_bw = max_bandwidths.get(interface_name, 0)

    print(f"正在实时监控网口: {interface_name} (按 Ctrl+C 停止)")
    print("-" * 90)
    print(f"{'时间':<10} | {'发送(Mbps)':<12} | {'接收(Mbps)':<12} | {'总使用':<10} | {'总可用':<10} | {'使用率':<8}")
    print("-" * 90)

    old_stats = psutil.net_io_counters(pernic=True).get(interface_name)

    try:
        while True:
            time.sleep(interval)
            new_stats = psutil.net_io_counters(pernic=True)[interface_name]

            bytes_sent = new_stats.bytes_sent - old_stats.bytes_sent
            bytes_recv = new_stats.bytes_recv - old_stats.bytes_recv

            tx_mbps = _calc_mbps(bytes_sent, interval)
            rx_mbps = _calc_mbps(bytes_recv, interval)

            total_used = tx_mbps + rx_mbps
            available_bw = calculate_available_bandwidth(tx_mbps, rx_mbps, max_bw)
            usage_pct = (total_used / max_bw * 100) if max_bw > 0 else 0

            current_time = time.strftime("%H:%M:%S")
            output = f"\r{current_time:<10} | {tx_mbps:>10.2f}   | {rx_mbps:>10.2f}   | {total_used:>8.2f}   | {available_bw:>8.2f}   | {usage_pct:>6.1f}% "

            sys.stdout.write(output)
            sys.stdout.flush()

            old_stats = new_stats

    except KeyboardInterrupt:
        print("\n\n监控已停止。")


if __name__ == "__main__":
    # --- 测试区域 ---
    # 实际使用时替换为你要观测的本机 IP
    test_ip = "172.18.0.170"
    monitor_interface_metrics(test_ip, interval=0.1)
