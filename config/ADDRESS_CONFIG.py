# 用于方便地修改不同配置中的IP地址和端口等


# ==================================================
#                      控制器
# ==================================================
CONFIG_CONTROLLER_IP = '172.18.0.170'
CONFIG_CONTROLLER_PORT = 5000


# ==================================================
#                    Prefill 池
# ==================================================
CONFIG_GATEWAY_P_IP = '172.18.0.170'
CONFIG_GATEWAY_P_PORT = 8081


# ==================================================
#                    Decode 池
# ==================================================
CONFIG_GATEWAY_D_IP = '172.18.0.170'
CONFIG_GATEWAY_D_PORT = 8082


# ==================================================
#                    P Instance代理
# ==================================================
CONFIG_INSTANCE_P_POOL = [
    {
        "ip": "172.18.0.170",
        "port": 7001,
        "vllm_port": 8001,
        "kv_host": "172.18.0.170",
        "kv_port": 14579,
        "tp_size": 2,
    },
    {
        "ip": "172.18.0.170",
        "port": 7002,
        "vllm_port": 8002,
        "kv_host": "172.18.0.170",
        "kv_port": 14581,
        "tp_size": 2,
    },
]
CONFIG_INSTANCE_P_ADDRS = [
    f"{item['ip']}:{item['port']}" for item in CONFIG_INSTANCE_P_POOL
]

CONFIG_INSTANCE_P_IP = CONFIG_INSTANCE_P_POOL[0]["ip"] if CONFIG_INSTANCE_P_POOL else None
CONFIG_INSTANCE_P_PORT = CONFIG_INSTANCE_P_POOL[0]["port"] if CONFIG_INSTANCE_P_POOL else None
CONFIG_VLLM_P_PORT = CONFIG_INSTANCE_P_POOL[0]["vllm_port"] if CONFIG_INSTANCE_P_POOL else None


# ==================================================
#                    D Instance代理
# ==================================================
CONFIG_INSTANCE_D_POOL = [
    {
        "ip": "172.18.0.170",
        "port": 7003,
        "vllm_port": 8003,
        "kv_host": "172.18.0.170",
        "kv_port": 14583,
        "tp_size": 2,
    },
    {
        "ip": "172.18.0.170",
        "port": 7004,
        "vllm_port": 8004,
        "kv_host": "172.18.0.170",
        "kv_port": 14585,
        "tp_size": 2,
    },
]
CONFIG_INSTANCE_D_ADDRS = [
    f"{item['ip']}:{item['port']}" for item in CONFIG_INSTANCE_D_POOL
]

CONFIG_INSTANCE_D_IP = CONFIG_INSTANCE_D_POOL[0]["ip"] if CONFIG_INSTANCE_D_POOL else None
CONFIG_INSTANCE_D_PORT = CONFIG_INSTANCE_D_POOL[0]["port"] if CONFIG_INSTANCE_D_POOL else None
CONFIG_VLLM_D_PORT = CONFIG_INSTANCE_D_POOL[0]["vllm_port"] if CONFIG_INSTANCE_D_POOL else None


# ==================================================
#                    Metadata Server
# ==================================================
CONFIG_METADATA_IP = '172.18.0.170'
CONFIG_METADATA_PORT = 8080


# ==================================================
#                    KDN Server
# ==================================================
CONFIG_KDN_IP = '172.18.0.170'
CONFIG_KDN_PORT = 9101


# ==================================================
#                    REDIS
# ==================================================
CONFIG_REDIS_IP = '172.18.0.170'
CONFIG_REDIS_PORT = 6378


CONFIG_INSTANCE_NIXL_META = {
    "172.18.0.170:7001": {
        "connector": "NixlConnector",
        "role": "prefill",
        "side_channel_host": "172.18.0.170",
        "side_channel_port": 5557,
        "tp_size": 2,
    },
    "172.18.0.170:7002": {
        "connector": "NixlConnector",
        "role": "prefill",
        "side_channel_host": "172.18.0.170",
        "side_channel_port": 5558,
        "tp_size": 2,
    },
    "172.18.0.170:7003": {
        "connector": "NixlConnector",
        "role": "decode",
        "side_channel_host": "172.18.0.170",
        "side_channel_port": 5559,
        "tp_size": 2,
    },
    "172.18.0.170:7004": {
        "connector": "NixlConnector",
        "role": "decode",
        "side_channel_host": "172.18.0.170",
        "side_channel_port": 5560,
        "tp_size": 2,
    },
    "172.18.0.169:7001": {
        "connector": "NixlConnector",
        "role": "prefill",
        "side_channel_host": "172.18.0.169",
        "side_channel_port": 5557,
        "tp_size": 1,
    },
    "172.18.0.169:7002": {
        "connector": "NixlConnector",
        "role": "prefill",
        "side_channel_host": "172.18.0.169",
        "side_channel_port": 5558,
        "tp_size": 1,
    },
    "172.18.0.169:7003": {
        "connector": "NixlConnector",
        "role": "decode",
        "side_channel_host": "172.18.0.169",
        "side_channel_port": 5559,
        "tp_size": 1,
    },
    "172.18.0.169:7004": {
        "connector": "NixlConnector",
        "role": "decode",
        "side_channel_host": "172.18.0.169",
        "side_channel_port": 5560,
        "tp_size": 1,
    },
    "172.18.0.167:7001": {
        "connector": "NixlConnector",
        "role": "prefill",
        "side_channel_host": "172.18.0.167",
        "side_channel_port": 5557,
        "tp_size": 1,
    },
    "172.18.0.167:7002": {
        "connector": "NixlConnector",
        "role": "prefill",
        "side_channel_host": "172.18.0.167",
        "side_channel_port": 5558,
        "tp_size": 1,
    },
    "172.18.0.166:7001": {
        "connector": "NixlConnector",
        "role": "decode",
        "side_channel_host": "172.18.0.166",
        "side_channel_port": 5557,
        "tp_size": 1,
    },
    "172.18.0.166:7002": {
        "connector": "NixlConnector",
        "role": "decode",
        "side_channel_host": "172.18.0.166",
        "side_channel_port": 5558,
        "tp_size": 1,
    },
}


def get_nixl_meta_for_instance(instance_addr: str) -> dict:
    return dict(CONFIG_INSTANCE_NIXL_META.get(instance_addr, {}))


def is_nixl_enabled_instance(instance_addr: str) -> bool:
    return get_nixl_meta_for_instance(instance_addr).get("connector") == "NixlConnector"


def get_tp_size_for_instance(instance_addr: str) -> int:
    meta = get_nixl_meta_for_instance(instance_addr)
    return int(meta.get("tp_size") or 0)


def get_side_channel_addr_for_instance(instance_addr: str) -> str:
    meta = get_nixl_meta_for_instance(instance_addr)
    host = meta.get("side_channel_host", "")
    port = meta.get("side_channel_port", "")
    return f"{host}:{port}" if host and port else ""
