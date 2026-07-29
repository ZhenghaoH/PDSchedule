#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import types
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class MockScheduler:
    def __init__(self) -> None:
        self.calls = 0

    def schedule(self, prompt_obj: Any, service_obj: Any, request_id: int) -> Dict[str, Any]:
        self.calls += 1
        return {
            "P_proxy_addr": f"127.0.0.{self.calls}",
            "P_proxy_port": 8000 + self.calls,
            "D_proxy_addr": f"127.0.1.{self.calls}",
            "D_proxy_port": 9000 + self.calls,
        }


ROUND_QUESTIONS = [
    "请用一句话解释什么是会话亲和调度。",
    "请说明启用亲和调度后，对连续请求有什么影响。",
    "请比较亲和调度和普通轮询调度的主要区别。",
    "请举一个用户连续多轮对话适合使用亲和调度的例子。",
    "请解释为什么亲和调度可能降低重复请求的首 token 延迟。",
    "请说明在什么情况下不应该复用历史调度结果。",
    "请总结亲和调度对 KV Cache 命中率的潜在帮助。",
    "请解释 user_id 和 session_id 在亲和调度中的作用。",
    "请用简短语言说明 warmup 请求和命中请求的区别。",
    "请说明为什么测试亲和调度时要分别统计 baseline 和 affinity hit。",
]


def build_round_question(round_idx: int) -> str:
    if round_idx < len(ROUND_QUESTIONS):
        return ROUND_QUESTIONS[round_idx]

    generated_idx = round_idx + 1
    return (
        f"这是第 {generated_idx} 轮测试，请解释亲和调度在连续多轮推理中的价值，"
        f"并补充一个与缓存复用相关的观察点。"
    )


def _build_payload(enable_affinity: bool, user_id: Optional[str], session_id: Optional[str]) -> Dict[str, Any]:
    payload = {
        "model": "mock-model",
        "messages": [
            {"role": "user", "content": "Explain affinity scheduling in one sentence."}
        ],
        "max_tokens": 8,
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "Enable_Affinity": enable_affinity,
    }
    if user_id is not None:
        payload["user_id"] = user_id
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def run_functional_tests() -> None:
    if "yaml" not in sys.modules:
        yaml_stub = types.ModuleType("yaml")
        yaml_stub.safe_load = lambda *_args, **_kwargs: {}
        sys.modules["yaml"] = yaml_stub

    from core.schemas.request import BaseRequest

    affinity_store: Dict[Any, Any] = {}
    affinity_lock = Lock()
    scheduler = MockScheduler()

    with patch("core.schemas.request.Prompt.extract_prompt_info", return_value=16):
        req1 = BaseRequest.build_request(
            url_path="/v1/chat/completions",
            payload=_build_payload(enable_affinity=False, user_id="u1", session_id="s1"),
            user_addr="127.0.0.1",
            request_id=1,
            scheduler_instance=scheduler,
            affinity_store=affinity_store,
            affinity_lock=affinity_lock,
        )
        req2 = BaseRequest.build_request(
            url_path="/v1/chat/completions",
            payload=_build_payload(enable_affinity=False, user_id="u1", session_id="s1"),
            user_addr="127.0.0.1",
            request_id=2,
            scheduler_instance=scheduler,
            affinity_store=affinity_store,
            affinity_lock=affinity_lock,
        )

        assert scheduler.calls == 2, "Enable_Affinity=False 时应每次重新调度"
        assert req1.Task.P_proxy_port != req2.Task.P_proxy_port, "关闭亲和性时不应复用调度结果"

        req3 = BaseRequest.build_request(
            url_path="/v1/chat/completions",
            payload=_build_payload(enable_affinity=True, user_id="u2", session_id="s2"),
            user_addr="127.0.0.1",
            request_id=3,
            scheduler_instance=scheduler,
            affinity_store=affinity_store,
            affinity_lock=affinity_lock,
        )
        calls_after_first_affinity = scheduler.calls

        req4 = BaseRequest.build_request(
            url_path="/v1/chat/completions",
            payload=_build_payload(enable_affinity=True, user_id="u2", session_id="s2"),
            user_addr="127.0.0.1",
            request_id=4,
            scheduler_instance=scheduler,
            affinity_store=affinity_store,
            affinity_lock=affinity_lock,
        )

        assert calls_after_first_affinity == 3, "开启亲和性后的首次请求应正常调度"
        assert scheduler.calls == 3, "开启亲和性后，同一 user_id/session_id 的后续请求应复用调度结果"
        assert req3.Task.P_proxy_addr == req4.Task.P_proxy_addr, "命中亲和性时应复用 Prefill 调度结果"
        assert req3.Task.D_proxy_addr == req4.Task.D_proxy_addr, "命中亲和性时应复用 Decode 调度结果"
        assert ("u2", "s2") in affinity_store, "首次亲和性调度后应写入 affinity_store"

        req5 = BaseRequest.build_request(
            url_path="/v1/chat/completions",
            payload=_build_payload(enable_affinity=True, user_id="u3", session_id=None),
            user_addr="127.0.0.1",
            request_id=5,
            scheduler_instance=scheduler,
            affinity_store=affinity_store,
            affinity_lock=affinity_lock,
        )
        req6 = BaseRequest.build_request(
            url_path="/v1/chat/completions",
            payload=_build_payload(enable_affinity=True, user_id="u3", session_id=None),
            user_addr="127.0.0.1",
            request_id=6,
            scheduler_instance=scheduler,
            affinity_store=affinity_store,
            affinity_lock=affinity_lock,
        )

        assert scheduler.calls == 5, "缺少 user_id/session_id 时应退化为普通调度"
        assert req5.Task.P_proxy_port != req6.Task.P_proxy_port, "标识不完整时不应复用调度结果"

    print("[PASS] 功能测试通过：Enable_Affinity 的开启、关闭、缺失标识降级逻辑符合预期。")


