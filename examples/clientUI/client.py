"""
client_web.py

基于 Fastapi 的 Web UI 版本，提供类似 ChatGPT 的界面来发送指令并查看响应。
支持控制台指令模式和聊天Prompt模式。
支持流式响应显示。
支持性能指标计算（包括流式和非流式模式）。
"""
from __future__ import annotations

import json
import logging
import shlex
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, AsyncGenerator, Tuple
from urllib.parse import urlparse
import requests
import aiohttp

import uvicorn
from fastapi import FastAPI, HTTPException
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


# ----------------- 流式转发函数 -----------------
AIOHTTP_TIMEOUT = 60.0

async def forward_request_stream(
    url: str,
    data: dict,
    extra_headers: Optional[dict] = None
) -> AsyncGenerator[Tuple[bytes, Optional[dict]], None]:
    """
    将请求以 HTTP POST 方式转发到下游，并以异步流的形式返回响应内容。
    返回元组：(chunk_bytes, metrics_info)
    在流式传输中只计算时间指标。
    """
    timeout = aiohttp.ClientTimeout(total=AIOHTTP_TIMEOUT)

    # 合并headers
    headers = {}
    if extra_headers:
        headers.update(extra_headers)

    # 确保有Content-Type
    if "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"

    # 记录开始时间
    start_time = time.time()
    first_token_time: Optional[float] = None

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(url=url, json=data, headers=headers) as response:
                # 下游返回 2xx 或 4xx 时，视为"正常响应"
                if 200 <= response.status < 300 or 400 <= response.status < 500:
                    # 流式模式：每次读取最多 1024 字节
                    async for chunk_bytes in response.content.iter_chunked(1024):
                        # 记录第一个token到达时间
                        if first_token_time is None:
                            first_token_time = time.time()

                        yield chunk_bytes, None

                    # 流式响应结束后，发送最终指标
                    end_time = time.time()

                    # 计算总时间
                    total_time = end_time - start_time

                    # 计算TTFT（首token时间）
                    ttft = (first_token_time - start_time) if first_token_time else total_time

                    # 流式模式下，我们不计算token数和TPOT
                    metrics_info = {
                        "total_time": total_time,
                        "ttft": ttft,
                        "tpot": 0,  # 流式模式下不计算TPOT
                        "input_tokens": 0,  # 流式模式下不计算token数
                        "output_tokens": 0,  # 流式模式下不计算token数
                        "is_final": True  # 标记为最终指标
                    }

                    # 发送一个空的chunk和指标信息
                    yield b'', metrics_info

                else:
                    # 其它状态码：记录错误日志并抛出 HTTPException
                    error_content = await response.text()
                    try:
                        error_content = json.loads(error_content)
                    except json.JSONDecodeError:
                        pass

                    logger.error("请求失败，状态码 %s: %s", response.status, error_content)
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"请求失败，状态码 {response.status}: {error_content}",
                    )

        except aiohttp.ClientError as e:
            logger.error("ClientError 发生: %s", str(e))
            raise HTTPException(
                status_code=502,
                detail="Bad Gateway: 与上游服务器通信时发生错误。",
            ) from e
        except Exception as e:
            logger.error("未预期的错误: %s", str(e))
            raise HTTPException(status_code=500, detail=str(e)) from e


