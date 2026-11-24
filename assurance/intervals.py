"""左闭右开时段的通用运算与原子切片。"""
from __future__ import annotations

from datetime import datetime
from typing import TypeVar

T = TypeVar("T")


def overlap(a_start: datetime, a_end: datetime,
            b_start: datetime, b_end: datetime) -> tuple[datetime, datetime] | None:
    """两个左闭右开时段的交集；无重叠（含端点相接）返回 None。"""
    lo, hi = max(a_start, b_start), min(a_end, b_end)
    if lo < hi:
        return lo, hi
    return None


def intersect(interval: tuple[datetime, datetime],
              boundary: tuple[datetime, datetime]) -> tuple[datetime, datetime] | None:
    """时段与边界的交集。"""
    return overlap(interval[0], interval[1], boundary[0], boundary[1])


def atomic_slices(sources: list[tuple[datetime, datetime]]
                  ) -> list[tuple[datetime, datetime]]:
    """由若干时段构造原子切片：每个切片内来源集合不发生变化。

    左闭右开；仅覆盖至少有一个来源覆盖的部分，不产生空切片。
    """
    cuts: set[datetime] = set()
    for start, end in sources:
        if end <= start:
            raise ValueError(f"非法时段：{start} >= {end}")
        cuts.add(start)
        cuts.add(end)
    ordered = sorted(cuts)
    return [(a, b) for a, b in zip(ordered, ordered[1:]) if a < b]


def covers(intervals: list[tuple[datetime, datetime]],
           start: datetime, end: datetime) -> bool:
    """目标 [start, end) 是否被给定时段集合完全覆盖（相接视为连续）。"""
    cursor = start
    for lo, hi in sorted(intervals):
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return cursor >= end
