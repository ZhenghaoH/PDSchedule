from config.ADDRESS_CONFIG import *

DEFAULT_BASE_URL = f"http://{CONFIG_KDN_IP}:{CONFIG_KDN_PORT}" if CONFIG_KDN_IP else ""
DEFAULT_WARN_LEN = 4000                                         # 注册文本超过该长度则会警告，建议通过文件路径方式注册
# build_kv 的默认值（与你服务端默认保持一致）
DEFAULT_API_URL = "http://127.0.0.1:8000/v1/chat/completions"   # 构建KVCache块时送入的vllm服务url
DEFAULT_MAX_TOKENS = 1                                          # 最大decode token数，一般为1
DEFAULT_TEMPERATURE = 0.0                                       # 温度，模型参数，对KVCache块无影响
DEFAULT_REDIS_HOST = CONFIG_REDIS_IP                            # 存储的Redis服务器地址
DEFAULT_REDIS_PORT = CONFIG_REDIS_PORT                          # 存储的Redis服务器端口号
DEFAULT_REDIS_DB = 0                                            # 由于采用差分抓取，默认初始化时Redis服务器的DBSIZE=0
DEFAULT_MATCH = "vllm@*"                                        # 在dump KVCache时用的KEYS统一前缀
DEFAULT_SCAN_COUNT = 1000                                       # 扫描轮次，默认值即可
DEFAULT_MODEL = "DeepSeek-R1-Distill-Qwen-1.5B"
