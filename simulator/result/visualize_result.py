from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

try:
    from ..experiment.pipeline import DEFAULT_LATEST_RESULT_PATH
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from simulator.experiment.pipeline import DEFAULT_LATEST_RESULT_PATH


DEFAULT_LATEST_FIGURE_PATH = Path(__file__).resolve().parent / "latest_result.svg"
DEFAULT_FIGURE_STRATEGIES = ("pso", "direct")


def render_strategy_svg(
    result: dict,
    strategy: str,
    max_requests: int = 20,
) -> str:
    if strategy not in result.get("runs", {}):
        raise ValueError(f"strategy {strategy!r} not found in result")

    run = result["runs"][strategy]
    requests = run["requests"][:max_requests]
    batches = run["vllm_result"]["batches"]
    summary = run["summary"]
    max_end_ms = max((batch["end_ms"] for batch in batches), default=1.0)

    width = 1200
    margin_x = 52
    timeline_y = 72
    timeline_h = 72
    table_y = 190
    col_w = 148
    label_w = 190
    row_h = 64
    table_rows = 5
    visible_count = max(1, len(requests))
    table_w = label_w + col_w * visible_count
    svg_w = max(width, table_w + margin_x * 2)
    svg_h = table_y + row_h * table_rows + 42
    axis_end_x = svg_w - margin_x
    timeline_w = axis_end_x - margin_x

    colors = [
        "#e8f4f8",
        "#eef3ff",
        "#f8eee5",
        "#edf7ed",
        "#f7eff8",
        "#fff5d9",
        "#eaf7f4",
        "#f3f3f3",
    ]

    request_index = {req["request_id"]: idx for idx, req in enumerate(requests)}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">',
        "<style>",
        "text{font-family:Arial,'Microsoft YaHei',sans-serif;fill:#202124}",
        ".title{font-size:24px;font-weight:700}",
        ".small{font-size:14px;fill:#555}",
        ".axis{stroke:#202124;stroke-width:2}",
        ".box{stroke:#1f2744;stroke-width:2}",
        ".grid{stroke:#a5a7ad;stroke-width:1.5;fill:#fff}",
        ".cell{font-size:22px}",
        ".no{fill:#b31412}",
        ".yes{fill:#137333}",
        "</style>",
        f'<text class="title" x="{margin_x}" y="34">{html.escape(strategy)} execution timeline</text>',
        (
            f'<text class="small" x="{margin_x + 320}" y="34">'
            f'goodput={summary["goodput_tokens"]}/{summary["total_prompt_tokens"]} tokens, '
            f'SLO={summary["met_slo_requests"]}/{summary["total_requests"]}'
            "</text>"
        ),
        _axis_svg(margin_x, timeline_y, axis_end_x),
    ]

    for batch in batches:
        x = margin_x + (batch["start_ms"] / max_end_ms) * timeline_w
        w = max(2.0, ((batch["end_ms"] - batch["start_ms"]) / max_end_ms) * timeline_w)
        chunk_labels = []
        color_idx = batch["batch_id"] % len(colors)
        for chunk in batch["chunks"]:
            rid = chunk["request_id"]
            token_label = rid
            if not chunk["completed"]:
                token_label += "+"
            chunk_labels.append(token_label)
            if rid in request_index:
                color_idx = request_index[rid] % len(colors)
        label = ", ".join(chunk_labels)
        if len(label) > 42:
            label = label[:39] + "..."
        parts.append(
            f'<rect class="box" x="{x:.2f}" y="{timeline_y}" width="{w:.2f}" height="{timeline_h}" fill="{colors[color_idx]}"/>'
        )
        if w >= 40:
            parts.append(
                f'<text class="cell" x="{x + w / 2:.2f}" y="{timeline_y + 44}" text-anchor="middle">'
                f'{html.escape(label)}</text>'
            )

    parts.extend(_table_svg(requests, table_y, margin_x, label_w, col_w, row_h))
    parts.append("</svg>")
    return "\n".join(parts)


def save_strategy_svg(
    result: dict,
    strategy: str,
    output_path: Path,
    max_requests: int = 20,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_strategy_svg(result, strategy, max_requests=max_requests),
        encoding="utf-8",
    )
    return output_path


