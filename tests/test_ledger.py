"""额度台账：已赔不可挤占、或然/确认分层、释放续办、受理顺序、逆向更正。"""
import unittest
from decimal import Decimal

from tests import _bootstrap  # noqa: F401
from assurance.claims import ClaimAssessment, LiabilityCombination
from assurance.ledger import CoverLedger
from assurance.models import Claim
from assurance.timeutil import parse_dt


def claim(cid, requested="100", paid_cover=None, paid="0",
          received="2026-07-02T10:00+08:00"):
    return Claim(cid, "W1", "Z", parse_dt("2026-07-01T00:30+08:00"),
                 Decimal(requested), parse_dt(received),
                 "paid" if paid_cover else "review", paid_cover, Decimal(paid))


def assessment(c, combos, decision="eligible", already_paid=False):
    return ClaimAssessment(c, "2026-07", decision, "W1", tuple(combos), (),
                           already_paid)


def combo(cid, alloc, contingent=False, state="carry", rationale="r"):
    return LiabilityCombination(cid, rationale, alloc, contingent, state)


class LedgerTests(unittest.TestCase):
    def test_paid_amount_cannot_be_displaced(self):
        ledger = CoverLedger({"LC": Decimal("500")}, "2026-07")
        ledger.record_paid(claim("K0", paid_cover="LC", paid="460"))
        a = assessment(claim("K1", "100"), [combo("C0", {"LC": Decimal("100")})])
        ledger.apply_assessment(a)
        # 仅剩 40 额度：付 40，余 60 排队，且已赔 460 原样保留
        self.assertEqual(ledger.paid_total("LC"), Decimal("460"))
        self.assertEqual(ledger.available_now("LC"), Decimal("0"))
        kinds = [(i.claim, i.kind, i.amount) for i in ledger.plan]
        self.assertIn(("K1", "pay", Decimal("40")), kinds)
        self.assertIn(("K1", "queue", Decimal("60")), kinds)
        self.assertTrue(ledger.escalations)

    def test_contingent_reservations_are_mutually_exclusive_not_summed(self):
        ledger = CoverLedger({"LC": Decimal("500")}, "2026-07")
        a = assessment(claim("K1", "300"), [
            combo("U1", {"LC": Decimal("300")}, contingent=True),
            combo("U2", {"LC": Decimal("300")}, contingent=True)])
        ledger.apply_assessment(a)
        # 两个互斥组合各 300，但或然读数取最大值 300，不相加成 600
        self.assertEqual(ledger.contingent_total("LC"), Decimal("300"))
        # 或然预留不扣减"当期可再占用"
        self.assertEqual(ledger.available_now("LC"), Decimal("500"))

    def test_confirm_combo_converts_and_releases_others(self):
        ledger = CoverLedger({"LC": Decimal("500")}, "2026-07")
        a = assessment(claim("K1", "300"), [
            combo("U1", {"LC": Decimal("300")}, contingent=True),
            combo("U2", {"LC": Decimal("200")}, contingent=True)])
        ledger.apply_assessment(a)
        ledger.confirm_combo("K1", "U1")
        self.assertEqual(ledger.contingent_total("LC"), Decimal("0"))
        self.assertEqual(ledger.available_now("LC"), Decimal("200"))

    def test_confirm_certain_combo_is_rejected(self):
        ledger = CoverLedger({"LC": Decimal("500")}, "2026-07")
        a = assessment(claim("K1", "300"),
                       [combo("C0", {"LC": Decimal("300")})])
        ledger.apply_assessment(a)
        with self.assertRaises(ValueError):
            ledger.confirm_combo("K1", "C0")

    def test_release_allows_queued_claim_to_proceed(self):
        ledger = CoverLedger({"LC": Decimal("100")}, "2026-07")
        # 一个或然预留不占可用额度；一个确认报案先占满额度并使后来者排队
        contingent = assessment(claim("K0", "100"),
                                [combo("U1", {"LC": Decimal("100")},
                                       contingent=True)])
        ledger.apply_assessment(contingent)
        first = assessment(claim("K1", "100"),
                           [combo("C0", {"LC": Decimal("100")})])
        ledger.apply_assessment(first)
        second = assessment(claim("K2", "60"),
                            [combo("C0", {"LC": Decimal("60")})])
        ledger.apply_assessment(second)
        self.assertEqual(ledger.available_now("LC"), Decimal("0"))
        self.assertTrue(any(r.claim == "K2" for r in ledger.queued["LC"]))
        # 排除 K0 的或然责任本身不释放确认额度；这里直接释放一个确认占用模拟更正
        ledger.confirmed["LC"] = [
            r for r in ledger.confirmed["LC"] if r.claim != "K1"]
        produced = ledger.reprocess_queue("LC")
        # K2 按受理顺序拿到释放出的额度
        self.assertTrue(any(i.claim == "K2" for i in produced))
        self.assertEqual(ledger.available_now("LC"), Decimal("40"))

    def test_reprocess_respects_reception_order(self):
        ledger = CoverLedger({"LC": Decimal("100")}, "2026-07")
        # 第一笔占满 100；第二笔请求 60，全额排队
        ledger.apply_assessment(assessment(
            claim("K1", "100"), [combo("C0", {"LC": Decimal("100")})]))
        ledger.apply_assessment(assessment(
            claim("K2", "60"), [combo("C0", {"LC": Decimal("60")})]))
        self.assertEqual(ledger.queued["LC"][0].amount, Decimal("60"))
        # 新证据排除 K1 的确认责任：释放 100，排队的 K2 按受理顺序续办 60
        released = ledger.release_confirmed("K1", "LC")
        self.assertEqual(released, Decimal("100"))
        self.assertEqual(ledger.queued["LC"], [])
        self.assertTrue(any(i.claim == "K2" and i.amount == Decimal("60")
                            for i in ledger.plan if i.kind == "pay"))
        # 续办后仍剩 40 可用额度
        self.assertEqual(ledger.available_now("LC"), Decimal("40"))

    def test_claims_processed_in_reception_order(self):
        ledger = CoverLedger({"LC": Decimal("100")}, "2026-07")
        claims = [claim("EARLY", "100", received="2026-07-02T08:00+08:00"),
                  claim("LATE", "100", received="2026-07-03T08:00+08:00")]
        assessments = [
            assessment(claims[0], [combo("C0", {"LC": Decimal("100")})]),
            assessment(claims[1], [combo("C0", {"LC": Decimal("100")})])]
        ledger.record_paid(claims[0])
        ledger.record_paid(claims[1])
        ledger.apply_assessment(assessments[0])
        ledger.apply_assessment(assessments[1])
        # EARLY 先拿到仅有的 100；LATE 全额排队
        plan = [(i.claim, i.kind) for i in ledger.plan]
        self.assertEqual(plan[0], ("EARLY", "pay"))
        self.assertTrue(any(c == "LATE" and k == "queue" for c, k in plan))

    def test_retroactive_correction_freezes_payment_and_carries_diff(self):
        ledger = CoverLedger({"LC": Decimal("500"), "PC": Decimal("500")},
                             "2026-07")
        c = claim("K1", "460", paid_cover="LC", paid="460")
        ledger.record_paid(c)
        a = assessment(c, [combo("C0", {"PC": Decimal("460")})],
                       already_paid=True)
        item = ledger.record_correction(a, "PC", Decimal("460"))
        # 原给付仍在 LC 上可核对
        self.assertEqual(ledger.paid_total("LC"), Decimal("460"))
        self.assertEqual(item.paid_cover, "LC")
        self.assertEqual(item.correct_cover, "PC")
        # PC 没有产生新的可领金额（报案人不会再次全额领款）
        self.assertEqual(ledger.available_now("PC"), Decimal("500"))
        self.assertEqual(ledger.confirmed_total("PC"), Decimal("0"))

    def test_same_amount_not_counted_twice_across_claims(self):
        ledger = CoverLedger({"LC": Decimal("300")}, "2026-07")
        a1 = assessment(claim("K1", "300"),
                        [combo("C0", {"LC": Decimal("300")})])
        ledger.apply_assessment(a1)
        a2 = assessment(claim("K2", "300"),
                        [combo("C0", {"LC": Decimal("300")})])
        ledger.apply_assessment(a2)
        # 额度被 K1 占满，K2 不能就同一额度再次获赔，只能排队
        self.assertEqual(ledger.available_now("LC"), Decimal("0"))
        self.assertTrue(any(i.claim == "K2" and i.kind == "queue"
                            for i in ledger.plan))

    def test_duplicate_paid_registration_rejected(self):
        ledger = CoverLedger({"LC": Decimal("500")}, "2026-07")
        c = claim("K1", "100", paid_cover="LC", paid="100")
        ledger.record_paid(c)
        with self.assertRaises(ValueError):
            ledger.record_paid(c)


if __name__ == "__main__":
    unittest.main()
