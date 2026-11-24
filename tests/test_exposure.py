"""暴露分摊：承运优先、并行平分、等单参保平分、挂起不计、断点上下界。"""
import unittest
from datetime import date
from decimal import Decimal

from tests import _bootstrap  # noqa: F401
from tests.factories import cover, ev, gap, identity
from assurance.basis import Basis
from assurance.exposure import CoverBook, UNDETERMINED
from assurance.identity import IdentityResolver
from assurance.models import WorkState
from assurance.settlement import build_segments
from assurance.timeline import build_timelines

T0 = "2026-07-01T00:00+08:00"


def run(events, gaps_, identities, covers, as_of="2026-07-02"):
    basis = Basis.baseline("t", date.fromisoformat(as_of))
    resolver = IdentityResolver(identities, basis)
    result = build_timelines(events, gaps_, resolver, basis)
    book = CoverBook(covers)
    return build_segments(result, book, "2026-07"), result, book


def share_map(segments, status=None):
    out = {}
    for s in segments:
        if status and s.status != status:
            continue
        out.setdefault(s.platform, Decimal("0"))
        out[s.platform] += Decimal(str(round(s.share_hours, 4)))
    return out


class ExposureTests(unittest.TestCase):
    def test_single_carrier_gets_full_hour(self):
        segments, _, _ = run(
            [ev("E1", "Pine", "a1", "carry", T0, "2026-07-01T01:00+08:00",
                task="T1")], [],
            [identity("Pine", "a1", "W1")],
            [cover("PC", "Pine", "carry")])
        self.assertEqual(share_map(segments, "carry"), {"Pine": Decimal("1.0000")})

    def test_parallel_carriers_split_equally(self):
        events = [
            ev("E1", "Pine", "a1", "carry", T0, "2026-07-01T01:00+08:00", "T1"),
            ev("E2", "Lake", "b1", "carry", T0, "2026-07-01T01:00+08:00", "T2")]
        ids = [identity("Pine", "a1", "W1"), identity("Lake", "b1", "W1")]
        covers = [cover("PC", "Pine", "carry"), cover("LC", "Lake", "carry")]
        segments, _, _ = run(events, [], ids, covers)
        self.assertEqual(share_map(segments, "carry"),
                         {"Pine": Decimal("0.5000"), "Lake": Decimal("0.5000")})

    def test_carry_overrides_wait_within_same_slice(self):
        events = [
            ev("W", "Pine", "a1", "wait", T0, "2026-07-01T01:00+08:00"),
            ev("C", "Lake", "b1", "carry", T0, "2026-07-01T01:00+08:00", "T9")]
        ids = [identity("Pine", "a1", "W1"), identity("Lake", "b1", "W1")]
        covers = [cover("PC", "Pine", "wait"), cover("PC", "Pine", "carry"),
                  cover("LC", "Lake", "carry")]
        segments, _, _ = run(events, [], ids, covers)
        # 存在实际承运：等单方不参与该段暴露平分
        self.assertEqual(share_map(segments, "carry"), {"Lake": Decimal("1.0000")})
        self.assertNotIn("wait", {s.status for s in segments})

    def test_wait_split_only_among_insured_platforms(self):
        events = [
            ev("W1", "Pine", "a1", "wait", T0, "2026-07-01T01:00+08:00"),
            ev("W2", "Lake", "b1", "wait", T0, "2026-07-01T01:00+08:00")]
        ids = [identity("Pine", "a1", "W1"), identity("Lake", "b1", "W1")]
        # Lake 无等单合同
        covers = [cover("PC", "Pine", "wait")]
        segments, _, _ = run(events, [], ids, covers)
        wait_segments = [s for s in segments if s.status == "wait"]
        self.assertEqual({s.platform for s in wait_segments}, {"Pine"})
        self.assertEqual(share_map(wait_segments), {"Pine": Decimal("1.0000")})
        # 未参保平台不进入等单平分；进入的 Pine 段有费率，费用在汇总层计
        self.assertTrue(all(s.insured for s in wait_segments))

    def test_pause_is_not_online_and_suppresses_wait(self):
        events = [
            ev("W", "Lake", "b1", "wait", T0, "2026-07-01T01:00+08:00"),
            ev("P", "Lake", "b1", "pause", "2026-07-01T00:30+08:00",
                "2026-07-01T01:00+08:00")]
        ids = [identity("Lake", "b1", "W1")]
        covers = [cover("LC", "Lake", "wait"), cover("LC", "Lake", "carry")]
        segments, result, _ = run(events, [], ids, covers)
        # 00:30-01:00 挂起段无暴露；仅 00:00-00:30 等单
        self.assertEqual(share_map(segments, "wait"), {"Lake": Decimal("0.5000")})

    def test_unresolved_gap_with_no_report_is_undetermined(self):
        gaps_ = [gap("G1", "Lake", "b1", T0, "2026-07-01T01:00+08:00")]
        ids = [identity("Lake", "b1", "W1")]
        segments, _, _ = run([], gaps_, ids, [cover("LC", "Lake", "carry")])
        undet = [s for s in segments if s.platform == UNDETERMINED]
        self.assertEqual(len(undet), 1)
        self.assertEqual(undet[0].lower_hours, 0.0)          # 下界为 0
        self.assertEqual(undet[0].upper_extra_hours, 1.0)    # 上界 1 小时
        self.assertEqual(undet[0].share_hours, 0.0)

    def test_resolved_gap_does_not_create_uncertainty(self):
        events = [ev("C", "Lake", "b1", "carry", T0,
                     "2026-07-01T01:00+08:00", task="T", received="2026-07-03")]
        gaps_ = [gap("G1", "Lake", "b1", T0, "2026-07-01T01:00+08:00",
                     resolved="2026-07-03")]
        ids = [identity("Lake", "b1", "W1")]
        segments, _, _ = run(events, gaps_, ids,
                             [cover("LC", "Lake", "carry")], as_of="2026-07-04")
        self.assertFalse(any(s.platform == UNDETERMINED for s in segments))
        self.assertEqual(share_map(segments, "carry"), {"Lake": Decimal("1.0000")})

    def test_underreporting_is_not_reassigned_to_other_platform(self):
        events = [ev("C", "Pine", "a1", "carry", T0,
                     "2026-07-01T01:00+08:00", task="T")]
        gaps_ = [gap("G1", "Lake", "b1", "2026-07-01T01:00+08:00",
                     "2026-07-01T02:00+08:00")]
        ids = [identity("Pine", "a1", "W1"), identity("Lake", "b1", "W1")]
        covers = [cover("PC", "Pine", "carry"), cover("LC", "Lake", "carry")]
        segments, _, _ = run(events, gaps_, ids, covers)
        # Pine 只拿自己确定的 1 小时，不因 Lake 断点而增加
        pine = [s for s in segments if s.platform == "Pine"]
        self.assertEqual(sum(s.share_hours for s in pine), 1.0)
        undet = [s for s in segments if s.platform == UNDETERMINED]
        self.assertTrue(undet)  # Lake 缺口单独挂未确定，不挪给 Pine

    def test_known_event_inside_open_gap_still_counts_to_lower_bound(self):
        events = [ev("C", "Lake", "b1", "carry", T0,
                     "2026-07-01T00:30+08:00", task="T")]
        gaps_ = [gap("G1", "Lake", "b1", T0, "2026-07-01T01:00+08:00")]
        ids = [identity("Lake", "b1", "W1")]
        segments, _, _ = run(events, gaps_, ids, [cover("LC", "Lake", "carry")])
        known = [s for s in segments if s.platform == "Lake" and s.lower_hours]
        self.assertEqual(sum(s.lower_hours for s in known), 0.5)
        undet = [s for s in segments if s.platform == UNDETERMINED]
        self.assertEqual(sum(s.upper_extra_hours for s in undet), 0.5)


if __name__ == "__main__":
    unittest.main()
