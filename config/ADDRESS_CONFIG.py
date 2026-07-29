"""Shared address defaults.

Machine-specific addresses belong in command-line arguments or environment
variables.  This module intentionally contains no deployment topology.
"""

import os


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def env_list(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


CONFIG_CONTROLLER_IP = os.getenv("FLEXPD_CONTROLLER_HOST", "")
CONFIG_CONTROLLER_PORT = env_int("FLEXPD_CONTROLLER_PORT", 5000)

CONFIG_GATEWAY_P_IP = os.getenv("FLEXPD_GATEWAY_P_HOST", "")
CONFIG_GATEWAY_P_PORT = env_int("FLEXPD_GATEWAY_P_PORT", 8081)
CONFIG_GATEWAY_D_IP = os.getenv("FLEXPD_GATEWAY_D_HOST", "")
CONFIG_GATEWAY_D_PORT = env_int("FLEXPD_GATEWAY_D_PORT", 8082)

CONFIG_INSTANCE_P_ADDRS = env_list("FLEXPD_PREFILL_INSTANCES")
CONFIG_INSTANCE_D_ADDRS = env_list("FLEXPD_DECODE_INSTANCES")

CONFIG_METADATA_IP = os.getenv("FLEXPD_METADATA_HOST", "")
CONFIG_METADATA_PORT = env_int("FLEXPD_METADATA_PORT", 8080)
CONFIG_REDIS_IP = os.getenv("FLEXPD_REDIS_HOST", "")
CONFIG_REDIS_PORT = env_int("FLEXPD_REDIS_PORT", 6378)

NIXL_CONNECTOR = "NixlConnector"
PREFILL_ROLE = "prefill"
DECODE_ROLE = "decode"
