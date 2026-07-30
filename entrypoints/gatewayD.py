# main.py
"""
主程序：启动调度服务器
- 仅负责应用初始化和启动
- 通过config.py获取默认配置
- 保持原run_server的启动逻辑
"""

import argparse
import json
from fastapi import FastAPI
import logging
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from config.gatewayD import *

from core.lifespans.gatewayD import lifespan
from store.gateway.manager import GatewayStoreManager
from core.routers.gatewayD import router
from core.consoles.gateway import run_gateway_server


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

    parser.add_argument(
        "--decode-scheduler-strategy",
        choices=[
            "round_robin",
            "lowest_generation_throughput",
            "slo_aware_decode",
        ],
        default=DECODE_SCHEDULER_STRATEGY,
        help="Decode async scheduler strategy.",
    )

    parser.add_argument(
        "--no-cli",
        action="store_true",
        help="Disable the interactive gateway CLI for background deployments.",
    )
    parser.add_argument("--connector-name", choices=["nixl", "lmcache"], default="nixl")
    parser.add_argument("--connector-version", default="1")
    parser.add_argument("--connector-metadata", type=json.loads, default={})

    tpot_warmup_group = parser.add_mutually_exclusive_group()
    tpot_warmup_group.add_argument(
        "--tpot-warmup",
        dest="tpot_warmup",
        action="store_true",
        help="Run TPOT predictor warmup in the background after the Decode gateway starts.",
    )
    tpot_warmup_group.add_argument(
        "--no-tpot-warmup",
        dest="tpot_warmup",
        action="store_false",
        help="Disable TPOT predictor warmup even if it is enabled in config.",
    )
    parser.set_defaults(tpot_warmup=TPOT_WARMUP_ENABLED)

    parser.add_argument(
        "--tpot-warmup-repeats",
        type=int,
        default=TPOT_WARMUP_REPEATS,
        help="Repeat count for each TPOT warmup configuration.",
    )

    parser.add_argument(
        "--tpot-target",
        type=str,
        default=TPOT_TARGET,
        help="Target vLLM instance for TPOT warmup, formatted as host:port. Defaults to the first --decode instance.",
    )

    parser.add_argument(
        "--tpot-model-id",
        type=str,
        default=TPOT_MODEL_ID,
        help="Model ID used by TPOT warmup requests.",
    )

    parser.add_argument(
        "--tpot-tokenizer-path",
        type=str,
        default=TPOT_TOKENIZER_PATH,
        help="Tokenizer path used by TPOT warmup prompt generation.",
    )

    parser.add_argument(
        "--tpot-model-file",
        type=str,
        default=TPOT_MODEL_FILE,
        help="Local file path for loading and saving the fitted TPOT predictor.",
    )
    return parser.parse_args()


def main():
    """启动调度服务器"""
    # 初始化全局日志
    setup_logger()

    # 解析命令行参数
    args = parse_args()
    missing = [name for name, value in {
        "host": args.host, "controller": args.controller,
    }.items() if not value]
    if missing:
        raise SystemExit(f"Missing required deployment options: {', '.join(missing)}")
    if args.tpot_warmup and not (args.tpot_target or args.decode):
        raise SystemExit("TPOT warmup requires --tpot-target or at least one --decode instance")
    if not args.tpot_target and args.decode:
        args.tpot_target = args.decode[0]

    # 初始化核心组件
    storage = GatewayStoreManager()
    storage.set_config(decodes=args.decode)
    storage.host = args.host
    storage.port = args.port
    storage.controller = args.controller
    storage.set_connector({
        "name": args.connector_name,
        "version": args.connector_version,
        "metadata": args.connector_metadata,
    })

    storage.SYN_TIMEOUT = SYN_TIMEOUT
    storage.SYN_BATCH_SIZE = SYN_BATCH_SIZE
    storage.rank = RANK
    storage.USE_SYN = USE_SYN
    storage.LOOP_TASK_TIME = LOOP_TASK_TIME
    storage.set_decode_scheduler_strategy(args.decode_scheduler_strategy)

    storage.TPOT_WARMUP_ENABLED = args.tpot_warmup
    storage.TPOT_WARMUP_REPEATS = args.tpot_warmup_repeats
    storage.TPOT_TARGET = args.tpot_target
    storage.TPOT_MODEL_ID = args.tpot_model_id
    storage.TPOT_TOKENIZER_PATH = args.tpot_tokenizer_path
    storage.TPOT_MODEL_FILE = args.tpot_model_file

    # storage.TRIGGER_MODE = TRIGGER_MODE
    # storage.PROFILE_MODE = PROFILE_MODE

    # 创建FastAPI应用
    app = FastAPI(
        title="Gateway D",
        lifespan=lifespan
    )

    # 注册路由
    app.include_router(router)

    logging.info("Decode scheduler strategy: %s", args.decode_scheduler_strategy)
    run_gateway_server(
        app,
        args.host,
        args.port,
        "decode",
        storage,
        args.no_cli,
    )


if __name__ == "__main__":
    main()