# ----------------- 非流式转发函数 -----------------
async def forward_request_non_stream(
    url: str,
    data: dict,
    extra_headers: Optional[dict] = None
) -> Tuple[bytes, dict]:
    """
    非流式请求：一次性获取完整响应体，并解析指标
    """
    timeout = aiohttp.ClientTimeout(total=AIOHTTP_TIMEOUT)

    headers = {}
    if extra_headers:
        headers.update(extra_headers)

    if "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(url=url, json=data, headers=headers) as response:
                if 200 <= response.status < 300 or 400 <= response.status < 500:
                    content = await response.read()

                    # 解析响应以获取token信息
                    try:
                        resp_data = json.loads(content.decode('utf-8'))
                        input_tokens = resp_data.get("usage", {}).get("prompt_tokens", 0)
                        output_tokens = resp_data.get("usage", {}).get("completion_tokens", 0)
                    except:
                        input_tokens = 0
                        output_tokens = 0

                    return content, {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens
                    }
                else:
                    error_content = await response.text()
                    try:
                        error_content = json.loads(error_content)
                    except json.JSONDecodeError:
                        pass

                    logger.error("请求失败，状态码 %s: %s", response.status, error_content)
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"请求失败，状态码 {response.status}: {error_content}",
                    )
        except aiohttp.ClientError as e:
            logger.error("ClientError 发生: %s", str(e))
            raise HTTPException(
                status_code=502,
                detail="Bad Gateway: 与上游服务器通信时发生错误。",
            ) from e
        except Exception as e:
            logger.error("未预期的错误: %s", str(e))
            raise HTTPException(status_code=500, detail=str(e)) from e


