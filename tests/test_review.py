"""异议、裁定事实码解释、合同额度一致性。"""
import unittest
from datetime import date
from decimal import Decimal

from tests import _bootstrap  # noqa: F401
from tests.factories import cover
from assurance.models import Objection, Ruling
from assurance.review import open_objections
from assurance.rulings import interpret
from assurance.settlement import cover_limit_map


class RulingCodeTests(unittest.TestCase):
    def test_recognizes_activity_fact(self):
        r = interpret(Ruling("R1", "X7", "confirmed-actual-carry",
                             date(2026, 7, 6)))
        self.assertEqual(r.effect.value, "activity")
        self.assertEqual(r.event_id, "X7")

    def test_recognizes_identity_fact(self):
        r = interpret(Ruling("R2", "K1",
                             "person-W01-identity-link-v2-applies",
                             date(2026, 7, 6)))
        self.assertEqual(r.effect.value, "identity")
        self.assertEqual(r.person, "W01")
        self.assertEqual(r.version, 2)

    def test_unknown_fact_code_rejected(self):
        with self.assertRaises(ValueError):
            interpret(Ruling("RX", "X9", "some-new-fact", date(2026, 7, 6)))


class ObjectionTests(unittest.TestCase):
    def test_open_objections_filter(self):
        obs = [
            Objection("O1", "K1", "份额异议", date(2026, 7, 7)),
            Objection("O2", "K3", "身份异议", date(2026, 7, 7),
                      status="accepted")]
        self.assertEqual([o.objection for o in open_objections(obs)], ["O1"])


class CoverLimitTests(unittest.TestCase):
    def test_dual_state_shares_one_limit_not_summed(self):
        covers = [cover("PC", "Pine", "carry", limit="600"),
                  cover("PC", "Pine", "wait", limit="600")]
        limits = cover_limit_map(covers, month="2026-07")
        self.assertEqual(limits, {"PC": Decimal("600")})

    def test_inconsistent_dual_state_limit_rejected(self):
        covers = [cover("PC", "Pine", "carry", limit="600"),
                  cover("PC", "Pine", "wait", limit="700")]
        with self.assertRaises(ValueError):
            cover_limit_map(covers, month="2026-07")

    def test_limit_map_filtered_by_month(self):
        covers = [cover("PC", "Pine", "carry", limit="600", month="2026-07"),
                  cover("PC", "Pine", "carry", limit="800", month="2026-06")]
        self.assertEqual(cover_limit_map(covers, month="2026-07"),
                         {"PC": Decimal("600")})


if __name__ == "__main__":
    unittest.main()
