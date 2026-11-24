"""身份解析：只在有生效身份关联的区间认定同一人。

规则：
1. 仅采用基准日（含）前收到的关联记录；被已受理裁定直接指定版本的关联，
   即使到件晚于基准日也随裁定生效；
2. 同一本地代号存在多版本时，以该代号可见的最高版本确定归属：代号改判给
   新人后，旧版本对旧人不再生效，但不影响该人在其他平台账号上的关联；
3. 独立裁定强制某联系人适用指定版本（R2 类）时，强制仅作用于存在该版本
   记录的账号，联系人其余账号按常规版本规则认定；
4. 关联有适用日期范围，超出范围不认定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .basis import Basis
from .models import IdentityLink


@dataclass
class IdentityResolver:
    links: list[IdentityLink]
    basis: Basis
    _visible: list[IdentityLink] = field(default_factory=list, init=False)
    _latest_by_account: dict[tuple[str, str], IdentityLink] = field(
        default_factory=dict, init=False)
    _forced: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._forced = self.basis.forced_identity_versions()
        self._visible = [
            ln for ln in self.links
            if ln.received <= self.basis.as_of
            or (ln.person in self._forced
                and ln.version == self._forced[ln.person])]
        latest: dict[tuple[str, str], IdentityLink] = {}
        for link in self._visible:
            key = (link.platform, link.account)
            if key not in latest or link.version > latest[key].version:
                latest[key] = link
        self._latest_by_account = latest

    def _eligible(self, link: IdentityLink, day: date) -> bool:
        if not link.applies_on(day):
            return False
        key = (link.platform, link.account)
        latest = self._latest_by_account.get(key)
        # 代号的最新归属已不是此人：旧版本关联失效（改判/强制均走此路）
        if latest is not None and latest.person != link.person:
            return False
        forced_version = self._forced.get(link.person)
        if forced_version is not None:
            account_has_forced = any(
                ln.platform == key[0] and ln.account == key[1]
                and ln.version == forced_version for ln in self._visible)
            if account_has_forced and link.version != forced_version:
                return False
        return True

    def resolve_account(self, platform: str, account: str,
                        moment: datetime) -> IdentityLink | None:
        """本地代号在某时刻归属哪个联系人；无生效关联返回 None。"""
        day = moment.date()
        candidates = [ln for ln in self._visible
                      if ln.platform == platform and ln.account == account
                      and self._eligible(ln, day)]
        if not candidates:
            return None
        return max(candidates, key=lambda ln: ln.version)

    def accounts_for(self, person: str, moment: datetime) -> list[IdentityLink]:
        """某联系人在某时刻生效的全部关联（可跨多个平台）。"""
        day = moment.date()
        return [ln for ln in self._visible
                if ln.person == person and self._eligible(ln, day)]

    def all_persons(self) -> list[str]:
        return sorted({ln.person for ln in self._visible})
