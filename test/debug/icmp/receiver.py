#!/usr/bin/env python3
"""
网络层数据包接收器 - 跨平台版本
可在Windows和Linux系统上运行
"""

import time
import socket
import struct
import sys
import platform
from typing import Dict, List, Tuple
import statistics


class PacketReceiver:
    def __init__(self, listen_ip: str = "0.0.0.0"):
        self.listen_ip = listen_ip
        self.received_packets: Dict[
            int, Tuple[float, float, int]] = {}  # {sequence: (send_time, receive_time, total_count)}

        # 创建原始套接字来接收IP数据包
        try:
            if platform.system().lower() == 'windows':
                # Windows
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                # 绑定到指定IP
                self.sock.bind((listen_ip, 0))
                # 启用接收所有数据包
                self.sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
            else:
                # Linux - 接收自定义协议号253的数据包
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, 253)
        except PermissionError:
            print("错误: 需要管理员/root权限来创建原始套接字")
            sys.exit(1)

    def parse_ip_packet(self, data: bytes) -> tuple:
        """解析IP数据包"""
        # 解析IP头部 (前20字节)
        ip_header = data[:20]
        iph = struct.unpack('!BBHHHBBH4s4s', ip_header)

        version_ihl = iph[0]
        protocol = iph[6]
        src_ip = socket.inet_ntoa(iph[8])
        dst_ip = socket.inet_ntoa(iph[9])

        # IP头部长度
        ihl = (version_ihl & 0xF) * 4

        # 数据部分
        payload = data[ihl:]

        return src_ip, dst_ip, protocol, payload

    def parse_payload(self, payload: bytes) -> tuple:
        """解析负载数据：序列号(4字节) + 总包数(4字节) + 发送时间戳(8字节)"""
        if len(payload) < 16:
            return None, None, None

        try:
            sequence, total_count, send_time = struct.unpack('!IId', payload[:16])
            return sequence, total_count, send_time
        except struct.error:
            return None, None, None

    def receive_packets(self, timeout: int = 30):
        """接收数据包"""
        print(f"开始监听数据包，协议类型: 253，超时时间: {timeout}秒...")
        print("等待接收数据包...")

        self.sock.settimeout(1)  # 设置较小的超时以便可以检查总超时
        start_time = time.time()
        packet_count_info = None

        try:
            while time.time() - start_time < timeout:
                try:
                    data, addr = self.sock.recvfrom(65535)

                    # 解析IP数据包
                    src_ip, dst_ip, protocol, payload = self.parse_ip_packet(data)

                    # 只处理我们自定义协议的数据包
                    if protocol == 253:
                        # 解析负载
                        sequence, total_count, send_time = self.parse_payload(payload)

                        if sequence is not None and send_time is not None and total_count is not None:
                            receive_time = time.time()

                            # 记录数据包信息
                            self.received_packets[sequence] = (send_time, receive_time, total_count)

                            # 保存总包数信息（从第一个有效包获取）
                            if packet_count_info is None:
                                packet_count_info = total_count

                            latency = (receive_time - send_time) * 1000  # 转换为毫秒
                            print(f"收到数据包 #{sequence}, 源IP: {src_ip}, 延迟: {latency:.3f}ms")

                            # 如果已经收到所有包，可以提前结束
                            if packet_count_info and len(self.received_packets) >= packet_count_info:
                                print("已收到所有预期数据包")
                                break

                except socket.timeout:
                    # 检查总超时
                    continue
                except Exception as e:
                    print(f"接收数据包时出错: {e}")
                    continue

        except KeyboardInterrupt:
            print("\n接收被用户中断")

        finally:
            # 在Windows上需要关闭混杂模式
            if platform.system().lower() == 'windows':
                try:
                    self.sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
                except:
                    pass

    def calculate_statistics(self):
        """计算传输统计信息"""
        if not self.received_packets:
            print("没有收到任何数据包")
            return

        # 从接收到的数据包中获取总包数（使用第一个包的信息）
        first_packet = next(iter(self.received_packets.values()))
        total_packets_sent = first_packet[2]  # total_count

        # 计算延迟
        latencies = []
        lost_packets = 0

        for seq in range(total_packets_sent):
            if seq in self.received_packets:
                send_time, receive_time, _ = self.received_packets[seq]
                latency = (receive_time - send_time) * 1000  # 毫秒
                latencies.append(latency)
            else:
                lost_packets += 1

        # 输出统计结果
        print("\n" + "=" * 60)
        print("网络传输统计结果:")
        print("=" * 60)
        print(f"发送数据包总数: {total_packets_sent}")
        print(f"接收数据包总数: {len(self.received_packets)}")
        print(f"丢包数: {lost_packets}")
        print(f"丢包率: {lost_packets / total_packets_sent * 100:.2f}%")

        if latencies:
            avg_latency = statistics.mean(latencies)
            min_latency = min(latencies)
            max_latency = max(latencies)

            # 计算标准差（如果有足够的数据点）
            if len(latencies) > 1:
                std_latency = statistics.stdev(latencies)
            else:
                std_latency = 0

            print(f"平均传输时间: {avg_latency:.3f}ms")
            print(f"最小传输时间: {min_latency:.3f}ms")
            print(f"最大传输时间: {max_latency:.3f}ms")
            print(f"传输时间标准差: {std_latency:.3f}ms")
            print(f"传输时间统计基于: {len(latencies)} 个样本")

            # 显示延迟分布（如果数据足够）
            if len(latencies) >= 5:
                latencies_sorted = sorted(latencies)
                n = len(latencies_sorted)
                p50 = latencies_sorted[n // 2]
                p95 = latencies_sorted[int(0.95 * n)]
                p99 = latencies_sorted[int(0.99 * n)] if n >= 100 else latencies_sorted[-1]

                print(f"延迟中位数 (P50): {p50:.3f}ms")
                print(f"95%分位数 (P95): {p95:.3f}ms")
                if n >= 100:
                    print(f"99%分位数 (P99): {p99:.3f}ms")
        else:
            print("没有成功接收任何数据包，无法计算传输时间")


def main():
    if len(sys.argv) > 1:
        listen_ip = sys.argv[1]
    else:
        listen_ip = "0.0.0.0"

    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    print("网络层数据包接收器")
    print("=" * 40)
    print(f"监听地址: {listen_ip}")
    print(f"协议类型: 253")
    print(f"超时时间: {timeout}秒")
    print("=" * 40)

    # 检查权限
    if platform.system().lower() != 'windows' and not hasattr(socket, 'SOCK_RAW'):
        print("错误: 当前Python环境不支持原始套接字")
        sys.exit(1)

    receiver = PacketReceiver(listen_ip)
    receiver.receive_packets(timeout)
    receiver.calculate_statistics()


if __name__ == "__main__":
    main()