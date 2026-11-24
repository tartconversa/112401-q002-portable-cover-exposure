"""命令行入口：月报、双基准对比、报案台账、裁定转换/释放、逆向更正。

示例：
  python3 -m assurance.cli monthly --data samples --month 2026-07 --out out/month
  python3 -m assurance.cli compare --data samples --month 2026-07 --out out/cmp
  python3 -m assurance.cli claims  --data samples --month 2026-07 --out out/claims
  python3 -m assurance.cli rule   --data samples --month 2026-07 \
      --claim K3 --combo C0 --out out/rule
  python3 -m assurance.cli correct --data samples --month 2026-07 \
      --claim K1 --cover PC7 --out out/correct
"""
from __future__ import annotations

import argparse
from datetime import date

from .loader import load_pack
from .reporting import (export_delta, export_month_pack, print_delta_explanation,
                        print_summary)
from .review import apply_retroactive_correction, compare_runs, run_basis
from .timeutil import parse_day


def _baseline_day(pack) -> date:
    """原月结基准日：首份裁定到件前一日（可被 --as-of 覆盖）。"""
    if pack.rulings:
        first = min(r.received for r in pack.rulings)
        return first.fromordinal(first.toordinal() - 1)
    return max(e.received for e in pack.activities)


def _build(args):
    pack = load_pack(args.data)
    as_of = parse_day(args.as_of) if args.as_of else _baseline_day(pack)
    adj_day = parse_day(args.adjudicated_as_of) if args.adjudicated_as_of \
        else max(r.received for r in pack.rulings)
    base = pack.basis("原月结", as_of, with_rulings=False)
    # 显式 --baseline 时人工操作在原月结基准上进行（保留或然组合）；
    # 否则裁定基准日不早于原月结基准日。
    if getattr(args, "baseline", False):
        adj = base
    else:
        adj = pack.basis("裁定后", max(as_of, adj_day), with_rulings=True)
    return pack, base, adj


def cmd_monthly(args) -> None:
    pack, base, _ = _build(args)
    run = run_basis(pack, base, args.month)
    paths = export_month_pack(run, args.out)
    print(print_summary(run))
    print("\n已导出：")
    for p in paths:
        print(f"  {p}")


def cmd_compare(args) -> None:
    pack, base, adj = _build(args)
    base_run = run_basis(pack, base, args.month)
    adj_run = run_basis(pack, adj, args.month)
    deltas = compare_runs(base_run, adj_run)
    export_month_pack(base_run, f"{args.out}/baseline")
    export_month_pack(adj_run, f"{args.out}/adjudicated")
    path = export_delta(deltas, args.out)
    print(print_summary(base_run))
    print()
    print(print_summary(adj_run))
    print()
    print(print_delta_explanation(deltas))
    print(f"\n差额表：{path}")


def cmd_claims(args) -> None:
    pack, _, adj = _build(args)
    run = run_basis(pack, adj, args.month)
    paths = export_month_pack(run, args.out)
    print(print_summary(run))
    print("\n已导出：")
    for p in paths:
        print(f"  {p}")


def cmd_rule(args) -> None:
    """新证据确认责任组合（或然转确认）或排除责任（--release）。"""
    pack, _, adj = _build(args)
    run = run_basis(pack, adj, args.month)
    ledger = run.ledger
    assert ledger is not None
    if args.release:
        ledger.release_claim(args.claim)
        released = ledger.release_confirmed(args.claim)
        if released:
            note = (f"已排除报案 {args.claim} 的确认责任并释放 {released} 元，"
                    "排队报案按受理顺序续办")
        else:
            note = f"已释放报案 {args.claim} 的或然预留"
    else:
        if not args.combo:
            raise SystemExit("确认责任需用 --combo 指定组合编号")
        ledger.confirm_combo(args.claim, args.combo)
        note = f"报案 {args.claim} 组合 {args.combo} 已由或然转为确认"
    export_month_pack(run, args.out)
    print(note)
    print(print_summary(run))


def cmd_correct(args) -> None:
    """逆向更正：原给付固化，差额转下期对账。"""
    pack, _, adj = _build(args)
    run = run_basis(pack, adj, args.month)
    ledger = run.ledger
    assert ledger is not None
    assessment = next((a for a in run.assessments if a.claim.claim == args.claim),
                      None)
    if assessment is None:
        raise SystemExit(f"当月未找到报案 {args.claim}")
    item = apply_retroactive_correction(ledger, assessment, args.cover)
    export_month_pack(run, args.out)
    print(f"更正登记：{item}")
    print(print_summary(run))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assurance",
                                     description="多平台接单暴露与保障责任台账")
    parser.add_argument("--data", default="samples", help="CSV 资料目录")
    parser.add_argument("--month", required=True, help="合同月 YYYY-MM")
    parser.add_argument("--out", default="out", help="导出目录")
    parser.add_argument("--as-of", help="原月结基准日 YYYY-MM-DD")
    parser.add_argument("--adjudicated-as-of", help="裁定基准日 YYYY-MM-DD")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("monthly", help="按原月结基准产出月报")
    p.set_defaults(func=cmd_monthly)

    p = sub.add_parser("compare", help="原月结 vs 裁定后的暴露与责任差额")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("claims", help="裁定后基准的报案责任与额度台账")
    p.set_defaults(func=cmd_claims)

    p = sub.add_parser("rule", help="确认或排除某报案的责任组合")
    p.add_argument("--claim", required=True)
    p.add_argument("--combo", help="确认的组合编号，如 C0/U1")
    p.add_argument("--release", action="store_true", help="排除责任，释放或然预留")
    p.add_argument("--baseline", action="store_true",
                   help="在原月结基准（无裁定）上操作，用于演练或然组合转换")
    p.set_defaults(func=cmd_rule)

    p = sub.add_parser("correct", help="逆向更正历史已赔责任")
    p.add_argument("--claim", required=True)
    p.add_argument("--cover", required=True, help="新责任合同 cover_id")
    p.add_argument("--baseline", action="store_true",
                   help="在原月结基准（无裁定）上操作")
    p.set_defaults(func=cmd_correct)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
