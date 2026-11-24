"""月结：暴露工时与费用归属的汇总、回溯与双基准差额。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .exposure import (CoverBook, ExposureSegment, UNDETERMINED, split_timeline)
from .models import Cover
from .timeline import TimelineBuildResult

MONEY_Q = Decimal("0.01")
HOUR_Q = Decimal("0.0001")


def q_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def q_hours(value: float) -> Decimal:
    return Decimal(str(value)).quantize(HOUR_Q, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class AllocationRow:
    """按 工作区/月/工作状态/平台 的一行归属。"""

    month: str
    zone: str
    status: str
    platform: str
    lower_hours: Decimal
    upper_hours: Decimal
    insured: bool
    cover_id: str | None
    lower_fee: Decimal
    upper_fee: Decimal
    note: str


def build_segments(timeline_result: TimelineBuildResult,
                   cover_book: CoverBook,
                   month: str | None = None) -> list[ExposureSegment]:
    segments: list[ExposureSegment] = []
    for person, timeline in sorted(timeline_result.timelines.items()):
        segments.extend(split_timeline(person, timeline, cover_book, month))
    return sorted(segments, key=lambda s: (s.start, s.person, s.platform))


def rollup(segments: list[ExposureSegment]) -> list[AllocationRow]:
    """汇总为可按工作区、月、工作状态、平台回溯的归属表。"""
    buckets: dict[tuple, dict] = {}
    for seg in segments:
        key = (seg.month, seg.zone, seg.status, seg.platform)
        bucket = buckets.setdefault(key, {
            "lower_hours": 0.0, "upper_extra": 0.0, "insured": seg.insured,
            "cover_id": seg.cover_id, "lower_fee": Decimal("0"),
            "upper_fee": Decimal("0"), "notes": set()})
        bucket["lower_hours"] += seg.lower_hours
        bucket["upper_extra"] += seg.upper_extra_hours
        if seg.note:
            bucket["notes"].add(seg.note)
        if seg.rate_per_hour is not None:
            rate = Decimal(seg.rate_per_hour)
            bucket["lower_fee"] += rate * Decimal(str(seg.share_hours))
            bucket["upper_fee"] += rate * Decimal(str(seg.upper_extra_hours))
    rows: list[AllocationRow] = []
    for (month, zone, status, platform), b in sorted(buckets.items()):
        lower_h = Decimal(str(b["lower_hours"]))
        upper_h = Decimal(str(b["lower_hours"] + b["upper_extra"]))
        rows.append(AllocationRow(
            month=month, zone=zone, status=status, platform=platform,
            lower_hours=q_hours(b["lower_hours"]), upper_hours=q_hours(upper_h),
            insured=b["insured"], cover_id=b["cover_id"],
            lower_fee=q_money(b["lower_fee"]), upper_fee=q_money(b["upper_fee"]),
            note="；".join(sorted(b["notes"]))))
    return rows


def totals(rows: list[AllocationRow]) -> dict[str, Decimal]:
    """整份月结的确定暴露工时、上界工时与确定费用；未确定归属单列。"""
    lower = sum((r.lower_hours for r in rows), Decimal("0"))
    upper = sum((r.upper_hours for r in rows), Decimal("0"))
    fee = sum((r.lower_fee for r in rows), Decimal("0"))
    undetermined = sum((r.upper_hours - r.lower_hours
                        for r in rows if r.platform == UNDETERMINED), Decimal("0"))
    return {
        "lower_hours": q_hours(lower),
        "upper_hours": q_hours(upper),
        "undetermined_hours": q_hours(undetermined),
        "lower_fee": q_money(fee),
    }


@dataclass(frozen=True)
class AllocationDelta:
    """同一资料在两个基准下的责任份额差额（总暴露可相同、份额不同）。"""

    month: str
    zone: str
    status: str
    platform: str
    base_lower_hours: Decimal
    new_lower_hours: Decimal
    delta_hours: Decimal
    base_fee: Decimal
    new_fee: Decimal
    delta_fee: Decimal


def diff_allocations(base_rows: list[AllocationRow],
                     new_rows: list[AllocationRow]) -> list[AllocationDelta]:
    keys = {(r.month, r.zone, r.status, r.platform) for r in base_rows}
    keys |= {(r.month, r.zone, r.status, r.platform) for r in new_rows}
    base_idx = {(r.month, r.zone, r.status, r.platform): r for r in base_rows}
    new_idx = {(r.month, r.zone, r.status, r.platform): r for r in new_rows}
    out: list[AllocationDelta] = []
    for key in sorted(keys):
        b = base_idx.get(key)
        n = new_idx.get(key)
        bh, nh = (b.lower_hours if b else Decimal("0.0000")), \
                 (n.lower_hours if n else Decimal("0.0000"))
        bf, nf = (b.lower_fee if b else Decimal("0")), \
                 (n.lower_fee if n else Decimal("0"))
        out.append(AllocationDelta(
            month=key[0], zone=key[1], status=key[2], platform=key[3],
            base_lower_hours=bh, new_lower_hours=nh,
            delta_hours=q_hours(nh - bh), base_fee=bf, new_fee=nf,
            delta_fee=q_money(nf - bf)))
    return out


def cover_limit_map(covers: list[Cover], month: str | None = None) -> dict[str, Decimal]:
    """同一 cover_id 双状态共用一个月额度，不相加；可按合同月过滤。"""
    limits: dict[str, Decimal] = {}
    for cover in covers:
        if month is not None and cover.month != month:
            continue
        if cover.cover_id in limits and limits[cover.cover_id] != cover.limit:
            raise ValueError(f"合同 {cover.cover_id} 月额度在多行间不一致")
        limits[cover.cover_id] = cover.limit
    return limits


def month_fee_bounds(rows: list[AllocationRow]) -> dict[str, dict[str, Decimal]]:
    """按合同汇总费用下界与上界（未确定归属不进任何合同）。"""
    out: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        if not row.cover_id:
            continue
        bucket = out.setdefault(row.cover_id,
                                {"lower_fee": Decimal("0"), "upper_fee": Decimal("0")})
        bucket["lower_fee"] += row.lower_fee
        bucket["upper_fee"] += row.upper_fee
    return {cid: {"lower_fee": q_money(v["lower_fee"]),
                  "upper_fee": q_money(v["upper_fee"])} for cid, v in sorted(out.items())}

