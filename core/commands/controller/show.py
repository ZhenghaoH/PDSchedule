import math
import unicodedata
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from store.controller.pool_manager import PoolManager


def do_show_pool_id():
    """FIXME: 以后用更好的方式展示 池id 和 池IP地址 的关系"""
    manager = PoolManager()
    manager.show_pool_id()


def _display_width(text: str) -> int:
    """按终端显示宽度计（东亚宽字符占 2 列）。"""
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [_display_width(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], _display_width(cell))
    separator = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    print(separator)
    print("| " + " | ".join(_pad(header, widths[i]) for i, header in enumerate(headers)) + " |")
    print(separator)
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            if index == 0:
                cells.append(_pad(cell, widths[index]))
            else:
                cells.append(cell.rjust(widths[index]))
        print("| " + " | ".join(cells) + " |")
    print(separator)


def _aggregate(
    records,
    field: str,
    token_field: str,
) -> Tuple[List[Tuple[str, int, int]], int]:
    totals: Dict[str, int] = {}
    counts: Counter = Counter()
    for record in records:
        instance = getattr(record, field, "") or ""
        if not instance:
            continue
        tokens = getattr(record, token_field, None) or 0
        totals[instance] = totals.get(instance, 0) + int(tokens)
        counts[instance] += 1
    rows = [
        (instance, totals.get(instance, 0), counts[instance])
        for instance in totals
    ]
    # 按实例地址升序，便于与启动脚本里的端口表逐行核对。
    rows.sort(key=lambda item: item[0])
    return rows, len(rows)


def _window_elapsed(records) -> Optional[float]:
    """窗口耗时：首条接收时点 → 末条（接收序号最大）首次终态时点。

    终点按接收编号取，不按完成顺序另选一条；末条未结束或时点无效时返回 None。
    """
    start = records[0].received_at
    end = records[-1].finished_at
    if start <= 0 or end is None:
        return None
    elapsed = end - start
    return elapsed if elapsed > 0 else None


def _format_throughput(tokens: int, elapsed: Optional[float]) -> str:
    if elapsed is None:
        return "N/A"
    return f"{tokens / elapsed:,.1f}"


def _token_stats(
    values: Sequence[float],
) -> Tuple[int, Optional[float], Optional[float], Optional[float]]:
    """实例级 token 总量：实例数、均值、总体标准差 σ、相对标准差（%）。"""
    n = len(values)
    if n == 0:
        return 0, None, None, None
    mean = sum(values) / n
    sigma = math.sqrt(sum((value - mean) ** 2 for value in values) / n)
    cv = None if mean <= 0 else sigma / mean * 100.0
    return n, mean, sigma, cv


def _mean_text(mean: Optional[float]) -> str:
    return "N/A" if mean is None else f"{mean:,.0f}"


def _cv_text(cv: Optional[float]) -> str:
    return "N/A" if cv is None else f"{cv:.2f}%"


def _print_group_stats(
    group: str,
    token_label: str,
    rows,
    tentative: bool,
) -> None:
    """表后打印该组实例数、token 均值与相对标准差。"""
    values = [float(tokens) for _, tokens, _ in rows]
    n, mean, _sigma, cv = _token_stats(values)
    suffix = "（暂计）" if tentative else ""
    print(
        f"  {group} 实例：{n} 个，{token_label}均值：{_mean_text(mean)}，"
        f"相对标准差：{_cv_text(cv)}{suffix}"
    )


def _print_summary(prefill_rows, decode_rows, throughput_elapsed: Optional[float]) -> None:
    """打印合并口径的均值、两侧相对标准差均值与分组平均吞吐量。"""
    prefill_values = [float(tokens) for _, tokens, _ in prefill_rows]
    decode_values = [float(tokens) for _, tokens, _ in decode_rows]
    n_prefill, mean_prefill, _sigma_prefill, cv_prefill = _token_stats(
        prefill_values
    )
    n_decode, mean_decode, _sigma_decode, cv_decode = _token_stats(decode_values)
    # 总 token 均值仍按合并后的各实例 token 数算。
    n_total, mean_total, _sigma_total, _cv_total = _token_stats(
        prefill_values + decode_values
    )
    # 两侧相对标准差均值取 P、D 两侧相对标准差的算术平均；只有一侧有值时取该侧。
    side_cvs = [cv for cv in (cv_prefill, cv_decode) if cv is not None]
    cv_total = sum(side_cvs) / len(side_cvs) if side_cvs else None

    def _average_throughput(mean: Optional[float]) -> str:
        if mean is None or throughput_elapsed is None:
            return "N/A"
        return f"{mean / throughput_elapsed:,.1f} token/s"

    items = [
        ("输入 token 均值", _mean_text(mean_prefill)),
        ("输出预算 token 均值", _mean_text(mean_decode)),
        ("总 token 均值", _mean_text(mean_total)),
        ("两侧相对标准差均值", _cv_text(cv_total)),
        ("平均输入吞吐量", _average_throughput(mean_prefill)),
        ("平均输出吞吐量", _average_throughput(mean_decode)),
    ]
    pairs = [items[index:index + 2] for index in range(0, len(items), 2)]
    left_width = max(_display_width(f"{pair[0][0]}：{pair[0][1]}") for pair in pairs)
    print("汇总：")
    for pair in pairs:
        left = f"{pair[0][0]}：{pair[0][1]}"
        if len(pair) == 1:
            print("  " + left)
        else:
            print("  " + _pad(left, left_width + 4) + f"{pair[1][0]}：{pair[1][1]}")


