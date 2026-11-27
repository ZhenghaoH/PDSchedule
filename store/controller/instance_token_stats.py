"""Controller 进程内的实例级 token 分配统计。

仅供 ``show load`` 查询：保留请求进入顺序、状态、P/D 实例地址、
输入 token、输出上限，以及接收和首次终态的单调时钟时点，
不保存 prompt、响应正文或完整请求对象。
记录随请求增长，不跨进程重启保存。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import List, Optional, Tuple

STATUS_INFLIGHT = "inflight"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


@dataclass
class InstanceTokenRecord:
    """单条请求的精简统计记录。"""

    seq: int
    request_id: int
    status: str = STATUS_INFLIGHT
    prompt_tokens: Optional[int] = None
    max_tokens: Optional[int] = None
    prefill_instance: str = ""
    decode_instance: str = ""
    # 是否 PD 分离请求：非 PD 请求没有 D 阶段，不参与 D 未归属统计。
    pd_request: bool = False
    # 是否拿到过 P Gateway 的响应头；用于区分"没执行该阶段"和"执行了但地址未知"。
    saw_headers: bool = False
    # 单调时钟时点：接收时刻与首次终态时刻，只用于窗口吞吐量，不参与排序。
    received_at: float = 0.0
    finished_at: Optional[float] = None


class InstanceTokenStats:
    """Controller 与 CLI 跨线程共享，读写都在同一把锁内。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: List[InstanceTokenRecord] = []
        self._next_seq = 0

    def begin(self, request_id: int) -> InstanceTokenRecord:
        """占用一个接收序号并记下接收时点；后续步骤失败也保留该序号。"""
        with self._lock:
            record = InstanceTokenRecord(
                seq=self._next_seq,
                request_id=request_id,
                received_at=time.monotonic(),
            )
            self._next_seq += 1
            self._records.append(record)
            return record

    def set_tokens(
        self,
        record: Optional[InstanceTokenRecord],
        prompt_tokens,
        max_tokens,
        *,
        pd_request: bool = False,
    ) -> None:
        if record is None:
            return
        with self._lock:
            record.prompt_tokens = _non_negative_int(prompt_tokens)
            record.max_tokens = _non_negative_int(max_tokens)
            record.pd_request = bool(pd_request)

    def attribute(
        self,
        record: Optional[InstanceTokenRecord],
        *,
        prefill_instance: Optional[str] = None,
        decode_instance: Optional[str] = None,
        saw_headers: bool = False,
    ) -> None:
        """同一阶段只记第一次拿到的实例地址。"""
        if record is None:
            return
        with self._lock:
            if saw_headers:
                record.saw_headers = True
            if prefill_instance and not record.prefill_instance:
                record.prefill_instance = prefill_instance
            if decode_instance and not record.decode_instance:
                record.decode_instance = decode_instance

    def finish(self, record: Optional[InstanceTokenRecord], status: str) -> None:
        """写入终态并记下首次终态时点；已经终结的记录不再改状态和时点。"""
        if record is None:
            return
        if status not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
            return
        with self._lock:
            if record.status == STATUS_INFLIGHT:
                record.status = status
                record.finished_at = time.monotonic()

    def snapshot(self, limit: int) -> Tuple[List[InstanceTokenRecord], int]:
        """返回最近接收的 ``limit`` 条副本（仍按接收顺序），以及已记录总数。

        从记录尾部截取：先跑一批请求、切换策略后再跑一批时，查询只覆盖
        最后 ``limit`` 条；总数不足 ``limit`` 时返回全部。
        """
        with self._lock:
            total = len(self._records)
            if limit <= 0 or limit >= total:
                chosen = self._records
            else:
                chosen = self._records[-limit:]
            return [replace(record) for record in chosen], total


def _non_negative_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def get_instance_token_stats(app):
    """读取 lifespan 中挂到 ``app.state`` 的记录器。"""
    return getattr(app.state, "instance_token_stats", None)
