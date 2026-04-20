#!/usr/bin/env python3
"""
网络层数据包发送器 - 跨平台版本
可在Windows和Linux系统上运行
"""

import time
import socket
import struct
import sys
import platform
from typing import List, Tuple


class PacketSender:
    def __init__(self, target_ip: str, packet_count: int = 10, packet_size: int = 64):
        self.target_ip = target_ip
        self.packet_count = packet_count
        self.packet_size = packet_size
        self.timestamps: List[Tuple[int, float]] = []  # [(sequence, send_time)]

        # 创建原始套接字
        try:
            if platform.system().lower() == 'windows':
                # Windows需要使用SOCK_RAW并指定IPPROTO_IP
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                # 设置IP头包含选项
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
            else:
                # Linux使用SOCK_RAW创建IP数据包
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        except PermissionError:
            print("错误: 需要管理员/root权限来创建原始套接字")
            sys.exit(1)

    def create_ip_packet(self, sequence: int, total_count: int, send_time: float) -> bytes:
        """创建IP数据包，在负载中包含序列号、总包数和发送时间戳"""
        # IP头部字段
        ip_ver_ihl = 0x45  # IPv4, 头部长度5*4=20字节
        ip_tos = 0  # 服务类型
        ip_total_len = 20 + self.packet_size  # IP头 + 数据
        ip_id = sequence  # 标识符
        ip_flags_frag = 0  # 标志和分片偏移
        ip_ttl = 64  # 生存时间
        ip_proto = 253  # 自定义协议号(253-254为测试用)
        ip_check = 0  # 校验和(简化处理)

        # 源IP和目标IP
        src_ip = socket.inet_aton(socket.gethostbyname(socket.gethostname()))
        dst_ip = socket.inet_aton(self.target_ip)

        # 构建IP头部
        ip_header = struct.pack('!BBHHHBBH4s4s',
                                ip_ver_ihl, ip_tos, ip_total_len,
                                ip_id, ip_flags_frag, ip_ttl,
                                ip_proto, ip_check, src_ip, dst_ip)

        # 构建负载数据：序列号(4字节) + 总包数(4字节) + 发送时间戳(8字节) + 填充数据
        payload = struct.pack('!IId', sequence, total_count, send_time)
        payload += b'X' * (self.packet_size - len(payload))  # 填充到指定大小

        return ip_header + payload

    def send_packets(self):
        """发送指定数量的数据包"""
        print(f"开始向 {self.target_ip} 发送 {self.packet_count} 个数据包...")
        print(f"数据包大小: {self.packet_size} 字节")
        print(f"协议类型: 253 (自定义)")

        for i in range(self.packet_count):
            # 记录发送时间
            send_time = time.time()
            self.timestamps.append((i, send_time))

            # 创建IP数据包
            packet = self.create_ip_packet(i, self.packet_count, send_time)

            try:
                # 发送数据包
                self.sock.sendto(packet, (self.target_ip, 0))
                print(f"发送数据包 {i + 1}/{self.packet_count}, 大小: {len(packet)} 字节")

                # 短暂延迟以避免拥塞
                time.sleep(0.1)

            except Exception as e:
                print(f"发送数据包 {i + 1} 时出错: {e}")

        print("所有数据包发送完成")
        print(f"发送统计: 成功发送 {len(self.timestamps)}/{self.packet_count} 个数据包")


def main():
    if len(sys.argv) < 2:
        print("用法: python sender.py <目标IP> [包数量] [包大小]")
        print("示例: python sender.py 192.168.1.100 10 64")
        print("注意: 需要管理员/root权限运行")
        sys.exit(1)

    target_ip = sys.argv[1]
    packet_count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    packet_size = int(sys.argv[3]) if len(sys.argv) > 3 else 64

    # 最小包大小检查
    if packet_size < 16:
        print("警告: 包大小至少需要16字节来存储时间戳信息")
        packet_size = 16

    # 检查权限
    if platform.system().lower() != 'windows' and not hasattr(socket, 'SOCK_RAW'):
        print("错误: 当前Python环境不支持原始套接字")
        sys.exit(1)

    sender = PacketSender(target_ip, packet_count, packet_size)
    sender.send_packets()


if __name__ == "__main__":
    main()