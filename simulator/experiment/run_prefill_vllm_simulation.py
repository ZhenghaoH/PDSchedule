from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from .pipeline import DEFAULT_LATEST_RESULT_PATH, run_comparison, save_latest_result
    from ..dataset.request_workload import DEFAULT_BURSTGPT_CSV_PATH
    from ..result.schedule_log import DEFAULT_LATEST_SCHEDULE_LOG_PATH, save_schedule_log
    from ..instance.ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH
    from ..result.visualize_result import (
        DEFAULT_FIGURE_STRATEGIES,
        DEFAULT_LATEST_FIGURE_PATH,
        save_strategy_svgs,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from simulator.experiment.pipeline import DEFAULT_LATEST_RESULT_PATH, run_comparison, save_latest_result
    from simulator.dataset.request_workload import DEFAULT_BURSTGPT_CSV_PATH
    from simulator.result.schedule_log import DEFAULT_LATEST_SCHEDULE_LOG_PATH, save_schedule_log
    from simulator.instance.ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH
    from simulator.result.visualize_result import (
        DEFAULT_FIGURE_STRATEGIES,
        DEFAULT_LATEST_FIGURE_PATH,
        save_strategy_svgs,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Direct/FCFS and Prefill PSO vLLM simulations.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--num-requests", type=int, default=218)
    parser.add_argument("--arrival-mode", choices=("all_at_once", "qps"), default="qps")
    parser.add_argument("--qps", type=int, default=20)
    parser.add_argument("--prompt-token-source", choices=("burstgpt", "synthetic"), default="burstgpt")
    parser.add_argument("--burstgpt-csv", type=Path, default=DEFAULT_BURSTGPT_CSV_PATH)
    parser.add_argument("--min-slo-ttft-ms", type=float, default=1500.0)
    parser.add_argument("--max-slo-ttft-ms", type=float, default=1500.0)
    parser.add_argument("--strategy", choices=("direct", "pso", "both"), default="both")
    parser.add_argument("--max-num-batched-tokens", type=int, default=4096)
    parser.add_argument("--arrival-window-ms", type=float, default=20.0)
    parser.add_argument("--token-time-ms", type=float, default=0.01)
    parser.add_argument("--batch-overhead-ms", type=float, default=0.0)
    parser.add_argument("--ttft-model", type=Path, default=DEFAULT_PREFILL_TTFT_MODEL_PATH)
    parser.add_argument("--idle-batching", choices=("first", "same_time", "window"), default="first")
    parser.add_argument("--pso-particles", type=int, default=30)
    parser.add_argument("--pso-iterations", type=int, default=20)
    parser.add_argument("--pso-random-seed", type=int)
    parser.add_argument("--indent", type=int, default=2)
    parser.add_argument("--output", type=Path, default=DEFAULT_LATEST_RESULT_PATH)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--save-figure", action="store_true")
    parser.add_argument("--figure-output", type=Path, default=DEFAULT_LATEST_FIGURE_PATH)
    parser.add_argument("--figure-strategy", choices=("direct", "pso", "both"), default="both")
    parser.add_argument("--max-figure-requests", type=int, default=20)
    parser.add_argument("--no-save-schedule-log", action="store_true")
    parser.add_argument("--schedule-log-output", type=Path, default=DEFAULT_LATEST_SCHEDULE_LOG_PATH)
    parser.add_argument("--schedule-log-strategy", choices=("direct", "pso"), default="pso")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = run_comparison(
        seed=args.seed,
        num_requests=args.num_requests,
        arrival_mode=args.arrival_mode,
        qps=args.qps,
        prompt_token_source=args.prompt_token_source,
        burstgpt_csv_path=args.burstgpt_csv,
        min_slo_ttft_ms=args.min_slo_ttft_ms,
        max_slo_ttft_ms=args.max_slo_ttft_ms,
        strategy=args.strategy,
        max_num_batched_tokens=args.max_num_batched_tokens,
        arrival_window_ms=args.arrival_window_ms,
        token_time_ms=args.token_time_ms,
        batch_overhead_ms=args.batch_overhead_ms,
        idle_batching=args.idle_batching,
        ttft_model_path=args.ttft_model,
        pso_particles=args.pso_particles,
        pso_iterations=args.pso_iterations,
        pso_random_seed=args.pso_random_seed,
    )
    if not args.no_save:
        output_path = save_latest_result(result, args.output, indent=args.indent)
        print(f"Saved latest simulation result to: {output_path}")

    if args.save_figure:
        figure_strategies = (
            DEFAULT_FIGURE_STRATEGIES
            if args.figure_strategy == "both"
            else (args.figure_strategy,)
        )
        figure_paths = save_strategy_svgs(
            result,
            strategies=figure_strategies,
            output_path=args.figure_output,
            max_requests=args.max_figure_requests,
        )
        for figure_path in figure_paths:
            print(f"Saved SVG figure to: {figure_path}")

    if not args.no_save_schedule_log:
        log_path = save_schedule_log(
            result,
            output_path=args.schedule_log_output,
            strategy=args.schedule_log_strategy,
        )
        print(f"Saved schedule log to: {log_path}")

    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=args.indent))
    else:
        print(json.dumps(_build_summary_output(result), ensure_ascii=False, indent=args.indent))


def _build_summary_output(result: dict) -> dict:
    summary = {
        "seed": result["seed"],
        "num_requests": result["num_requests"],
        "runs": {
            name: run["summary"]
            for name, run in result["runs"].items()
        },
    }
    if "comparison" in result:
        summary["comparison"] = result["comparison"]
    return summary


if __name__ == "__main__":
    main()
