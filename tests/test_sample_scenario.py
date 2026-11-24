"""端到端：用 samples 演练资料锁定完整业务叙事。"""
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from tests import _bootstrap  # noqa: F401
from assurance.claims import ALREADY_PAID, ELIGIBLE, PENDING
from assurance.exposure import UNDETERMINED
from assurance.loader import load_pack
from assurance.review import apply_retroactive_correction, compare_runs, run_basis
from assurance.settlement import totals

DATA = Path(__file__).resolve().parent.parent / "samples"


class SampleScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = load_pack(DATA)
        cls.base = cls.pack.basis("原月结", date(2026, 7, 5), with_rulings=False)
        cls.adj = cls.pack.basis("裁定后", date(2026, 7, 6), with_rulings=True)

    def test_baseline_identity_groups_b77_under_w01(self):
        run = run_basis(self.pack, self.base, "2026-07")
        decisions = {a.claim.claim: a for a in run.assessments}
        # K1 在原月结下按表面并行承运，两家平台平分
        k1 = decisions["K1"]
        alloc = k1.combinations[0].allocations
        self.assertEqual(set(alloc), {"PC7", "LC7"})
        # K3 在原月结下身份关联尚未生效，需重查
        self.assertEqual(decisions["K3"].decision, "identity_recheck")

    def test_baseline_gap_makes_k2_pending_with_contingent_combo(self):
        run = run_basis(self.pack, self.base, "2026-07")
        k2 = next(a for a in run.assessments if a.claim.claim == "K2")
        self.assertEqual(k2.decision, PENDING)
        self.assertTrue(any(c.contingent for c in k2.combinations))

    def test_adjudication_reassigns_lake_carry_to_w03(self):
        run = run_basis(self.pack, self.adj, "2026-07")
        decisions = {a.claim.claim: a.decision for a in run.assessments}
        self.assertEqual(decisions["K3"], ELIGIBLE)   # W03 身份经 v2 成立
        self.assertEqual(decisions["K1"], ALREADY_PAID)
        k3 = next(a for a in run.assessments if a.claim.claim == "K3")
        self.assertEqual(k3.combinations[0].allocations,
                         {"LC7": Decimal("450.00")})

    def test_ruling_confirms_x7_carry_even_though_reported_late(self):
        run = run_basis(self.pack, self.adj, "2026-07")
        # X7（00:39-00:44 Pine 承运）虽 07-06 才补报，经 R1 裁定进入时间轴
        pine_carry = [r for r in run.rows
                      if r.platform == "Pine" and r.status == "carry"
                      and r.month == "2026-07"]
        self.assertTrue(pine_carry)
        self.assertGreaterEqual(pine_carry[0].lower_hours, Decimal("0.5833"))

    def test_open_gap_produces_lower_and_undetermined_bounds(self):
        run = run_basis(self.pack, self.adj, "2026-07")
        undet = [r for r in run.rows if r.platform == UNDETERMINED]
        self.assertTrue(undet)
        # 下界工时为确定值，未确定部分只增加上界
        self.assertTrue(all(r.lower_hours == Decimal("0.0000") for r in undet))
        self.assertGreater(totals(run.rows)["undetermined_hours"], Decimal("0"))

    def test_total_exposure_can_hold_while_shares_shift(self):
        base_run = run_basis(self.pack, self.base, "2026-07")
        adj_run = run_basis(self.pack, self.adj, "2026-07")
        deltas = compare_runs(base_run, adj_run)
        # Lake 的承运责任从 W01 账上转移到 W03：出现非零份额差额
        lake_delta = [d for d in deltas if d.platform == "Lake"
                      and d.status == "carry"]
        self.assertTrue(any(d.delta_hours != 0 for d in lake_delta))

    def test_k3_cannot_displace_existing_paid_460(self):
        run = run_basis(self.pack, self.adj, "2026-07")
        ledger = run.ledger
        view = {v["cover_id"]: v for v in ledger.availability_view()}
        # LC7 限额 500，已赔 460，K3 只能再占 40，余额排队并升级
        self.assertEqual(view["LC7"]["paid"], Decimal("460"))
        self.assertEqual(view["LC7"]["available_now"], Decimal("0"))
        queued = [i for i in ledger.plan
                  if i.claim == "K3" and i.kind == "queue"]
        self.assertTrue(queued)
        self.assertTrue(any(e.claim == "K3" for e in ledger.escalations))

    def test_k4_paid_from_distinct_cover_not_blocked_by_k3(self):
        run = run_basis(self.pack, self.adj, "2026-07")
        plan = {(i.claim, i.cover_id, i.kind): i for i in run.ledger.plan}
        self.assertIn(("K4", "LC7S", "pay"), plan)
        self.assertEqual(plan[("K4", "LC7S", "pay")].amount, Decimal("290.00"))

    def test_retroactive_correction_for_k1(self):
        run = run_basis(self.pack, self.adj, "2026-07")
        k1 = next(a for a in run.assessments if a.claim.claim == "K1")
        item = apply_retroactive_correction(run.ledger, k1, "PC7")
        self.assertEqual(item.paid_amount, Decimal("460"))
        self.assertEqual(item.correct_cover, "PC7")
        # 原 LC7 给付仍可核对；K1 更正不产生任何新的 PC7 占用
        self.assertEqual(run.ledger.paid_total("LC7"), Decimal("460"))
        k1_reservations = [r for group in run.ledger.confirmed.values()
                           for r in group if r.claim == "K1"]
        self.assertEqual(k1_reservations, [])

    def test_june_and_july_split_at_contract_midnight(self):
        june = run_basis(self.pack, self.adj, "2026-06")
        july = run_basis(self.pack, self.adj, "2026-07")
        self.assertTrue(june.rows)
        self.assertTrue(july.rows)
        self.assertTrue(all(r.month == "2026-06" for r in june.rows))
        self.assertTrue(all(r.month == "2026-07" for r in july.rows))

    def test_unassigned_events_reported_under_baseline(self):
        # 原月结下 v2 未到件，X3/X4 仍能经 v1 归 W01；不应有完全失联事件
        run = run_basis(self.pack, self.base, "2026-07")
        self.assertEqual(run.unassigned_events, [])


if __name__ == "__main__":
    unittest.main()
