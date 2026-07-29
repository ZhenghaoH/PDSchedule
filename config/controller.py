# config.py
import os
from pathlib import Path
from config.ADDRESS_CONFIG import *

# 默认模型型号，影响 token数 估算
DEFAULT_MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
DEFAULT_MODEL_SHORTNAME = "DeepSeek-R1-Distill-Qwen-1.5B"

# 网页模板路径
TEMPLATES_PATH = str(Path(__file__).resolve().parents[1] / "templates")

# 服务配置
# DEFAULT_PORT = 8000
# DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = CONFIG_CONTROLLER_PORT
DEFAULT_HOST = CONFIG_CONTROLLER_IP

# 把模型路径暴露给 scheduler（scheduler.py 里通过 os.getenv 读取），同时设置避免在线拉取模型
os.environ["SCHEDULER_MODEL_PATH"] = DEFAULT_MODEL
os.environ[
    "SCHEDULER_TOKENIZER_MAP"] = '{"llama3-70b":"/workspace/llm-stack/models/LLM-Research/Meta-Llama-3-70B-Instruct"}'
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
