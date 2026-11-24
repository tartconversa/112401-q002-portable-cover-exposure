"""报案核定：承运/等单条款、缺证待核、身份重查、互斥组合。"""
import unittest
from datetime import date
from decimal import Decimal

from tests import _bootstrap  # noqa: F401
from tests.factories import cover, ev, gap, identity
from assurance.basis import Basis
from assurance.claims import (ELIGIBLE, IDENTITY_RECHECK, NO_COVER, PENDING,
                              assess_claim)
from assurance.exposure import CoverBook
from assurance.identity import IdentityResolver
from assurance.models import Claim
from assurance.timeline import build_timelines
from assurance.timeutil import parse_dt


def make_claim(cid="K1", person="W1", instant="2026-07-01T00:30+08:00",
               requested="100", zone="Z", paid_cover=None, paid="0"):
    return Claim(cid, person, zone, parse_dt(instant), Decimal(requested),
                 parse_dt("2026-07-02T10:00+08:00"),
                 "paid" if paid_cover else "review", paid_cover, Decimal(paid))


def assemble(events, gaps_, ids, covers, as_of="2026-07-02", rulings=None):
    basis = (Basis.adjudicated("t", date.fromisoformat(as_of), rulings)
             if rulings else Basis.baseline("t", date.fromisoformat(as_of)))
    resolver = IdentityResolver(ids, basis)
    timelines = build_timelines(events, gaps_, resolver, basis).timelines
    return resolver, CoverBook(covers), basis, timelines


class ClaimAssessmentTests(unittest.TestCase):
    def test_carry_claim_splits_across_carry_covers(self):
        events = [
            ev("C1", "Pine", "a1", "carry", "2026-07-01T00:00+08:00",
                "2026-07-01T01:00+08:00", "T1"),
            ev("C2", "Lake", "b1", "carry", "2026-07-01T00:00+08:00",
                "2026-07-01T01:00+08:00", "T2")]
        ids = [identity("Pine", "a1", "W1"), identity("Lake", "b1", "W1")]
        covers = [cover("PC", "Pine", "carry"), cover("LC", "Lake", "carry")]
        resolver, book, basis, timelines = assemble(events, [], ids, covers)
        result = assess_claim(make_claim(), timelines, resolver, book, basis)
        self.assertEqual(result.decision, ELIGIBLE)
        combo = result.combinations[0]
        self.assertEqual(combo.allocations,
                         {"PC": Decimal("50.00"), "LC": Decimal("50.00")})
        self.assertEqual(sum(combo.allocations.values()), Decimal("100.00"))

    def test_wait_claim_uses_wait_clause_only(self):
        events = [ev("W1", "Pine", "a1", "wait", "2026-07-01T00:00+08:00",
                     "2026-07-01T01:00+08:00")]
        ids = [identity("Pine", "a1", "W1")]
        covers = [cover("PC", "Pine", "wait")]
        resolver, book, basis, timelines = assemble(events, [], ids, covers)
        result = assess_claim(make_claim(), timelines, resolver, book, basis)
        self.assertEqual(result.combinations[0].state_basis, "wait")
        self.assertEqual(result.combinations[0].allocations,
                         {"PC": Decimal("100.00")})

    def test_missing_evidence_across_states_becomes_pending(self):
        # 已知等单 + 同段未补报断点（可能是承运）
        events = [ev("W1", "Pine", "a1", "wait", "2026-07-01T00:00+08:00",
                     "2026-07-01T01:00+08:00")]
        gaps_ = [gap("G1", "Lake", "b1", "2026-07-01T00:00+08:00",
                     "2026-07-01T01:00+08:00")]
        ids = [identity("Pine", "a1", "W1"), identity("Lake", "b1", "W1")]
        covers = [cover("PC", "Pine", "wait"), cover("LC", "Lake", "carry")]
        resolver, book, basis, timelines = assemble(events, gaps_, ids, covers)
        result = assess_claim(make_claim(), timelines, resolver, book, basis)
        self.assertEqual(result.decision, PENDING)
        self.assertTrue(any(c.contingent for c in result.combinations))
        # 互斥：确定组合与或然组合分开列示
        self.assertTrue(any(not c.contingent for c in result.combinations))

    def test_identity_recheck_when_no_effective_link(self):
        events = [ev("C1", "Lake", "b1", "carry", "2026-07-01T00:00+08:00",
                     "2026-07-01T01:00+08:00", "T1")]
        # W9 没有任何关联
        ids = [identity("Lake", "b1", "W1")]
        covers = [cover("LC", "Lake", "carry")]
        resolver, book, basis, timelines = assemble(events, [], ids, covers)
        result = assess_claim(make_claim(person="W9"), timelines, resolver,
                              book, basis)
        self.assertEqual(result.decision, IDENTITY_RECHECK)

    def test_no_cover_when_platform_uninsured(self):
        events = [ev("C1", "Lake", "b1", "carry", "2026-07-01T00:00+08:00",
                     "2026-07-01T01:00+08:00", "T1")]
        ids = [identity("Lake", "b1", "W1")]
        resolver, book, basis, timelines = assemble(events, [], ids, [])
        result = assess_claim(make_claim(), timelines, resolver, book, basis)
        self.assertEqual(result.decision, NO_COVER)

    def test_paid_claim_is_flagged_and_creates_no_new_money(self):
        events = [ev("C1", "Pine", "a1", "carry", "2026-07-01T00:00+08:00",
                     "2026-07-01T01:00+08:00", "T1")]
        ids = [identity("Pine", "a1", "W1")]
        covers = [cover("PC", "Pine", "carry")]
        resolver, book, basis, timelines = assemble(events, [], ids, covers)
        claim = make_claim(paid_cover="LC", paid="100")
        result = assess_claim(claim, timelines, resolver, book, basis)
        self.assertTrue(result.already_paid)
        self.assertEqual(result.decision, "already_paid")


if __name__ == "__main__":
    unittest.main()
