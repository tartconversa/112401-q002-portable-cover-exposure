"""身份关联：版本可见性、改判易主、裁定强制、适用日期范围。"""
import unittest
from datetime import date

from tests import _bootstrap  # noqa: F401
from assurance.basis import Basis
from assurance.identity import IdentityResolver
from assurance.models import IdentityLink, Ruling
from assurance.timeutil import parse_dt


def link(platform, account, person, vfrom, vto, version, received):
    return IdentityLink(platform, account, person, date.fromisoformat(vfrom),
                        date.fromisoformat(vto), version,
                        date.fromisoformat(received))


def ruling(rid, person, version, received):
    return Ruling(rid, person,
                  f"person-{person}-identity-link-v{version}-applies",
                  date.fromisoformat(received))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.links = [
            link("Lake", "b77", "W01", "2026-06-01", "2026-08-01", 1, "2026-06-02"),
            link("Lake", "b77", "W03", "2026-07-01", "2026-08-01", 2, "2026-07-06"),
            link("Pine", "a05", "W01", "2026-06-01", "2026-08-01", 1, "2026-06-01"),
        ]

    def test_future_version_invisible_before_receipt(self):
        basis = Basis.baseline("base", date(2026, 7, 5))
        resolver = IdentityResolver(self.links, basis)
        owner = resolver.resolve_account("Lake", "b77",
                                         parse_dt("2026-07-01T00:17+08:00"))
        self.assertEqual(owner.person, "W01")  # v2 未到件，仍归旧人

    def test_version_reassignment_moves_account_to_new_person(self):
        basis = Basis.baseline("base", date(2026, 7, 6))
        resolver = IdentityResolver(self.links, basis)
        owner = resolver.resolve_account("Lake", "b77",
                                         parse_dt("2026-07-01T00:17+08:00"))
        self.assertEqual(owner.person, "W03")
        # 旧人不再凭该账号出现，但其 Pine 账号不受影响
        pine = resolver.resolve_account("Pine", "a05",
                                        parse_dt("2026-07-01T00:40+08:00"))
        self.assertEqual(pine.person, "W01")

    def test_ruling_forces_version_even_when_link_visible(self):
        basis = Basis.adjudicated("adj", date(2026, 7, 6),
                                  [ruling("R2", "W01", 2, "2026-07-06")])
        resolver = IdentityResolver(self.links, basis)
        # b77 的 v2 归 W03；W01 被强制 v2 但在 b77 上无 v2 记录，故 b77 不再属 W01
        self.assertIsNone(next(
            (ln for ln in resolver.accounts_for(
                "W01", parse_dt("2026-07-01T00:12+08:00"))
             if ln.account == "b77"), None))
        # W01 的 Pine v1 关联不受针对 b77 的裁定影响
        pine = resolver.accounts_for("W01", parse_dt("2026-07-01T00:40+08:00"))
        self.assertEqual({ln.account for ln in pine}, {"a05"})

    def test_link_outside_validity_window_does_not_apply(self):
        links = self.links + [
            link("Lake", "b99", "W09", "2026-08-01", "2026-09-01", 1,
                 "2026-06-01")]
        basis = Basis.baseline("base", date(2026, 7, 6))
        resolver = IdentityResolver(links, basis)
        self.assertIsNone(resolver.resolve_account(
            "Lake", "b99", parse_dt("2026-07-15T00:00+08:00")))
        self.assertIsNotNone(resolver.resolve_account(
            "Lake", "b99", parse_dt("2026-08-15T00:00+08:00")))


if __name__ == "__main__":
    unittest.main()