def do_show_load(stats, limit: int) -> None:
    """打印当前进程最近 limit 条请求的实例级 token、吞吐量与负载离散度。"""
    if stats is None:
        print("[CLI] Instance load statistics are not initialized.")
        return

    records, total = stats.snapshot(limit)
    if not records:
        print("[CLI] No requests recorded since Controller start.")
        return

    first_seq = records[0].seq + 1
    last_seq = records[-1].seq + 1
    completed = sum(1 for record in records if record.status == "completed")
    cancelled = sum(1 for record in records if record.status == "cancelled")
    failed = sum(1 for record in records if record.status == "failed")
    inflight = len(records) - completed - cancelled - failed
    prefill_unattributed = sum(
        1 for record in records if record.saw_headers and not record.prefill_instance
    )
    # 只有 PD 请求才存在 D 阶段；非 PD 请求不参与 D 未归属统计。
    decode_unattributed = sum(
        1
        for record in records
        if record.pd_request and record.saw_headers and not record.decode_instance
    )

    elapsed = _window_elapsed(records)
    # 仍有在途请求时 token 总数不完整，吞吐量不拿当前时间充当终点，直接给 N/A。
    throughput_elapsed = elapsed if (elapsed is not None and inflight == 0) else None

    print("=" * 96)
    print(f"Instance load statistics (latest {limit} requests)")
    print("=" * 96)
    print(
        f"范围: 第 {first_seq} - {last_seq} 条"
        f"（已记录 {total} 条，纳入 {len(records)} 条）"
    )
    if total > len(records):
        print(
            f"提示: 仅统计最近 {len(records)} 条，"
            f"更早的 {total - len(records)} 条未纳入本次统计"
        )
    elif total < limit:
        print(f"提示: 已记录 {total} 条，不足 {limit} 条，已显示全部")
    else:
        print(f"提示: 已记录的 {total} 条全部纳入本次统计")
    print(
        f"状态: 已完成 {completed} / 在途 {inflight} / 失败 {failed} / 取消 {cancelled}"
    )
    if elapsed is not None:
        print(
            f"窗口耗时: {elapsed:,.3f} s"
            f"（第 {first_seq} 条接收 → 第 {last_seq} 条结束）"
        )
    else:
        print(f"窗口耗时: N/A（第 {first_seq} 条接收 → 第 {last_seq} 条尚未结束）")
    if inflight:
        print(f"提示: 窗口内仍有 {inflight} 条在途，吞吐量显示 N/A，实例分布为暂计")
    elif elapsed is None:
        print("提示: 窗口耗时无效，吞吐量显示 N/A")
    print(
        "未归属实例: P "
        f"{prefill_unattributed} 条 / D {decode_unattributed} 条"
        "（已拿到 Gateway 响应但缺实例地址）"
    )
    print("")

    tentative = inflight > 0
    prefill_rows, _ = _aggregate(records, "prefill_instance", "prompt_tokens")
    print("Prefill 实例")
    if prefill_rows:
        _render_table(
            ("实例", "输入 token", "请求数", "吞吐量（token/s）"),
            [
                (
                    instance,
                    f"{tokens:,}",
                    str(count),
                    _format_throughput(tokens, throughput_elapsed),
                )
                for instance, tokens, count in prefill_rows
            ],
        )
    else:
        print("  (无已归属实例)")
    _print_group_stats("Prefill", "输入 token ", prefill_rows, tentative)
    print("")

    decode_rows, _ = _aggregate(records, "decode_instance", "max_tokens")
    print("Decode 实例")
    if decode_rows:
        _render_table(
            ("实例", "输出预算 token", "请求数", "吞吐量（token/s）"),
            [
                (
                    instance,
                    f"{tokens:,}",
                    str(count),
                    _format_throughput(tokens, throughput_elapsed),
                )
                for instance, tokens, count in decode_rows
            ],
        )
    else:
        print("  (无已归属实例)")
    _print_group_stats("Decode", "输出预算 token ", decode_rows, tentative)
    print("")
    _print_summary(prefill_rows, decode_rows, throughput_elapsed)
