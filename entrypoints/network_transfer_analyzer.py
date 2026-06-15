import argparse
import json
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from utils.kv_transfer import QWEN25_7B_KV_BYTES_PER_TOKEN

try:
    from transformers import AutoTokenizer
except ImportError:
    AutoTokenizer = None  # type: ignore


DEFAULT_PREFILL_GATEWAY = "http://172.18.0.170:8081"
DEFAULT_MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
DEFAULT_LENGTHS = ""
DEFAULT_RANGE_START = 128
DEFAULT_RANGE_STOP = 2048
DEFAULT_RANGE_STEP = 128


@lru_cache(maxsize=4)
def _load_tokenizer(model: str):
    if AutoTokenizer is None:
        raise RuntimeError("transformers is required for exact token-length prompt generation.")
    is_local = os.path.isdir(model)
    return AutoTokenizer.from_pretrained(
        model,
        trust_remote_code=True,
        use_fast=True,
        local_files_only=is_local,
    )


def _parse_lengths(raw: str) -> List[int]:
    values: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value <= 0:
            raise ValueError("lengths must be positive integers")
        values.append(value)
    if not values:
        raise ValueError("at least one length is required")
    return values


def _default_range_lengths() -> List[int]:
    if DEFAULT_RANGE_START <= 0 or DEFAULT_RANGE_STOP <= 0 or DEFAULT_RANGE_STEP <= 0:
        raise ValueError("DEFAULT_RANGE_START/STOP/STEP must be positive")
    if DEFAULT_RANGE_START > DEFAULT_RANGE_STOP:
        raise ValueError("DEFAULT_RANGE_START must be <= DEFAULT_RANGE_STOP")
    return list(range(DEFAULT_RANGE_START, DEFAULT_RANGE_STOP + 1, DEFAULT_RANGE_STEP))


def _resolve_lengths(raw: str) -> List[int]:
    raw = (raw or "").strip()
    if raw:
        return _parse_lengths(raw)
    return _default_range_lengths()


def _build_prompt(target_length: int, model: str) -> str:
    if target_length <= 0:
        raise ValueError("target token length must be positive")

    tokenizer = _load_tokenizer(model)
    prefix = f"probe_uuid={uuid.uuid4()}\n"
    unit = "network transfer analysis probe "
    text = prefix + unit
    token_ids = tokenizer.encode(text, add_special_tokens=False)

    grow_limit = max(16, target_length * 4)
    grow_count = 0
    while len(token_ids) < target_length and grow_count < grow_limit:
        text += unit
        token_ids = tokenizer.encode(text, add_special_tokens=False)
        grow_count += 1

    if len(token_ids) < target_length:
        raise RuntimeError(
            f"Tokenizer failed to produce enough tokens: target={target_length}, actual={len(token_ids)}"
        )

    prompt = tokenizer.decode(token_ids[:target_length], skip_special_tokens=True)
    actual_len = len(tokenizer.encode(prompt, add_special_tokens=False))

    # Some tokenizers are not perfectly stable under decode -> encode around
    # token boundaries. Repair with a bounded loop so target_len remains exact.
    for _ in range(16):
        if actual_len == target_length:
            return prompt
        if actual_len < target_length:
            prompt += unit
            token_ids = tokenizer.encode(prompt, add_special_tokens=False)
            prompt = tokenizer.decode(token_ids[:target_length], skip_special_tokens=True)
        else:
            token_ids = tokenizer.encode(prompt, add_special_tokens=False)
            prompt = tokenizer.decode(token_ids[:target_length], skip_special_tokens=True)
        actual_len = len(tokenizer.encode(prompt, add_special_tokens=False))

    raise RuntimeError(
        f"Failed to build exact prompt length: target={target_length}, actual={actual_len}"
    )


def _kv_bytes(prompt_tokens: int) -> float:
    return max(0, int(prompt_tokens)) * QWEN25_7B_KV_BYTES_PER_TOKEN


def _effective_bandwidth_mbps(kv_bytes: float, observed_ms: float) -> Optional[float]:
    if observed_ms <= 0:
        return None
    return (kv_bytes * 8.0) / (observed_ms * 1000.0)


def _fmt_float(value: Optional[float], width: int, digits: int) -> str:
    if value is None:
        return f"{'N/A':>{width}}"
    return f"{value:>{width}.{digits}f}"


def _offset_ms(value_s: Optional[float], base_s: Optional[float]) -> Optional[float]:
    if value_s is None or base_s is None:
        return None
    return (float(value_s) - float(base_s)) * 1000.0


