# main.py
"""
主程序：启动调度服务器
- 仅负责应用初始化和启动
- 通过config.py获取默认配置
- 保持原run_server的启动逻辑
"""

import argparse
import uvicorn
from fastapi import FastAPI
import logging
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from config.gatewayP import *

from core.lifespans.gatewayP import lifespan
from store.gateway.manager import GatewayStoreManager
from core.routers.gatewayP import router


def setup_logger():
    """配置全局日志系统"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )
    # 为 root logger 设置格式
    logging.getLogger().setLevel(logging.INFO)


def parse_args():
    """解析命令行参数（使用config中的默认值）"""
    parser = argparse.ArgumentParser("vLLM disaggregated proxy server.")

    parser.add_argument(
        "--prefill",
        "-p",
        type=str,
        nargs="+",
        default=DEFAULT_PREFILL,
        help="List of prefill instance URLs (host:port)",
    )

    parser.add_argument(
        "--decode",
        "-d",
        type=str,
        nargs="+",
        default=DEFAULT_DECODE,
        help="List of decode proxy URLs (host:port)",
    )

    parser.add_argument(
        "--controller",
        type=str,
        default=DEFAULT_CONTROLLER,
        help="The IP address for the controller server to listen on. Supports IPv4 and IPv6."
    )

    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Server port number",
    )

    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_HOST,
        help="The IP address for the proxy server to listen on. Supports IPv4 and IPv6."
    )
    return parser.parse_args()


def main():
    """启动调度服务器"""
    # 初始化全局日志
    setup_logger()

    # 解析命令行参数
    args = parse_args()

    # 初始化核心组件
    storage = GatewayStoreManager()
    storage.set_config(args.prefill, args.decode)
    storage.host = args.host
    storage.port = args.port
    storage.controller = args.controller

    storage.SYN_TIMEOUT = SYN_TIMEOUT
    storage.SYN_BATCH_SIZE = SYN_BATCH_SIZE
    storage.rank = RANK
    storage.USE_SYN = USE_SYN
    storage.LOOP_TASK_TIME = LOOP_TASK_TIME

    storage.TRIGGER_MODE = TRIGGER_MODE
    storage.PROFILE_MODE = PROFILE_MODE

    storage.KDN_BASE_URL = KDN_BASE_URL

    # 创建FastAPI应用
    app = FastAPI(
        title="Gateway P",
        lifespan=lifespan
    )

    # 注册路由
    app.include_router(router)

    # 启动服务器
    # uvicorn.run(app, host=args.host, port=args.port, log_level="info", loop="uvloop")  # ubuntu
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")  # windows


if __name__ == "__main__":
    main()
