# config.py
import json
import os
from pdschedule.resources import controller_template_dir
from config.ADDRESS_CONFIG import *

# 默认模型型号，影响 token数 估算
DEFAULT_MODEL = os.getenv(
    "PDSCHEDULE_MODEL_PATH",
    "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
)
DEFAULT_MODEL_SHORTNAME = os.getenv(
    "PDSCHEDULE_MODEL_NAME",
    "DeepSeek-R1-Distill-Qwen-1.5B",
)

# 网页模板路径
TEMPLATES_PATH = str(controller_template_dir())

# 服务配置
# DEFAULT_PORT = 8000
# DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = CONFIG_CONTROLLER_PORT
DEFAULT_HOST = CONFIG_CONTROLLER_IP

# 把模型路径暴露给 scheduler（scheduler.py 里通过 os.getenv 读取），同时设置避免在线拉取模型
_default_tokenizer_map = {
    "llama3-70b": "/workspace/llm-stack/models/LLM-Research/Meta-Llama-3-70B-Instruct"
}
_tokenizer_path = os.getenv("PDSCHEDULE_TOKENIZER_PATH", "").strip()
if _tokenizer_path:
    _default_tokenizer_map[DEFAULT_MODEL_SHORTNAME] = _tokenizer_path
_tokenizer_map = os.getenv("PDSCHEDULE_TOKENIZER_MAP", "").strip()
if _tokenizer_map:
    try:
        parsed_tokenizer_map = json.loads(_tokenizer_map)
    except json.JSONDecodeError as exc:
        raise ValueError("PDSCHEDULE_TOKENIZER_MAP must contain a JSON object") from exc
    if not isinstance(parsed_tokenizer_map, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in parsed_tokenizer_map.items()
    ):
        raise ValueError("PDSCHEDULE_TOKENIZER_MAP must map model names to paths")
    _default_tokenizer_map.update(parsed_tokenizer_map)

os.environ["SCHEDULER_MODEL_PATH"] = DEFAULT_MODEL
os.environ["SCHEDULER_TOKENIZER_MAP"] = json.dumps(_default_tokenizer_map)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
