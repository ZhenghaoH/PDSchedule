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
RANK = 0

# 默认代理配置
# DEFAULT_PREFILL = ["127.0.0.1:7001"]
# DEFAULT_DECODE = ["127.0.0.1:8082"]
DEFAULT_PREFILL = CONFIG_INSTANCE_P_ADDRS
DEFAULT_DECODE = []

# 服务配置
# DEFAULT_PORT = 8081
# DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = CONFIG_GATEWAY_P_PORT
DEFAULT_HOST = CONFIG_GATEWAY_P_IP

# 轮询上报任务时间
LOOP_TASK_TIME = 10  # 10s

# 上级调度器
# DEFAULT_CONTROLLER = "127.0.0.1:8000"
DEFAULT_CONTROLLER = f"{CONFIG_CONTROLLER_IP}:{CONFIG_CONTROLLER_PORT}" if CONFIG_CONTROLLER_IP else ""

# 同步设置 是否启用同步
USE_SYN = True

SYN_BATCH_SIZE = 30  # 同步设置 批处理批次, 超过该批次，进行调度
SYN_TIMEOUT = 15  # 同步设置 等待时间 秒， 超过该时间，进行调度

# 同步调度触发逻辑配置
TRIGGER_MODE = "ARRIVAL"  # "ARRIVAL" or "BATCH" or "TIMEOUT"
PROFILE_MODE = False
SCHEDULER_STRATEGY = "slo_aware_prefill"
ENABLE_ADAPTIVE_SWITCH = False

# TTFT predictor warmup configuration
TTFT_WARMUP_ENABLED = True
TTFT_WARMUP_REPEATS = 3
TTFT_TARGET = CONFIG_INSTANCE_P_ADDRS[0] if CONFIG_INSTANCE_P_ADDRS else None
TTFT_MODEL_ID = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
TTFT_TOKENIZER_PATH = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
TTFT_MODEL_FILE = "profile/TTFT_predictor/artifacts/prefill_ttft_regressor.pkl"