# ----------------- 响应处理函数 -----------------
def extract_content_from_response(resp_text: str, mode: str = "chat") -> str:
    """从响应中提取内容文本"""
    try:
        # 尝试解析为JSON
        data = json.loads(resp_text)

        if mode == "chat":
            # Chat模式
            if "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                if "message" in choice and "content" in choice["message"]:
                    return choice["message"]["content"]
                elif "delta" in choice and "content" in choice["delta"]:
                    return choice["delta"]["content"]
        elif mode == "completions":
            # Completions模式
            if "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                if "text" in choice:
                    return choice["text"]

        # 如果无法提取，返回原始JSON字符串
        return json.dumps(data, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        # 如果不是JSON，可能是流式响应的一部分
        if resp_text.startswith("data: "):
            # 尝试提取JSON部分
            json_str = resp_text[6:]  # 移除"data: "前缀
            if json_str.strip() == "[DONE]":
                return ""
            try:
                data = json.loads(json_str)
                # 提取内容
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "delta" in choice and "content" in choice["delta"]:
                        return choice["delta"]["content"]
                    elif "text" in choice:
                        return choice["text"]
            except:
                pass
        # 如果所有尝试都失败，返回原始文本
        return resp_text


# ----------------- FastAPI Web UI 部分 -----------------

app = FastAPI(title="Web UI Client")

# 存储对话历史（内存中，重启后清空）
conversations = {}


def get_conversation_history(user_id):
    """获取用户的对话历史"""
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


def format_response_for_ui(resp: requests.Response, mode: str = "console") -> str:
    """将响应格式化为适合UI显示的结构"""
    try:
        # 控制台模式：显示完整的响应格式
        if mode == "console":
            try:
                body = resp.json()
                body_str = json.dumps(body, ensure_ascii=False, indent=2)
            except ValueError:
                body_str = resp.text

            result = "=" * 50 + "\n"
            result += f"[RESPONSE] HTTP {resp.status_code}\n"
            result += "- Headers:\n"
            result += json.dumps(dict(resp.headers), ensure_ascii=False, indent=2) + "\n"
            result += "- Body:\n"
            result += body_str + "\n"
            result += "=" * 50
            return result

        # 聊天模式：只提取内容文本
        else:
            # 非流式响应
            try:
                data = resp.json()
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        return choice["message"]["content"]
                    elif "text" in choice:
                        return choice["text"]
            except:
                pass

            # 如果无法提取，返回原始文本
            return resp.text[:1000] + ("..." if len(resp.text) > 1000 else "")

    except Exception as e:
        logger.error(f"格式化响应失败: {e}")
        return f"响应处理错误: {str(e)}\n原始响应: {resp.text[:500]}"


# ----------------- 流式响应生成器 -----------------
async def generate_stream_response(
    url: str,
    data: dict,
    headers: dict
):
    """生成流式响应，同时计算性能指标"""
    try:
        # 使用forward_request_stream获取流式响应和指标
        async for chunk, metrics_info in forward_request_stream(
            url, data, extra_headers=headers
        ):
            if chunk:
                # 将字节转换为字符串
                chunk_str = chunk.decode('utf-8', errors='ignore')

                # 如果是SSE格式，提取内容
                lines = chunk_str.strip().split('\n')
                for line in lines:
                    if line.startswith('data: '):
                        data_line = line[6:]  # 移除"data: "前缀
                        if data_line.strip() == '[DONE]':
                            # 发送结束标记
                            yield f'data: {json.dumps({"type": "done", "content": ""})}\n\n'
                        else:
                            try:
                                data_json = json.loads(data_line)
                                if "choices" in data_json and len(data_json["choices"]) > 0:
                                    choice = data_json["choices"][0]
                                    if "delta" in choice and "content" in choice["delta"]:
                                        content = choice["delta"]["content"]
                                        if content:
                                            # 发送内容块
                                            yield f'data: {json.dumps({"type": "chunk", "content": content})}\n\n'
                            except json.JSONDecodeError:
                                continue

            # 如果有关联的指标信息，发送给前端
            if metrics_info:
                # 转换为毫秒并格式化
                formatted_metrics = {
                    "total_time": round(metrics_info.get("total_time", 0) * 1000, 2),
                    "ttft": round(metrics_info.get("ttft", 0) * 1000, 2),
                    "tpot": round(metrics_info.get("tpot", 0) * 1000, 2),
                    "input_tokens": metrics_info.get("input_tokens", 0),
                    "output_tokens": metrics_info.get("output_tokens", 0)
                }

                yield f'data: {json.dumps({"type": "metrics", "metrics": formatted_metrics})}\n\n'

    except Exception as e:
        logger.error(f"流式响应生成错误: {e}")
        yield f'data: {json.dumps({"type": "error", "content": f"流式响应错误: {str(e)}"})}\n\n'


# ----------------- 路由 -----------------
@app.get('/')
def index(request: Request):
    """主页 - 显示Web UI"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post('/api/send_command')
async def api_send_command(request: Request):
    """API端点：发送命令"""
    data = await request.json()
    command = data.get('command', '').strip()
    user_id = data.get('user_id', 'default')
    mode = data.get('mode', 'chat')  # 默认改为聊天模式

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
        'mode': mode,
        'timestamp': datetime.now().isoformat()
    })

    # 聊天模式：构建完整的命令
    if mode == 'chat':
        try:
            # 解析前端传递的参数
            params = data.get('params', {})
            model = params.get('model', '/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B')
            chat_mode = params.get('chat_mode', 'chat')
            stream_enabled = params.get('stream', 'False')
            server_url = params.get('server_url', 'http://127.0.0.1:5000')

            # 构建基础请求体
            if chat_mode == 'chat':
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": command}],
                }
                endpoint = "/v1/chat/completions"
                # chat模式支持流式传输
                if stream_enabled == 'True':
                    body["stream"] = True
            else:  # completions 模式
                body = {
                    "model": model,
                    "prompt": command
                }
                endpoint = "/v1/completions"
                # completions模式不支持stream字段，忽略流式传输设置

            # 添加可选参数（如果启用）
            if params.get('use_temperature', False):
                temperature = params.get('temperature', 0.7)
                body["temperature"] = temperature

            if params.get('use_top_p', False):
                top_p = params.get('top_p', 1.0)
                body["top_p"] = top_p

            if params.get('use_max_tokens', False):
                max_tokens = params.get('max_tokens', 64)
                body["max_tokens"] = max_tokens

            # 构建完整的命令行
            url = f"{server_url}{endpoint}"
            command_str = f'{url} -H "Content-Type: application/json" -d \'{json.dumps(body)}\''

            # 解析命令
            parsed = parse_cli_line(command_str)

            # 校验请求（聊天模式下也需要进行字段检查）
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

            # 检查是否是流式请求（仅chat模式支持）
            is_streaming = body.get("stream", False) if chat_mode == "chat" else False

            # 记录开始时间
            start_time = time.time()

            if is_streaming:
                # 流式请求：使用StreamingResponse
                return StreamingResponse(
                    generate_stream_response(parsed.url, parsed.body, parsed.headers),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no"  # 禁用Nginx缓冲
                    }
                )
            else:
                # 非流式请求：使用forward_request一次性获取
                full_content, token_info = await forward_request_non_stream(
                    parsed.url, parsed.body, extra_headers=parsed.headers
                )

                # 记录结束时间
                end_time = time.time()
                total_time = end_time - start_time

                # 处理响应
                resp_text = full_content.decode('utf-8')
                response_content = extract_content_from_response(resp_text, chat_mode)

                # 获取token使用情况
                input_tokens = token_info.get("input_tokens", 0)
                output_tokens = token_info.get("output_tokens", 0)

                # 计算指标
                ttft = total_time  # 非流式模式下，TTFT就是总时间
                tpot = total_time / output_tokens if output_tokens > 0 else 0

                history.append({
                    'type': 'response',
                    'content': response_content,
                    'timestamp': datetime.now().isoformat(),
                    'metrics': {
                        'total_time': round(total_time * 1000, 2),  # 转换为毫秒
                        'ttft': round(ttft * 1000, 2),
                        'tpot': round(tpot * 1000, 2),
                        'input_tokens': input_tokens,
                        'output_tokens': output_tokens
                    }
                })

                return JSONResponse(content={
                    'success': True,
                    'response': response_content,
                    'history': history[-2:],
                    'streaming': False,
                    'metrics': {
                        'total_time': round(total_time * 1000, 2),
                        'ttft': round(ttft * 1000, 2),
                        'tpot': round(tpot * 1000, 2),
                        'input_tokens': input_tokens,
                        'output_tokens': output_tokens
                    }
                })

        except Exception as e:
            error_msg = f"构建聊天命令失败：{e}"
            logger.error(error_msg)
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

    # 控制台模式：解析原始命令
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
                'history': history[-2:]
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

    # 发送请求（控制台模式）
    try:
        # 记录开始时间
        start_time = time.time()

        resp = send_request(parsed)
        response_content = format_response_for_ui(resp, mode="console")

        # 记录结束时间
        end_time = time.time()
        total_time = end_time - start_time

        # 尝试从响应中获取token信息
        try:
            resp_data = resp.json()
            input_tokens = resp_data.get("usage", {}).get("prompt_tokens", 0)
            output_tokens = resp_data.get("usage", {}).get("completion_tokens", 0)
        except:
            input_tokens = 0
            output_tokens = 0

        # 计算指标
        ttft = total_time  # 非流式模式下，TTFT就是总时间
        tpot = total_time / output_tokens if output_tokens > 0 else 0

        history.append({
            'type': 'response',
            'content': response_content,
            'timestamp': datetime.now().isoformat(),
            'metrics': {
                'total_time': round(total_time * 1000, 2),
                'ttft': round(ttft * 1000, 2),
                'tpot': round(tpot * 1000, 2),
                'input_tokens': input_tokens,
                'output_tokens': output_tokens
            }
        })

        return JSONResponse(content={
            'success': True,
            'response': response_content,
            'history': history[-2:],
            'streaming': False,
            'metrics': {
                'total_time': round(total_time * 1000, 2),
                'ttft': round(ttft * 1000, 2),
                'tpot': round(tpot * 1000, 2),
                'input_tokens': input_tokens,
                'output_tokens': output_tokens
            }
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
    print("访问地址: http://127.0.0.1:5050")
    print("按 Ctrl+C 停止服务器")
    config = uvicorn.Config(app, host="127.0.0.1", port=5050, log_level="info")
    server = uvicorn.Server(config)
    server.run()
