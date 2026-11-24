"""月报与报案资料的 CSV 导出，以及控制台核对视图。"""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .exposure import UNDETERMINED
from .review import MonthRun, explain_share_shift
from .settlement import AllocationDelta


def _write(path: Path, header: tuple[str, ...], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(header)
        writer.writerows(rows)


def export_month_pack(run: MonthRun, directory: str | Path) -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    seg_rows = []
    for s in run.segments:
        seg_rows.append((
            s.month, s.person, s.zone, s.start.isoformat(), s.end.isoformat(),
            s.status, s.platform,
            f"{s.share_hours:.4f}", f"{s.lower_hours:.4f}",
            f"{s.upper_extra_hours:.4f}",
            s.insured, s.cover_id or "",
            str(s.rate_per_hour) if s.rate_per_hour is not None else "",
            "|".join(s.sources), "|".join(s.candidate_platforms), s.note))
    p = directory / "exposure_segments.csv"
    _write(p, ("month", "person", "zone", "start", "end", "status", "platform",
               "share_hours", "lower_hours", "upper_extra_hours", "insured",
               "cover_id", "rate_per_hour", "sources",
               "candidate_platforms", "note"), seg_rows)
    written.append(p)

    alloc_rows = [(r.month, r.zone, r.status, r.platform,
                   str(r.lower_hours), str(r.upper_hours), r.insured,
                   r.cover_id or "", str(r.lower_fee), str(r.upper_fee), r.note)
                  for r in run.rows]
    p = directory / "allocation.csv"
    _write(p, ("month", "zone", "status", "platform", "lower_hours",
               "upper_hours", "insured", "cover_id", "lower_fee",
               "upper_fee", "note"), alloc_rows)
    written.append(p)

    combo_rows = []
    for a in run.assessments:
        for combo in a.combinations:
            for cover_id, amount in sorted(combo.allocations.items()):
                combo_rows.append((
                    a.claim.claim, a.claim.person, a.month, a.decision,
                    combo.combo_id, cover_id, str(amount),
                    "contingent" if combo.contingent else "confirmed",
                    combo.state_basis, combo.rationale,
                    "；".join(a.notes)))
        if not a.combinations:
            combo_rows.append((a.claim.claim, a.claim.person, a.month,
                               a.decision, "", "", "", "", "",
                               "；".join(a.notes), ""))
    p = directory / "claim_liability.csv"
    _write(p, ("claim", "person", "month", "decision", "combo_id",
               "cover_id", "amount", "kind", "state_basis",
               "rationale", "notes"), combo_rows)
    written.append(p)

    if run.ledger is not None:
        p = directory / "cover_availability.csv"
        view_rows = [(v["cover_id"], v["month"], str(v["limit"]),
                      str(v["paid"]), str(v["confirmed_reserved"]),
                      str(v["contingent_reserved"]), str(v["available_now"]),
                      str(v.get("queued", Decimal("0"))), v["contingent_note"])
                     for v in run.ledger.availability_view()]
        _write(p, ("cover_id", "month", "limit", "paid",
                   "confirmed_reserved", "contingent_reserved",
                   "available_now", "queued", "contingent_note"), view_rows)
        written.append(p)

        p = directory / "payment_plan.csv"
        plan_rows = [(i.claim, i.cover_id, str(i.amount), i.kind,
                      i.combo_id, i.note) for i in run.ledger.plan]
        _write(p, ("claim", "cover_id", "amount", "kind", "combo_id", "note"),
               plan_rows)
        written.append(p)

        p = directory / "escalations.csv"
        esc_rows = [(e.claim, e.cover_id, str(e.requested_amount),
                     str(e.blocked_by_paid), e.note)
                    for e in run.ledger.escalations]
        _write(p, ("claim", "cover_id", "requested_amount",
                   "blocked_by_paid", "note"), esc_rows)
        written.append(p)

        p = directory / "corrections.csv"
        corr_rows = [(c.claim, c.paid_cover, str(c.paid_amount),
                      c.correct_cover, str(c.correct_amount), c.month, c.note)
                     for c in run.ledger.corrections]
        _write(p, ("claim", "paid_cover", "paid_amount", "correct_cover",
                   "correct_amount", "month", "note"), corr_rows)
        written.append(p)

    unassigned = [(e.event_id, e.platform, e.account, e.start.isoformat(),
                   e.end.isoformat(), "基准下无生效身份关联")
                  for e in run.unassigned_events]
    p = directory / "unassigned_events.csv"
    _write(p, ("event_id", "platform", "account", "start", "end", "note"),
           unassigned)
    written.append(p)
    return written


def export_delta(deltas: list[AllocationDelta], directory: str | Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "allocation_delta.csv"
    rows = [(d.month, d.zone, d.status, d.platform,
             str(d.base_lower_hours), str(d.new_lower_hours), str(d.delta_hours),
             str(d.base_fee), str(d.new_fee), str(d.delta_fee))
            for d in deltas]
    _write(p, ("month", "zone", "status", "platform", "base_lower_hours",
               "new_lower_hours", "delta_hours", "base_fee", "new_fee",
               "delta_fee"), rows)
    return p


def print_summary(run: MonthRun) -> str:
    lines = [f"== 基准 {run.basis_name} ==",
             f"确定暴露 {run.total['lower_hours']}h | "
             f"上界 {run.total['upper_hours']}h | "
             f"未确定归属 {run.total['undetermined_hours']}h | "
             f"确定费用 {run.total['lower_fee']} 元",
             "-- 责任归属 --"]
    for r in run.rows:
        tag = "  [未确定]" if r.platform == UNDETERMINED else ""
        lines.append(
            f"{r.month} {r.zone} {r.status:<4} {r.platform:<5} "
            f"{r.lower_hours}h(上界{r.upper_hours}h) "
            f"费用{r.lower_fee}元{tag} {r.note}")
    lines.append("-- 报案 --")
    for a in run.assessments:
        combos = "; ".join(
            f"{c.combo_id}:{','.join(f'{k}={v}' for k, v in c.allocations.items())}"
            f"{'(或然)' if c.contingent else ''}" for c in a.combinations) or "无组合"
        lines.append(f"{a.claim.claim} {a.claim.person} {a.decision}: {combos}")
        for note in a.notes:
            lines.append(f"    注：{note}")
    if run.ledger is not None:
        lines.append("-- 当期可再占用额度 --")
        for v in run.ledger.availability_view():
            lines.append(
                f"{v['cover_id']}: 限额{v['limit']} 已赔{v['paid']} "
                f"确认预留{v['confirmed_reserved']} "
                f"或然{v['contingent_reserved']} 可再占用{v['available_now']} "
                f"{v['contingent_note']}")
        for e in run.ledger.escalations:
            lines.append(f"[升级经办人] {e.claim} {e.cover_id}: {e.note}")
    return "\n".join(lines)


def print_delta_explanation(deltas: list[AllocationDelta]) -> str:
    lines = explain_share_shift(deltas)
    return "== 裁定前后责任差额 ==\n" + ("\n".join(lines) if lines else "无差额")