def _request_once(
    endpoint: str,
    model: str,
    prompt: str,
    request_id: str,
    timeout_s: float,
) -> Dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "request_id": request_id,
        "timeout_s": timeout_s,
    }
    response = requests.post(endpoint, json=payload, timeout=timeout_s)
    response.raise_for_status()
    data = response.json()
    data["success"] = bool(data.get("success", False))
    return data


def _build_probe_tasks(
    lengths: List[int],
    repeat: int,
    model: str,
    concurrency: int,
) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    seq = 0
    for target_length in lengths:
        for repeat_idx in range(repeat):
            seq += 1
            request_id = f"nta-{target_length}-{repeat_idx + 1}-{seq}-{int(time.time() * 1000)}"
            tasks.append(
                {
                    "sequence": seq,
                    "request_id": request_id,
                    "target_length": target_length,
                    "repeat_index": repeat_idx + 1,
                    "repeat_total": repeat,
                    "concurrency": concurrency,
                    "prompt": _build_prompt(target_length, model),
                    "batch_id": ((seq - 1) // concurrency) + 1,
                    "batch_index": ((seq - 1) % concurrency) + 1,
                }
            )
    for task in tasks:
        batch_start = ((int(task["batch_id"]) - 1) * concurrency) + 1
        batch_end = min(batch_start + concurrency - 1, len(tasks))
        task["batch_size"] = batch_end - batch_start + 1
    return tasks


def _iter_batches(tasks: List[Dict[str, Any]], batch_size: int) -> List[List[Dict[str, Any]]]:
    return [tasks[idx : idx + batch_size] for idx in range(0, len(tasks), batch_size)]


def _run_probe_task(
    task: Dict[str, Any],
    endpoint: str,
    model: str,
    timeout_s: float,
    batch_start_time_s: float,
) -> Dict[str, Any]:
    started_at = time.time()
    try:
        result = _request_once(
            endpoint=endpoint,
            model=model,
            prompt=str(task["prompt"]),
            request_id=str(task["request_id"]),
            timeout_s=timeout_s,
        )
        result["success"] = bool(result.get("success", False))
    except Exception as exc:
        result = {
            "success": False,
            "request_id": task["request_id"],
            "error": str(exc),
        }

    finished_at = time.time()
    result["sequence"] = task["sequence"]
    result["target_length"] = task["target_length"]
    result["repeat_index"] = task["repeat_index"]
    result["repeat_total"] = task["repeat_total"]
    result["concurrency"] = task["concurrency"]
    result["batch_id"] = task["batch_id"]
    result["batch_index"] = task["batch_index"]
    result["batch_size"] = task["batch_size"]
    result["batch_start_time_s"] = batch_start_time_s
    result["client_start_time_s"] = started_at
    result["client_end_time_s"] = finished_at
    result["client_start_offset_ms"] = (started_at - batch_start_time_s) * 1000.0
    result["client_end_offset_ms"] = (finished_at - batch_start_time_s) * 1000.0
    result["client_elapsed_ms"] = (finished_at - started_at) * 1000.0
    result["request_elapsed_ms"] = result["client_elapsed_ms"]
    if result.get("success"):
        result["kv_bytes"] = _kv_bytes(int(result.get("prompt_tokens", 0) or 0))
    return result


def _print_samples(samples: List[Dict[str, Any]]) -> None:
    print("\nNetwork Transfer Analysis")
    print("=" * 176)
    print(
        f"{'seq':>4} {'batch':>5} {'in_batch':>8} {'req':<24} {'conc':>5} "
        f"{'target_len':>10} {'tokens':>8} {'kv_MB':>12} {'observed_ms':>14} "
        f"{'start_off_ms':>14} {'prefill_done_off_ms':>20} {'end_off_ms':>12} "
        f"{'request_elapsed_ms':>18} {'effective_bw_mbps':>20}"
    )
    print("-" * 176)

    bandwidths: List[float] = []

    for sample in samples:
        if not sample.get("success"):
            print(
                f"{sample.get('sequence', 0):>4} {sample.get('batch_id', 0):>5} "
                f"{sample.get('batch_index', 0):>3}/{sample.get('batch_size', 0):<4} "
                f"{sample.get('request_id', ''):<24} {sample.get('concurrency', 0):>5} "
                f"{sample.get('target_length', 0):>10} {'ERR':>8} {'ERR':>12} "
                f"{'ERR':>14} {'ERR':>20} {'ERR':>12} {'ERR':>18} {'ERR':>20}"
            )
            continue

        observed_ms = float(sample["observed_transfer_time_ms"])
        kv_bytes = float(sample["kv_bytes"])
        kv_mb = kv_bytes / (1024.0 * 1024.0)
        client_start_offset_ms = float(sample.get("client_start_offset_ms", 0.0))
        prefill_done_offset_ms = _offset_ms(
            sample.get("prefill_first_chunk_abs_time_s"),
            sample.get("batch_start_time_s"),
        )
        client_end_offset_ms = float(sample.get("client_end_offset_ms", 0.0))
        request_elapsed_ms = float(sample.get("request_elapsed_ms", 0.0))
        effective_bw_mbps = sample.get("effective_bandwidth_mbps")
        if effective_bw_mbps is not None:
            bandwidths.append(float(effective_bw_mbps))

        print(
            f"{sample['sequence']:>4} {sample['batch_id']:>5} "
            f"{sample['batch_index']:>3}/{sample['batch_size']:<4} "
            f"{sample['request_id']:<24} {sample['concurrency']:>5} "
            f"{sample['target_length']:>10} {sample['prompt_tokens']:>8} "
            f"{kv_mb:>12.3f} {observed_ms:>14.3f} "
            f"{client_start_offset_ms:>14.3f} {_fmt_float(prefill_done_offset_ms, 20, 3)} "
            f"{client_end_offset_ms:>12.3f} "
            f"{request_elapsed_ms:>18.3f} "
            f"{_fmt_float(effective_bw_mbps, 20, 3)}"
        )

    print("=" * 176)
    if bandwidths:
        print(f"Effective bandwidth avg(Mbps): {statistics.mean(bandwidths):.3f}")
        print(f"Effective bandwidth median(Mbps): {statistics.median(bandwidths):.3f}")
    else:
        print("Effective bandwidth avg(Mbps): N/A")
        print("Effective bandwidth median(Mbps): N/A")


def main() -> None:
    parser = argparse.ArgumentParser("FlexPD network transfer analyzer")
    parser.add_argument("--prefill-gateway", default=DEFAULT_PREFILL_GATEWAY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--lengths", default=DEFAULT_LENGTHS)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--json-output", default="")
    args = parser.parse_args()

    if args.repeat <= 0:
        raise ValueError("--repeat must be positive")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be positive")

    lengths = _resolve_lengths(args.lengths)
    endpoint = args.prefill_gateway.rstrip("/") + "/analysis/network-transfer"
    tasks = _build_probe_tasks(lengths, args.repeat, args.model, args.concurrency)
    samples: List[Dict[str, Any]] = []

    batches = _iter_batches(tasks, args.concurrency)
    print(f"Prepared {len(tasks)} probes, concurrency={args.concurrency}, batches={len(batches)}")
    for batch in batches:
        batch_id = int(batch[0]["batch_id"])
        print(f"Starting batch {batch_id}/{len(batches)}, size={len(batch)}")
        batch_start_time_s = time.time()
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            future_to_task = {
                executor.submit(
                    _run_probe_task,
                    task,
                    endpoint,
                    args.model,
                    args.timeout_s,
                    batch_start_time_s,
                ): task
                for task in batch
            }
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    sample = future.result()
                except Exception as exc:
                    sample = {
                        "success": False,
                        "request_id": task["request_id"],
                        "sequence": task["sequence"],
                        "target_length": task["target_length"],
                        "repeat_index": task["repeat_index"],
                        "repeat_total": task["repeat_total"],
                        "concurrency": task["concurrency"],
                        "batch_id": task["batch_id"],
                        "batch_index": task["batch_index"],
                        "batch_size": task["batch_size"],
                        "batch_start_time_s": batch_start_time_s,
                        "error": str(exc),
                    }
                samples.append(sample)
                status = "OK" if sample.get("success") else "FAIL"
                print(
                    f"[batch {sample.get('batch_id')} {sample.get('batch_index')}/"
                    f"{sample.get('batch_size')}] {status} seq={sample.get('sequence')}/"
                    f"{len(tasks)} target_length={sample.get('target_length')}, repeat="
                    f"{sample.get('repeat_index')}/{sample.get('repeat_total')}"
                )

    for sample in samples:
        if not sample.get("success"):
            continue
        kv_bytes = float(sample["kv_bytes"])
        observed_ms = float(sample["observed_transfer_time_ms"])
        sample["effective_bandwidth_mbps"] = _effective_bandwidth_mbps(kv_bytes, observed_ms)

    samples.sort(key=lambda item: int(item.get("sequence", 0)))
    _print_samples(samples)

    if args.json_output:
        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON output written to: {output_path}")


if __name__ == "__main__":
    main()
