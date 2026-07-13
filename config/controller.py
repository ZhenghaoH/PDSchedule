# config.py
import os
from pathlib import Path
from config.ADDRESS_CONFIG import *

# 默认模型型号，影响 token数 估算
DEFAULT_MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
DEFAULT_MODEL_SHORTNAME = "DeepSeek-R1-Distill-Qwen-1.5B"

DEFAULT_EMBED_MODEL = "/workspace/llm-stack/models/intfloat/multilingual-e5-large-instruct"

# 知识库路径
KNOWLEDGE_YAML_PATH = str(Path(__file__).resolve().parents[1] / "data" / "knowledge_base.yaml")


SCHEDULER_KDN_AUTO_REFRESH_DEFAULT = True                       # scheduler会自动触发KDN更新
SCHEDULER_KDN_REFRESH_INTERVAL_S_DEFAULT = 30                   # scheduler自动触发KDN知识更新的频率（秒）

# 知识匹配参数
SCHEDULER_RETRIEVAL_TOP_K = 3
SCHEDULER_RETRIEVAL_MIN_SCORE = 0.25                            # embedding得分阈值下限，筛选空值，cosine 下常见起点：0.2~0.35
SCHEDULER_RETRIEVAL_MIN_RATIO = 0.75                            # embedding相似度门限，只保留 >= best*0.75，减轻检索知识污染


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
os.environ["SCHEDULER_KNOWLEDGE_YAML"] = str(KNOWLEDGE_YAML_PATH)
if CONFIG_KDN_IP:
    os.environ["SCHEDULER_KDN_BASE_URL"] = f"http://{CONFIG_KDN_IP}:{CONFIG_KDN_PORT}".rstrip("/")
os.environ["SCHEDULER_EMBEDDING_MODEL"] = DEFAULT_EMBED_MODEL
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
