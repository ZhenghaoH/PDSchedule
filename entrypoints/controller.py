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
import threading
import time
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from config.controller import (
    DEFAULT_PORT,
    DEFAULT_HOST,
    DEFAULT_ROUTING_MODE,
    DEFAULT_FIXED_PREFILL_GATEWAY,
    DEFAULT_FIXED_DECODE_GATEWAY,
)

from core.routers.controller import router
from core.lifespans.controller import lifespan
from core.consoles.controller import run_scheduler_cli
from scheduler.controller.global_scheduler import parse_gateway_endpoint


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
    parser = argparse.ArgumentParser("vLLM disaggregated Scheduler server.")
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
        help="The IP address for the Scheduler server to listen on",
    )
    parser.add_argument(
        "--routing-mode",
        choices=["global", "fixed_gateway"],
        default=DEFAULT_ROUTING_MODE,
        help="Controller routing mode: global pool scheduling or fixed P/D gateways.",
    )
    parser.add_argument(
        "--fixed-prefill-gateway",
        default=DEFAULT_FIXED_PREFILL_GATEWAY,
        help="Prefill gateway for fixed_gateway mode, formatted as host:port.",
    )
    parser.add_argument(
        "--fixed-decode-gateway",
        default=DEFAULT_FIXED_DECODE_GATEWAY,
        help="Decode gateway for fixed_gateway mode, formatted as host:port.",
    )
    return parser.parse_args()


def main():
    """启动调度服务器"""
    # 初始化全局日志
    setup_logger()

    # 解析命令行参数
    args = parse_args()
    if not args.host:
        raise SystemExit("Missing required deployment option: host")
    if args.routing_mode == "fixed_gateway":
        if not args.fixed_prefill_gateway:
            raise SystemExit(
                "Missing required deployment option for fixed_gateway: "
                "--fixed-prefill-gateway or FLEXPD_FIXED_PREFILL_GATEWAY"
            )
        if not args.fixed_decode_gateway:
            raise SystemExit(
                "Missing required deployment option for fixed_gateway: "
                "--fixed-decode-gateway or FLEXPD_FIXED_DECODE_GATEWAY"
            )
        try:
            parse_gateway_endpoint(args.fixed_prefill_gateway)
            parse_gateway_endpoint(args.fixed_decode_gateway)
        except ValueError as exc:
            raise SystemExit(f"Invalid fixed gateway configuration: {exc}") from exc

    # 创建FastAPI应用
    app = FastAPI(
        title="Scheduler",
        lifespan=lifespan,
    )
    app.state.controller_routing_mode = args.routing_mode
    app.state.fixed_prefill_gateway = args.fixed_prefill_gateway
    app.state.fixed_decode_gateway = args.fixed_decode_gateway

    # 注册路由
    app.include_router(router)

    # 配置 uvicorn.Server
    # server_config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info", loop="uvloop")  # ubuntu
    server_config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")  # windows
    server = uvicorn.Server(server_config)

    # 启动 uvicorn server 到后台线程
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    time.sleep(3)

    # 启动 CLI，与 scheduler 交互
    #    注意：run_scheduler_cli 内部会阻塞直到用户输入 quit/exit
    run_scheduler_cli(app=app, server=server)

    # 等待 server 线程结束（优雅退出）
    server_thread.join()
    print("Scheduler stopped, HTTP Server exit.")


if __name__ == "__main__":
    main()
