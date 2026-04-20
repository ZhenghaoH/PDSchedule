import urllib
import re
import requests
import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Union
import aiohttp
from fastapi import HTTPException

AIOHTTP_TIMEOUT = 6 * 60 * 60

@dataclass
class VLLLMRequestPayload:
    model: str
    prompt: Union[str, List[Dict[str, str]]]  # prompt(str) or messages(list)
    max_tokens: Optional[int]
    top_k: Optional[int]
    temperature: Optional[float]
    stream: Optional[bool]

    def to_dict(self) -> Dict[str, Any]:
        payload = {"model": self.model}
        if isinstance(self.prompt, str):
            payload["prompt"] = self.prompt
        elif isinstance(self.prompt, list):
            payload["messages"] = self.prompt
        if isinstance(self.max_tokens, int):
            payload["max_tokens"] = self.max_tokens
        if isinstance(self.top_k, int):
            payload["top_k"] = self.top_k
        if isinstance(self.temperature, float):
            payload["temperature"] = self.temperature
        if isinstance(self.stream, bool):
            payload["stream"] = self.stream
        return payload


@dataclass
class VLLMRequest:
    url: str
    headers: Dict[str, str]
    body: VLLLMRequestPayload

    @classmethod
    def build_request(cls, host: str, body: VLLLMRequestPayload, headers: Optional[Dict[str, str]] = None, use_chat: bool = True) -> "VLLMRequest":
        if headers is None:
            headers = {"Content-Type": "application/json"}
        if use_chat:
            target_url = f"http://{host}/v1/chat/completions"
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": body.prompt}
            ]
            body.prompt = messages
        else:
            target_url = f"http://{host}/v1/completions"
        req = VLLMRequest(
            url=target_url,
            headers=headers,
            body=body
        )
        return req

    async def forward_request(self, use_chunked: bool = True):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=AIOHTTP_TIMEOUT)) as session:
            try:
                async with session.post(url=self.url, json=self.body.to_dict(), headers=self.headers) as response:
                    if 200 <= response.status < 300 or 400 <= response.status < 500:  # noqa: E501
                        if use_chunked:
                            async for chunk_bytes in response.content.iter_chunked(1024):  # noqa: E501
                                yield chunk_bytes
                        else:
                            content = await response.read()
                            yield content
                    else:
                        error_content = await response.text()
                        try:
                            error_content = json.loads(error_content)
                        except json.JSONDecodeError:
                            error_content = error_content
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"Request failed with status {response.status}: "
                                   f"{error_content}",
                        )
            except aiohttp.ClientError as e:
                raise HTTPException(
                    status_code=502,
                    detail="Bad Gateway: Error communicating with upstream server.",
                ) from e
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

class TokenEstimator:
    def __init__(self, tokenizer_path: Optional[str] = None):
        self.tokenizer = None
        self.use_exact = False
        if tokenizer_path:
            try:
                # 尝试加载 tokenizer
                from transformers import AutoTokenizer
                self.tokenizer = AutoTokenizer.from_pretrained(
                    tokenizer_path,
                    trust_remote_code=True
                )
                self.use_exact = True
                print(f"✅ Tokenizer 加载成功: {tokenizer_path}")
            except Exception as e:
                print(f"⚠️ Tokenizer 加载失败，将使用估算方式: {e}")
                self.use_exact = False

    def estimate_tokens(self, prompt: str) -> int:
        if self.use_exact and self.tokenizer:
            try:
                encoding = self.tokenizer.encode(prompt)
                return len(encoding)
            except Exception as e:
                print(f"encoding failed, error: {e}. use simple estimator.")

        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', prompt))  # 计算中文字符数量
        non_chinese_chars = len(prompt) - chinese_chars  # 计算非中文字符数量
        return int(chinese_chars * 0.75 + non_chinese_chars / 4)  # 估算token数


def create_completions(request: VLLMRequest, batch_size: int = 1, tokenizer: Optional[str] = None):
    estimator = TokenEstimator(tokenizer)
    if batch_size < 1:
        print("batch_size should > 1.")
    elif batch_size == 1:
        generator = request.forward_request()
    else:
        # TODO: 多条请求批处理
        pass


def main():
    body = VLLLMRequestPayload(
        top_k=None,
        temperature=None,
        max_tokens=None,
        stream=True,
        model="/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
        # prompt="你觉得你是职业AI吗？",
        prompt=input("Please input your prompt: "),
    )
    req = VLLMRequest.build_request(
        host="172.18.0.169:8000",
        body=body,
        use_chat=True,
    )
    batch_size = 1
    create_completions(req, batch_size)


if __name__ == '__main__':
    main()