def save_strategy_svgs(
    result: dict,
    strategies: Iterable[str] = DEFAULT_FIGURE_STRATEGIES,
    output_path: Optional[Path] = None,
    max_requests: int = 20,
) -> list[Path]:
    base = output_path or DEFAULT_LATEST_FIGURE_PATH
    paths = []
    strategy_list = list(strategies)
    for index, strategy in enumerate(strategy_list):
        if len(strategy_list) == 1:
            path = base
        elif index == 0:
            path = base
        else:
            path = base.with_name(f"{base.stem}_{strategy}{base.suffix}")
        paths.append(save_strategy_svg(result, strategy, path, max_requests=max_requests))
    return paths


def load_result(path: Path = DEFAULT_LATEST_RESULT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _axis_svg(start_x: float, y: float, end_x: float) -> str:
    return "\n".join(
        [
            f'<line class="axis" x1="{start_x}" y1="{y + 72}" x2="{end_x + 30}" y2="{y + 72}"/>',
            f'<line class="axis" x1="{start_x}" y1="{y - 30}" x2="{start_x}" y2="{y + 90}"/>',
            f'<polygon points="{end_x + 30},{y + 72} {end_x + 14},{y + 64} {end_x + 14},{y + 80}" fill="#202124"/>',
            f'<polygon points="{start_x},{y - 30} {start_x - 8},{y - 14} {start_x + 8},{y - 14}" fill="#202124"/>',
            f'<text class="small" x="{end_x + 36}" y="{y + 78}">time</text>',
        ]
    )


def _table_svg(
    requests: list[dict],
    table_y: float,
    start_x: float,
    label_w: float,
    col_w: float,
    row_h: float,
) -> list[str]:
    rows = ["request ID", "prompt tokens", "SLO", "actual latency", "SLO met?"]
    row_count = len(rows)
    table_w = label_w + col_w * max(1, len(requests))
    parts = [
        f'<rect class="grid" x="{start_x}" y="{table_y}" width="{table_w}" height="{row_h * row_count}"/>'
    ]
    for i in range(1, row_count):
        y = table_y + row_h * i
        parts.append(f'<line class="grid" x1="{start_x}" y1="{y}" x2="{start_x + table_w}" y2="{y}"/>')
    parts.append(
        f'<line class="grid" x1="{start_x + label_w}" y1="{table_y}" x2="{start_x + label_w}" y2="{table_y + row_h * row_count}"/>'
    )
    for idx in range(1, len(requests)):
        x = start_x + label_w + col_w * idx
        parts.append(f'<line class="grid" x1="{x}" y1="{table_y}" x2="{x}" y2="{table_y + row_h * row_count}"/>')

    for row_idx, row_name in enumerate(rows):
        parts.append(
            f'<text class="cell" x="{start_x + label_w / 2}" y="{table_y + row_h * row_idx + 40}" text-anchor="middle">'
            f'{html.escape(row_name)}</text>'
        )

    for col_idx, req in enumerate(requests):
        x = start_x + label_w + col_w * col_idx + col_w / 2
        parts.append(
            f'<text class="cell" x="{x}" y="{table_y + 40}" text-anchor="middle">{html.escape(req["request_id"])}</text>'
        )
        parts.append(
            f'<text class="cell" x="{x}" y="{table_y + row_h + 40}" text-anchor="middle">{req["prompt_tokens"]}</text>'
        )
        parts.append(
            f'<text class="cell" x="{x}" y="{table_y + row_h * 2 + 40}" text-anchor="middle">{req["slo_ttft_ms"]:.1f}ms</text>'
        )
        parts.append(
            f'<text class="cell" x="{x}" y="{table_y + row_h * 3 + 40}" text-anchor="middle">{req["ttft_ms"]:.1f}ms</text>'
        )
        met = "yes" if req["met_slo"] else "no"
        cls = "yes" if req["met_slo"] else "no"
        parts.append(
            f'<text class="cell {cls}" x="{x}" y="{table_y + row_h * 4 + 40}" text-anchor="middle">{met}</text>'
        )
    return parts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render simulator results as SVG timeline charts.")
    parser.add_argument("--input", type=Path, default=DEFAULT_LATEST_RESULT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_LATEST_FIGURE_PATH)
    parser.add_argument("--strategy", choices=("direct", "pso", "both"), default="both")
    parser.add_argument("--max-requests", type=int, default=50)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = load_result(args.input)
    strategies = DEFAULT_FIGURE_STRATEGIES if args.strategy == "both" else (args.strategy,)
    paths = save_strategy_svgs(
        result,
        strategies=strategies,
        output_path=args.output,
        max_requests=args.max_requests,
    )
    for path in paths:
        print(f"Saved SVG figure to: {path}")


if __name__ == "__main__":
    main()
