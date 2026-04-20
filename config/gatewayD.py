# config.py
"""
配置模块：定义所有默认参数和常量
- 仅包含常量定义，不包含任何逻辑
- 所有默认值集中管理
- 为生产环境提供清晰的配置参考
"""
from config.ADDRESS_CONFIG import *
# 身份 prefill=0 decode=1
RANK = 1

# 默认代理配置
# DEFAULT_DECODE = ["127.0.0.1:7002"]
DEFAULT_DECODE = [f"{CONFIG_INSTANCE_D_IP}:{CONFIG_INSTANCE_D_PORT}"]

# 服务配置
# DEFAULT_PORT = 8082
# DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = CONFIG_GATEWAY_D_PORT
DEFAULT_HOST = CONFIG_GATEWAY_D_IP

# 轮询任务时间
LOOP_TASK_TIME = 10  # 10s

# 上级调度器
# DEFAULT_CONTROLLER = "127.0.0.1:8000"
DEFAULT_CONTROLLER = f"{CONFIG_CONTROLLER_IP}:{CONFIG_CONTROLLER_PORT}"

# 同步设置 是否启用同步
USE_SYN = True
# 同步设置 批处理批次
SYN_BATCH_SIZE = 1
# 同步设置 等待时间 秒
SYN_TIMEOUT = 1
