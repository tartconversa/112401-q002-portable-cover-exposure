"""时间与跨月切分规则。"""
import unittest
from datetime import datetime

from tests import _bootstrap  # noqa: F401
from assurance.timeutil import (CONTRACT_TZ, contract_month, hours,
                                month_bounds, parse_dt, split_by_month)


def dt(text: str) -> datetime:
    return parse_dt(text)


class TimeTests(unittest.TestCase):
    def test_parse_naive_uses_contract_timezone(self):
        moment = dt("2026-07-01T00:00")
        self.assertEqual(moment.tzinfo, CONTRACT_TZ)
        self.assertEqual(contract_month(moment), "2026-07")

    def test_offset_normalized_to_contract_zone(self):
        moment = dt("2026-06-30T16:00+00:00")  # UTC 16:00 == +08 次日 00:00
        self.assertEqual(contract_month(moment), "2026-07")

    def test_month_bounds_are_midnight_in_contract_zone(self):
        start, end = month_bounds("2026-06")
        self.assertEqual(start, dt("2026-06-01T00:00+08:00"))
        self.assertEqual(end, dt("2026-07-01T00:00+08:00"))

    def test_split_cross_midnight_interval_at_contract_boundary(self):
        pieces = split_by_month(dt("2026-06-30T23:15+08:00"),
                                dt("2026-07-01T00:30+08:00"))
        self.assertEqual([p[0] for p in pieces], ["2026-06", "2026-07"])
        self.assertEqual(hours(*pieces[0][1:]), 0.75)
        self.assertEqual(hours(*pieces[1][1:]), 0.5)

    def test_december_rollover(self):
        start, end = month_bounds("2026-12")
        self.assertEqual(contract_month(end), "2027-01")

    def test_reversed_interval_rejected(self):
        with self.assertRaises(ValueError):
            split_by_month(dt("2026-07-01T01:00+08:00"),
                           dt("2026-07-01T00:00+08:00"))


if __name__ == "__main__":
    unittest.main()
