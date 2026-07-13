# config.py
"""
配置模块：定义所有默认参数和常量
- 仅包含常量定义，不包含任何逻辑
- 所有默认值集中管理
- 为生产环境提供清晰的配置参考
"""
import os

from config.ADDRESS_CONFIG import *
# 身份 prefill=0 decode=1
RANK = 1

# 服务配置
# DEFAULT_PORT = 7002
# DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = None
DEFAULT_HOST = os.getenv("FLEXPD_INSTANCE_HOST", "")

# 所代理的vLLM的端口号（instance代理和vLLM实例在一起，理论上IP相同）
# VLLM_PORT = 8002
VLLM_PORT = None

# 轮询上报任务时间
LOOP_TASK_TIME = 10  # 10s

# 上级网关
# DEFAULT_GATEWAY = "127.0.0.1:8082"
DEFAULT_GATEWAY = os.getenv(
    "FLEXPD_GATEWAY_ADDR",
    f"{CONFIG_GATEWAY_D_IP}:{CONFIG_GATEWAY_D_PORT}" if CONFIG_GATEWAY_D_IP else "",
)

# 使用模拟数据
USE_MOCK = False

# RDMA配置 协议  可选 "tcp" or "rdma"
MOONCAKE_PROTOCOL = "tcp"
# RDMA配置 设备名 可选 "" or "mlx5_0"
MOONCAKE_DEVICE_NAME = ""

# VLLM PD分离插件
KV_CONNECTOR = "lmcache"  # mooncake or lmcache
