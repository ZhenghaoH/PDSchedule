"""
Realtime network status entrypoint.

Examples:
    python entrypoints/network_status.py
    python entrypoints/network_status.py --ip 172.18.0.170
    python entrypoints/network_status.py --interface eth0
"""

import argparse
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Show realtime network connection status."
    )
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--ip",
        type=str,
        help="Local bind IP. The script resolves the related network interface.",
    )
    target_group.add_argument(
        "--interface",
        type=str,
        help="Network interface name, such as eth0, ens33, or WLAN.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Sampling interval in seconds. Default: 1.0.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of top active interfaces to show in global mode. Default: 5.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Number of samples before exit. Default: 0 means run until Ctrl+C.",
    )
    return parser.parse_args()


def _load_network_metric():
    try:
        from utils import network_metric
    except ModuleNotFoundError as exc:
        if exc.name == "psutil":
            print(
                "Missing dependency: psutil. Please install project requirements first.",
                file=sys.stderr,
            )
            return None
        raise
    return network_metric


def _validate_common_args(interval, top_n, count):
    if interval <= 0:
        raise ValueError("--interval must be greater than 0")
    if top_n <= 0:
        raise ValueError("--top-n must be greater than 0")
    if count < 0:
        raise ValueError("--count must be greater than or equal to 0")


def _should_continue(sample_index, count):
    return count == 0 or sample_index < count


def _print_global_snapshot(interval, top_n):
    network_metric = _load_network_metric()
    if network_metric is None:
        return False

    current_time = time.strftime("%H:%M:%S")
    global_metrics = network_metric.get_global_metrics(interval=interval)
    top_interfaces = network_metric.get_top_interfaces(
        interval=max(0.05, min(interval, 1.0)),
        top_n=top_n,
    )

    print(
        f"{current_time} | "
        f"GLOBAL TX={global_metrics['global_tx_mbps']:.2f} Mbps | "
        f"RX={global_metrics['global_rx_mbps']:.2f} Mbps | "
        f"TOTAL={global_metrics['global_total_mbps']:.2f} Mbps"
    )
    print("Top interfaces:")
    for idx, item in enumerate(top_interfaces, start=1):
        print(
            f"  {idx}. {item['interface']:<16} "
            f"TX={item['tx_mbps']:>8.2f} Mbps  "
            f"RX={item['rx_mbps']:>8.2f} Mbps  "
            f"TOTAL={item['total_mbps']:>8.2f} Mbps  "
            f"SPEED={item['speed_mbps']:>8} Mbps  "
            f"KIND={item['kind_hint']}"
        )
    print("-" * 120)
    return True


def monitor_global(interval, top_n, count):
    print("Monitoring global network status. Press Ctrl+C to stop.")
    print("All rate fields are Mbps.")
    print("-" * 120)

    sample_index = 0
    try:
        while _should_continue(sample_index, count):
            if not _print_global_snapshot(interval=interval, top_n=top_n):
                break
            sample_index += 1
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


def monitor_ip(ip_address, interval, count):
    network_metric = _load_network_metric()
    if network_metric is None:
        return

    if count == 0:
        network_metric.monitor_interface_metrics(ip_address, interval=interval)
        return

    print(f"Monitoring local IP: {ip_address}")
    print("All rate fields are Mbps.")
    print("-" * 160)
    for _ in range(count):
        data = network_metric.get_interface_metrics(ip_address, interval=interval)
        current_time = time.strftime("%H:%M:%S")
        if not data:
            print(f"{current_time} | failed to resolve interface for IP {ip_address}")
            continue
        print(
            f"{current_time} | "
            f"IF={data['interface']:<16} "
            f"TX={data['tx_mbps']:>8.2f} "
            f"RX={data['rx_mbps']:>8.2f} "
            f"TOTAL={data['total_mbps']:>8.2f} "
            f"GLOBAL_TOTAL={data['global_total_mbps']:>8.2f} "
            f"SPEED={data['interface_speed_mbps']:>8.2f} "
            f"AVAILABLE={data['available_mbps']:>8.2f} "
            f"USAGE={data['usage_percent']:>6.2f}%"
        )


def monitor_interface(interface_name, interval, count):
    network_metric = _load_network_metric()
    if network_metric is None:
        return

    if count == 0:
        network_metric.monitor_passive_bandwidth(interface_name, interval=interval)
        return

    import psutil

    per_nic = psutil.net_io_counters(pernic=True)
    if interface_name not in per_nic:
        print(f"Error: interface '{interface_name}' not found.")
        return

    max_bandwidths = network_metric.get_max_bandwidth()
    max_bw = max_bandwidths.get(interface_name, 0)
    old_stats = per_nic[interface_name]

    print(f"Monitoring interface: {interface_name}")
    print("All rate fields are Mbps.")
    print("-" * 120)
    for _ in range(count):
        time.sleep(interval)
        new_stats = psutil.net_io_counters(pernic=True)[interface_name]
        tx_mbps = ((new_stats.bytes_sent - old_stats.bytes_sent) * 8) / (1000000 * interval)
        rx_mbps = ((new_stats.bytes_recv - old_stats.bytes_recv) * 8) / (1000000 * interval)
        total_mbps = tx_mbps + rx_mbps
        available_mbps = max(0, max_bw - total_mbps)
        usage_percent = (total_mbps / max_bw * 100) if max_bw > 0 else 0.0
        current_time = time.strftime("%H:%M:%S")

        print(
            f"{current_time} | "
            f"TX={tx_mbps:>8.2f} Mbps  "
            f"RX={rx_mbps:>8.2f} Mbps  "
            f"TOTAL={total_mbps:>8.2f} Mbps  "
            f"SPEED={max_bw:>8.2f} Mbps  "
            f"AVAILABLE={available_mbps:>8.2f} Mbps  "
            f"USAGE={usage_percent:>6.2f}%"
        )
        old_stats = new_stats


def main():
    args = parse_args()
    try:
        _validate_common_args(args.interval, args.top_n, args.count)
    except ValueError as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        return 2

    if args.ip:
        monitor_ip(args.ip, interval=args.interval, count=args.count)
    elif args.interface:
        monitor_interface(args.interface, interval=args.interval, count=args.count)
    else:
        monitor_global(interval=args.interval, top_n=args.top_n, count=args.count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