def _stream_ttft_once(
    url: str,
    model: str,
    prompt: str,
    enable_affinity: bool,
    user_id: str,
    session_id: str,
    max_tokens: int,
    timeout_s: float,
) -> Dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("运行 TTFT benchmark 需要先安装 requests：pip install requests") from exc

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": True,
        "Enable_Affinity": enable_affinity,
        "user_id": user_id,
        "session_id": session_id,
    }

    start = time.perf_counter()
    first_token_time = None
    response = requests.post(url, json=payload, stream=True, timeout=timeout_s)
    response.raise_for_status()

    chunks = 0
    try:
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices") or []
            if not choices:
                continue

            delta = choices[0].get("delta") or {}
            text = delta.get("content")
            if text:
                chunks += 1
                if first_token_time is None:
                    first_token_time = time.perf_counter() - start
    finally:
        response.close()

    total_time = time.perf_counter() - start
    return {
        "ttft_s": first_token_time if first_token_time is not None else total_time,
        "total_s": total_time,
        "chunks": chunks,
    }


def _mean(values: List[float]) -> float:
    return statistics.mean(values) if values else 0.0


def run_ttft_benchmark(
    url: str,
    model: str,
    rounds: int,
    max_tokens: int,
    timeout_s: float,
) -> None:
    print("[INFO] 开始 TTFT 对比测试。建议在服务空闲时运行，结果会更稳定。")

    baseline_results: List[Dict[str, Any]] = []
    affinity_results: List[Dict[str, Any]] = []

    for i in range(rounds):
        round_prompt = build_round_question(i)
        result = _stream_ttft_once(
            url=url,
            model=model,
            prompt=round_prompt,
            enable_affinity=False,
            user_id=f"baseline_user_{i}",
            session_id=f"baseline_session_{i}",
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
        baseline_results.append(result)
        print(
            f"[BASELINE {i + 1}/{rounds}] TTFT={result['ttft_s']:.4f}s "
            f"total={result['total_s']:.4f}s chunks={result['chunks']} "
            f"question={round_prompt}"
        )

    warmup_prompt = build_round_question(rounds)
    affinity_warmup = _stream_ttft_once(
        url=url,
        model=model,
        prompt=warmup_prompt,
        enable_affinity=True,
        user_id="affinity_user",
        session_id="affinity_session",
        max_tokens=max_tokens,
        timeout_s=timeout_s,
    )
    print(
        f"[AFFINITY WARMUP] TTFT={affinity_warmup['ttft_s']:.4f}s "
        f"total={affinity_warmup['total_s']:.4f}s chunks={affinity_warmup['chunks']} "
        f"question={warmup_prompt}"
    )

    for i in range(rounds):
        round_prompt = build_round_question(rounds + 1 + i)
        result = _stream_ttft_once(
            url=url,
            model=model,
            prompt=round_prompt,
            enable_affinity=True,
            user_id="affinity_user",
            session_id="affinity_session",
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
        affinity_results.append(result)
        print(
            f"[AFFINITY HIT {i + 1}/{rounds}] TTFT={result['ttft_s']:.4f}s "
            f"total={result['total_s']:.4f}s chunks={result['chunks']} "
            f"question={round_prompt}"
        )

    baseline_ttft = [item["ttft_s"] for item in baseline_results]
    affinity_ttft = [item["ttft_s"] for item in affinity_results]

    baseline_avg = _mean(baseline_ttft)
    affinity_avg = _mean(affinity_ttft)
    improvement = baseline_avg - affinity_avg
    improvement_ratio = (improvement / baseline_avg * 100.0) if baseline_avg > 0 else 0.0

    print("\n================ TTFT Benchmark Summary ================")
    print(f"URL: {url}")
    print(f"Model: {model}")
    print(f"Rounds: {rounds}")
    print(f"Baseline avg TTFT (affinity off): {baseline_avg:.4f}s")
    print(f"Affinity avg TTFT (hit path):     {affinity_avg:.4f}s")
    print(f"TTFT delta:                       {improvement:.4f}s")
    print(f"TTFT improvement:                 {improvement_ratio:.2f}%")
    print("说明：Affinity 结果不包含首次 warmup 请求，只统计命中后的后续请求。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="测试亲和性调度功能正确性，并对比 Enable_Affinity 开关前后的 TTFT 差异。"
    )
    parser.add_argument(
        "--url",
        default="http://172.18.0.170:5000/v1/chat/completions",
        help="真实服务的 chat/completions 地址",
    )
    parser.add_argument(
        "--model",
        default="/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        help="压测时使用的模型字段",
    )
    parser.add_argument(
        "--prompt",
        default="请用一小段话解释什么是会话亲和性调度。",
        help="保留的兼容参数；当前脚本会为每个 round 自动生成不同问题",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=5,
        help="TTFT 对比轮数，不含 affinity 首次 warmup 请求",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=32,
        help="TTFT 对比测试的 max_tokens",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="单次 HTTP 请求超时时间（秒）",
    )
    parser.add_argument(
        "--skip-benchmark",
        action="store_true",
        help="只跑功能测试，不跑真实服务 TTFT 对比",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_functional_tests()

    if args.skip_benchmark:
        print("[INFO] 已跳过 TTFT benchmark。")
        return

    run_ttft_benchmark(
        url=args.url,
        model=args.model,
        rounds=args.rounds,
        max_tokens=args.max_tokens,
        timeout_s=args.timeout,
    )


if __name__ == "__main__":
    main()
