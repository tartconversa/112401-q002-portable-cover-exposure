"""核查员工作流：异议、双基准月结对比、裁定落地与逆向更正。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .claims import ClaimAssessment, assess_all
from .exposure import CoverBook
from .identity import IdentityResolver
from .ledger import CorrectionItem, CoverLedger, build_month_ledger
from .loader import DataPack
from .models import Objection
from .settlement import (AllocationDelta, AllocationRow, build_segments,
                         cover_limit_map, diff_allocations, rollup, totals)
from .timeline import build_timelines


@dataclass
class MonthRun:
    """一个认定基准下，单月（或全部月）的完整重算结果。"""

    basis_name: str
    segments: list
    rows: list[AllocationRow]
    total: dict[str, Decimal]
    assessments: list[ClaimAssessment]
    unassigned_events: list
    ledger: CoverLedger | None = None


def run_basis(pack: DataPack, basis, month: str | None = None,
              with_ledger: bool = True) -> MonthRun:
    resolver = IdentityResolver(pack.identities, basis)
    timeline_result = build_timelines(pack.activities, pack.gaps, resolver, basis)
    cover_book = CoverBook(pack.covers)
    segments = build_segments(timeline_result, cover_book, month)
    rows = rollup(segments)
    assessments = assess_all(pack.claims, timeline_result.timelines,
                             resolver, cover_book, basis)
    if month is not None:
        assessments = [a for a in assessments if a.month == month]
    ledger = None
    if with_ledger and month is not None:
        month_claims = sorted(
            [c for c in pack.claims if c.instant.strftime("%Y-%m") == month],
            key=lambda c: c.received)
        ledger = build_month_ledger(month,
                                    cover_limit_map(pack.covers, month=month),
                                    month_claims, assessments)
    return MonthRun(
        basis_name=basis.name, segments=segments, rows=rows,
        total=totals(rows), assessments=assessments,
        unassigned_events=timeline_result.unassigned_events, ledger=ledger)


def compare_runs(base: MonthRun, adjudicated: MonthRun) -> list[AllocationDelta]:
    """相同总暴露为何换了责任份额：逐 工作区/月/状态/平台 列差额。"""
    return diff_allocations(base.rows, adjudicated.rows)


def open_objections(objections: list[Objection]) -> list[Objection]:
    return [o for o in objections if o.status == "open"]


def apply_retroactive_correction(ledger: CoverLedger,
                                  assessment: ClaimAssessment,
                                  correct_cover: str) -> CorrectionItem:
    """逆向更正入口。

    前置校验：该报案必须存在既有给付事实；更正后：
    - 原 paid 记录保留、仍可核对；
    - 未赔部分按新责任在台账中继续办理；
    - 已赔差额生成对账项交下一期，报案人不会再次变成全额可领。
    """
    claim = assessment.claim
    if not claim.paid_cover or claim.paid <= 0:
        raise ValueError(f"报案 {claim.claim} 无既有给付，应走正常裁定而非逆向更正")
    correct_amount = min(claim.paid, claim.requested)
    return ledger.record_correction(assessment, correct_cover, correct_amount)


def pending_and_recheck(run: MonthRun) -> dict[str, list[ClaimAssessment]]:
    """值班员视图：待核、身份重查、无合同、已赔核对分桶。"""
    buckets = {"pending": [], "identity_recheck": [], "no_cover": [],
               "eligible": [], "already_paid": []}
    for assessment in run.assessments:
        buckets.get(assessment.decision, buckets["no_cover"]).append(assessment)
    return buckets


def explain_share_shift(deltas: list[AllocationDelta]) -> list[str]:
    """把份额差额翻成可向核查员解释的中文结论。"""
    lines = []
    for d in deltas:
        if d.delta_hours == 0 and d.delta_fee == 0:
            continue
        lines.append(
            f"{d.month} {d.zone} {d.status} {d.platform}: "
            f"暴露 {d.base_lower_hours}h -> {d.new_lower_hours}h "
            f"（差 {d.delta_hours}h），费用 {d.base_fee} -> {d.new_fee} "
            f"（差 {d.delta_fee}）")
    return lines
