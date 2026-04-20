"""
client_web.py

基于 Flask 的 Web UI 版本，提供类似 ChatGPT 的界面来发送指令并查看响应。
只使用 Flask 基础功能，无需额外依赖。
"""
from __future__ import annotations

import json
import logging
import shlex
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import os
import requests

import uvicorn
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path


# 获取当前Python文件所在目录
current_dir = Path(__file__).parent
template_dir = current_dir / "templates"
template_dir.mkdir(exist_ok=True)
# 初始化Jinja2模板
templates = Jinja2Templates(directory=str(template_dir))


# ----------------- 原代码的数据结构和逻辑 -----------------
REQUIRED_FIELDS = {
    "chat": {"model", "messages"},
    "completions": {"model", "prompt"},
}
ALLOWED_OPTION_FIELDS = {
    "chat": {"temperature", "top_p", "max_tokens", "stream"},
    "completions": {"temperature", "top_p", "max_tokens"},
}

# ----------------- 日志配置 -----------------
logger = logging.getLogger("client")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
    )


# ----------------- 数据结构 -----------------
@dataclass
class ParsedRequest:
    """解析后的请求结构"""
    url: str
    headers: Dict[str, str]
    body: Dict[str, Any]


# ----------------- 解析逻辑 -----------------

def parse_cli_line(line: str) -> ParsedRequest:
    """解析一行用户输入"""
    line = line.strip()
    if not line:
        raise ValueError("输入为空")

    # 用 shlex 保留引号分组
    try:
        tokens = shlex.split(line)
    except ValueError as e:
        raise ValueError(f"命令行解析失败：{e}")

    if not tokens:
        raise ValueError("解析后为空，请检查输入")

    # 第一个 token 必须是 URL
    url = tokens[0]
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"第一个参数必须是完整 URL（含 http/https），当前：{url}")

    headers: Dict[str, str] = {}
    body_str: Optional[str] = None

    i = 1
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t in ("-H", "--header"):
            if i + 1 >= n:
                raise ValueError("缺少 -H/--header 后的值，例如 -H \"Content-Type: application/json\"")
            header_str = tokens[i + 1]
            # 简单按第一个 ":" 拆分
            if ":" not in header_str:
                raise ValueError(f"非法 Header 格式：{header_str}，应类似于 \"Key: Value\"")
            key, value = header_str.split(":", 1)
            headers[key.strip()] = value.strip()
            i += 2
        elif t in ("-d", "--data", "--data-raw"):
            if i + 1 >= n:
                raise ValueError("缺少 -d/--data/--data-raw 后的 JSON 字符串")
            body_str = tokens[i + 1]
            i += 2
        else:
            raise ValueError(f"无法识别的参数：{t}")

    # body 必须存在
    if body_str is None:
        raise ValueError("未提供请求体，请使用 -d/--data/--data-raw 传入 JSON 字符串")

    # 解析 JSON
    try:
        body = json.loads(body_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"请求体 JSON 解析失败：{e}")

    # 补齐 Content-Type
    ct = None
    for k in list(headers.keys()):
        if k.lower() == "content-type":
            ct = headers[k]
            break
    if ct is None:
        headers["Content-Type"] = "application/json"

    return ParsedRequest(url=url, headers=headers, body=body)


# ----------------- 校验逻辑 -----------------
def validate_openai_like_request(parsed: ParsedRequest) -> List[str]:
    """检查 URL 路径和 JSON body 是否符合 OpenAI 风格"""
    errors: List[str] = []

    parsed_url = urlparse(parsed.url)
    path = parsed_url.path or ""
    body = parsed.body

    # 公共：model 必须存在
    model = body.get("model")
    if not isinstance(model, str) or not model:
        errors.append("body.model 缺失或不是非空字符串")

    # 判定模式
    mode: Optional[str]
    if path.endswith("/v1/chat/completions"):
        mode = "chat"
    elif path.endswith("/v1/completions"):
        mode = "completions"
    else:
        mode = None

    # 按模式做字段校验
    if mode is None:
        # 未识别的路径，给个提示，但不做强字段限制
        logger.warning("未识别的路径 %s，不进行严格字段白名单校验", path)
        return errors

    # 必要字段检查
    required = REQUIRED_FIELDS.get(mode, set())
    missing = [k for k in required if k not in body]
    if missing:
        errors.append(
            f"{mode} 请求缺少必要字段：{', '.join(missing)}"
        )

    # 额外字段白名单判断
    allowed_options = ALLOWED_OPTION_FIELDS.get(mode, set())
    allowed_all = required | allowed_options

    # body 中出现，但不在允许集合里的字段，都视为非法
    extra_keys = set(body.keys()) - allowed_all
    if extra_keys:
        errors.append(
            f"{mode} 请求包含未被允许的字段：{', '.join(sorted(extra_keys))}。"
            f" 当前仅允许：{', '.join(sorted(allowed_all))}"
        )
    return errors


