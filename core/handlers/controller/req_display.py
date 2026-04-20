"""
请求状态显示处理器 - 基于BaseRequest结构
"""

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, List, Any
import json
from datetime import datetime
from pathlib import Path

from config.controller import TEMPLATES_PATH
from store.controller.pool_manager import PoolManager
global_storage = PoolManager()


# 初始化Jinja2模板
templates = Jinja2Templates(directory=str(TEMPLATES_PATH))


def get_request_data(req_obj: Any) -> Dict[str, Any]:
    """从BaseRequest实例中提取数据"""
    try:
        # 基础请求信息
        request_data = {
            "request_id": str(getattr(req_obj, 'Request_ID', 'N/A')),
            "request_type": str(getattr(req_obj, 'Request_type', 'N/A')),
        }

        # 获取Prompt信息
        prompt = getattr(req_obj, 'Prompt', None)
        if prompt:
            request_data["model"] = str(getattr(prompt, 'model', 'N/A'))
            request_data["user_prompt"] = str(getattr(prompt, 'user_prompt', 'N/A'))
            request_data["token_length"] = str(getattr(prompt, 'token_length', 'N/A'))
            request_data["bs"] = str(getattr(prompt, 'bs', 'N/A'))
            request_data["max_tokens"] = str(getattr(prompt, 'max_tokens', 'N/A'))
            request_data["stream"] = str(getattr(prompt, 'stream', 'N/A'))
            request_data["temperature"] = str(getattr(prompt, 'temperature', '1.0'))
            request_data["top_p"] = str(getattr(prompt, 'top_p', '1.0'))

        # 获取Service信息
        service = getattr(req_obj, 'Service', None)
        if service:
            request_data["enable_pd_disaggregation"] = str(getattr(service, 'Enable_PD_Disaggregation', 'N/A'))
            request_data["enable_know_injection"] = str(getattr(service, 'Enable_know_injection', 'N/A'))
            request_data["enable_compress"] = str(getattr(service, 'Enable_compress', 'N/A'))
            request_data["compress_factor"] = str(getattr(service, 'Compress_factor', 'N/A'))
            request_data["slo_ttft"] = str(getattr(service, 'SLO_TTFT', 'N/A'))
            request_data["slo_e2e"] = str(getattr(service, 'SLO_E2E', 'N/A'))
            request_data["slo_tpot"] = str(getattr(service, 'SLO_TPOT', 'N/A'))
            request_data["endpoint_type"] = str(getattr(service, 'Endpoint_type', 'N/A'))

        # 获取Task信息 - 这是最重要的部分
        task = getattr(req_obj, 'Task', None)
        if task:
            request_data["user_addr"] = str(getattr(task, 'User_addr', 'N/A'))
            request_data["prefill_pool"] = f"{getattr(task, 'P_proxy_addr', 'N/A')}:{getattr(task, 'P_proxy_port', 'N/A')}"
            request_data["decode_pool"] = f"{getattr(task, 'D_proxy_addr', 'N/A')}:{getattr(task, 'D_proxy_port', 'N/A')}"
            request_data["prefill_instance"] = str(getattr(task, 'prefill_instance', 'N/A'))
            request_data["decode_instance"] = str(getattr(task, 'decode_instance', 'N/A'))
            request_data["batch_order"] = str(getattr(task, 'batch_order', 'N/A'))
            request_data["user_url_path"] = str(getattr(task, 'User_url_path', 'N/A'))
            request_data["kdn_server_addr"] = str(getattr(task, 'KDN_server_addr', 'N/A'))
            request_data["default_know_addr"] = str(getattr(task, 'default_know_addr', 'N/A'))
        else:
            request_data["prefill_pool"] = "N/A"
            request_data["decode_pool"] = "N/A"

        # 添加创建时间戳（如果请求对象有创建时间）
        if hasattr(req_obj, 'create_time'):
            request_data["create_time"] = str(getattr(req_obj, 'create_time'))
        else:
            request_data["create_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 简化的状态（基于是否已完成）
        if hasattr(req_obj, 'finished') and getattr(req_obj, 'finished', False):
            request_data["simple_status"] = "completed"
        elif hasattr(req_obj, 'running') and getattr(req_obj, 'running', False):
            request_data["simple_status"] = "running"
        else:
            request_data["simple_status"] = "waiting"

        # 添加原始对象字符串表示（用于调试）
        request_data["_repr"] = str(req_obj) if hasattr(req_obj, '__repr__') else str(type(req_obj))

    except Exception as e:
        request_data = {
            "request_id": "ERROR",
            "request_type": "ERROR",
            "prefill_pool": "N/A",
            "decode_pool": "N/A",
            "error": str(e)
        }

    return request_data


def generate_requests_data() -> Dict[str, Any]:
    """生成请求数据"""
    try:
        # 获取请求列表
        requests_list = global_storage.get_reqs()

        # 处理每个请求
        processed_requests = []
        total_requests = len(requests_list)

        # 按请求类型统计
        type_counts = {}

        # 按池统计
        prefill_pools = {}
        decode_pools = {}

        for i, req in enumerate(requests_list):
            req_data = get_request_data(req)
            req_data["index"] = i + 1
            processed_requests.append(req_data)

            # 统计请求类型
            req_type = req_data.get("request_type", "unknown")
            type_counts[req_type] = type_counts.get(req_type, 0) + 1

            # 统计Prefill池使用情况
            prefill_pool = req_data.get("prefill_pool", "N/A")
            if prefill_pool != "N/A":
                prefill_pools[prefill_pool] = prefill_pools.get(prefill_pool, 0) + 1

            # 统计Decode池使用情况
            decode_pool = req_data.get("decode_pool", "N/A")
            if decode_pool != "N/A":
                decode_pools[decode_pool] = decode_pools.get(decode_pool, 0) + 1

        # 按请求类型排序
        sorted_requests = sorted(processed_requests, key=lambda x: x["index"])

        return {
            "requests": sorted_requests,
            "total_requests": total_requests,
            "type_counts": type_counts,
            "prefill_pools": prefill_pools,
            "decode_pools": decode_pools,
            "unique_prefill_pools": len(prefill_pools),
            "unique_decode_pools": len(decode_pools),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "has_requests": total_requests > 0
        }

    except Exception as e:
        print(f"Error generating requests data: {e}")
        return {
            "requests": [],
            "total_requests": 0,
            "type_counts": {},
            "prefill_pools": {},
            "decode_pools": {},
            "unique_prefill_pools": 0,
            "unique_decode_pools": 0,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "has_requests": False,
            "error": str(e)
        }


async def show_requests(request: Request) -> HTMLResponse:
    """显示请求状态页面"""
    requests_data = generate_requests_data()

    return templates.TemplateResponse(
        "requests.html",
        {
            "request": request,
            "requests_data": requests_data
        }
    )