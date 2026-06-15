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
CONFIG_GATEWAY_D_IP = '172.18.0.166'
CONFIG_GATEWAY_D_PORT = 8082


# ==================================================
#                    P Instance代理
# ==================================================
CONFIG_INSTANCE_P_POOL = [

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
        "ip": "172.18.0.166",
        "port": 7002,
        "vllm_port": 8002,
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