# ----------------- 发送请求 -----------------
def send_request(parsed: ParsedRequest, timeout: float = 60.0) -> requests.Response:
    """使用 requests 向 Scheduler 发送 POST 请求"""
    logger.info("发送请求 → %s", parsed.url)
    logger.debug("请求头: %s", parsed.headers)
    logger.debug("请求体: %s", parsed.body)

    resp = requests.post(
        parsed.url,
        headers=parsed.headers,
        json=parsed.body,
        timeout=timeout,
    )
    return resp


# ----------------- FastAPI Web UI 部分 -----------------

app = FastAPI(title="Web UI Client")

# 存储对话历史（内存中，重启后清空）
conversations = {}


def get_conversation_history(user_id):
    """获取用户的对话历史"""
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


def format_response_for_ui(resp: requests.Response) -> str:
    """将响应格式化为适合UI显示的结构，模拟控制台输出"""
    try:
        body = resp.json()
        body_str = json.dumps(body, ensure_ascii=False, indent=2)
    except ValueError:
        body_str = resp.text

    # 按照原控制台输出格式
    result = "=" * 50 + "\n"
    result += f"[RESPONSE] HTTP {resp.status_code}\n"
    result += "- Headers:\n"
    result += json.dumps(dict(resp.headers), ensure_ascii=False, indent=2) + "\n"

    result += "- Body:\n"
    result += body_str + "\n"
    result += "=" * 50

    return result


# ----------------- 路由 -----------------
@app.get('/')
def index(request: Request):
    """主页 - 显示Web UI"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post('/api/send_command')
async def api_send_command(request: Request):
    """API端点：发送命令"""
    print("收到命令请求")
    data = await request.json()
    command = data.get('command', '').strip()
    user_id = data.get('user_id', 'default')

    if not command:
        return JSONResponse(
            status_code=400,
            content={
                'error': '命令不能为空'
            }
        )

    # 添加到对话历史
    history = get_conversation_history(user_id)
    history.append({
        'type': 'command',
        'content': command,
        'timestamp': datetime.now().isoformat()
    })

    # 解析命令
    try:
        parsed = parse_cli_line(command)
    except ValueError as e:
        error_msg = f"请求解析失败：{e}"
        history.append({
            'type': 'error',
            'content': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return JSONResponse(
            status_code=400,
            content={
                'error': error_msg,
                'history': history[-2:] # 返回最近的两条消息
            }
        )

    # 校验请求
    errors = validate_openai_like_request(parsed)
    if errors:
        error_msg = "请求字段校验失败：\n" + "\n".join(f"  - {msg}" for msg in errors)
        history.append({
            'type': 'error',
            'content': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return JSONResponse(
            status_code=400,
            content={
                'error': error_msg,
                'history': history[-2:]
            }
        )

    # 发送请求
    try:
        resp = send_request(parsed)
        response_content = format_response_for_ui(resp)

        history.append({
            'type': 'response',
            'content': response_content,
            'timestamp': datetime.now().isoformat()
        })

        return JSONResponse(content={
            'success': True,
            'response': response_content,
            'history': history[-2:]
        })

    except requests.RequestException as e:
        error_msg = f"HTTP 请求异常：{e}"
        history.append({
            'type': 'error',
            'content': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return JSONResponse(
            status_code=500,
            content={
                'error': error_msg,
                'history': history[-2:]
            }
        )


@app.get('/api/get_history')
async def api_get_history(request: Request):
    """API端点：获取对话历史"""
    user_id = request.query_params.get('user_id', 'default')
    history = get_conversation_history(user_id)
    return JSONResponse(content={'history': history})


@app.post('/api/clear_history')
async def api_clear_history(request: Request):
    """API端点：清空对话历史"""
    data = await request.json()
    user_id = data.get('user_id', 'default')
    if user_id in conversations:
        conversations[user_id] = []
    return JSONResponse(content={'success': True})


# ----------------- 启动服务 -----------------

if __name__ == '__main__':
    print("=== API 客户端 Web UI ===")
    print("访问地址: http://127.0.0.1:5000")
    print("按 Ctrl+C 停止服务器")
    config = uvicorn.Config(app, host="127.0.0.1", port=5000, log_level="info")
    server = uvicorn.Server(config)
    server.run()
