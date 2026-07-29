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
DEFAULT_DECODE = CONFIG_INSTANCE_D_ADDRS

# 服务配置
# DEFAULT_PORT = 8082
# DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = CONFIG_GATEWAY_D_PORT
DEFAULT_HOST = CONFIG_GATEWAY_D_IP

# 轮询任务时间
LOOP_TASK_TIME = 10  # 10s

# 上级调度器
# DEFAULT_CONTROLLER = "127.0.0.1:8000"
DEFAULT_CONTROLLER = f"{CONFIG_CONTROLLER_IP}:{CONFIG_CONTROLLER_PORT}" if CONFIG_CONTROLLER_IP else ""

# 同步设置 是否启用同步
USE_SYN = False
SYN_BATCH_SIZE = 1
SYN_TIMEOUT = 1
DECODE_SCHEDULER_STRATEGY = "slo_aware_decode"
# round_robin
# lowest_generation_throughput
# slo_aware_decode

# TPOT predictor warmup configuration
TPOT_WARMUP_ENABLED = True
TPOT_WARMUP_REPEATS = 2
TPOT_TARGET = CONFIG_INSTANCE_D_ADDRS[0] if CONFIG_INSTANCE_D_ADDRS else None
TPOT_MODEL_ID = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
TPOT_TOKENIZER_PATH = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
TPOT_MODEL_FILE = "profile/TPOT_predictor/artifacts/decode_tpot_regressor.pkl"
# 同步设置 批处理批次
SYN_BATCH_SIZE = 1
# 同步设置 等待时间 秒
SYN_TIMEOUT = 1
