from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from ..experiment.pipeline import DEFAULT_LATEST_RESULT_PATH
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from simulator.experiment.pipeline import DEFAULT_LATEST_RESULT_PATH


DEFAULT_LATEST_SCHEDULE_LOG_PATH = Path(__file__).resolve().parent / "latest_schedule_log.md"


def render_schedule_log(result: dict, strategy: str = "pso") -> str:
    runs = result.get("runs", {})
    if strategy not in runs:
        return f"# Simulator Schedule Log\n\nStrategy `{strategy}` not found in result.\n"

    run = runs[strategy]
    lines = [
        "# Simulator Schedule Log",
        "",
        "## Summary",
        "",
        f"- seed: {result.get('seed')}",
        f"- num_requests: {result.get('num_requests')}",
        f"- arrival: {result.get('arrival')}",
        f"- prompt_tokens: {result.get('prompt_tokens')}",
        f"- slo: {result.get('slo')}",
        f"- strategy: {strategy}",
        f"- summary: {run.get('summary')}",
    ]
    if "comparison" in result:
        lines.append(f"- comparison: {result['comparison']}")

    lines.extend(["", "## PSO Rolling Dispatch", ""])
    queue_result = run.get("queue_result", {})
    dispatch_records = queue_result.get("dispatch_records", [])
    if not dispatch_records:
        lines.append("_No dispatch records._")
    for record in dispatch_records:
        request_ids = ", ".join(record.get("request_ids", []))
        lines.extend(
            [
                f"### Dispatch {record.get('dispatch_order')} - {record.get('role', 'unknown')}",
                "",
                f"- dispatch_time_ms: {_fmt(record.get('dispatch_time_ms'))}",
                f"- planned_start_ms: {_fmt(record.get('planned_start_ms'))}",
                f"- planned_finish_ms: {_fmt(record.get('planned_finish_ms'))}",
                f"- actual_start_ms: {_fmt(record.get('actual_start_ms'))}",
                f"- actual_finish_ms: {_fmt(record.get('actual_finish_ms'))}",
                f"- total_tokens: {record.get('total_tokens')}",
                f"- request_ids: {request_ids}",
                "",
            ]
        )

    lines.extend(["## PSO Request Outcomes", ""])
    for request in run.get("requests", []):
        lines.append(
            "- "
            f"{request.get('request_id')}: "
            f"arrival={_fmt(request.get('original_arrival_ms'))}ms, "
            f"dispatch={_fmt(request.get('dispatch_time_ms'))}ms, "
            f"tokens={request.get('prompt_tokens')}, "
            f"slo={_fmt(request.get('slo_ttft_ms'))}ms, "
            f"complete={_fmt(request.get('complete_time_ms'))}ms, "
            f"latency={_fmt(request.get('ttft_ms'))}ms, "
            f"met_slo={request.get('met_slo')}"
        )

    lines.extend(["", "## vLLM Batches", ""])
    for batch in run.get("vllm_result", {}).get("batches", []):
        chunk_text = "; ".join(
            (
                f"{chunk.get('request_id')} "
                f"{chunk.get('tokens')}t "
                f"completed={chunk.get('completed')}"
            )
            for chunk in batch.get("chunks", [])
        )
        lines.extend(
            [
                f"### vLLM Batch {batch.get('batch_id')}",
                "",
                f"- start_ms: {_fmt(batch.get('start_ms'))}",
                f"- end_ms: {_fmt(batch.get('end_ms'))}",
                f"- scheduled_tokens: {batch.get('scheduled_tokens')}",
                f"- chunks: {chunk_text}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def save_schedule_log(
    result: dict,
    output_path: Path = DEFAULT_LATEST_SCHEDULE_LOG_PATH,
    strategy: str = "pso",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_schedule_log(result, strategy=strategy), encoding="utf-8")
    return output_path


def load_result(path: Path = DEFAULT_LATEST_RESULT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.3f}"
    return str(value)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render simulator schedule log as Markdown.")
    parser.add_argument("--input", type=Path, default=DEFAULT_LATEST_RESULT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_LATEST_SCHEDULE_LOG_PATH)
    parser.add_argument("--strategy", choices=("direct", "pso"), default="pso")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    path = save_schedule_log(load_result(args.input), args.output, strategy=args.strategy)
    print(f"Saved schedule log to: {path}")


if __name__ == "__main__":
    main()
