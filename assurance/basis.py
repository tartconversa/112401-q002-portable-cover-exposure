"""认定基准（Basis）：同一套引擎在不同证据时点重跑的开关。

月结与报案资料必须"可反复装入"：
- ``as_of``：只装入该日（含）之前收到的平台上报与补报；
- ``rulings``：该基准下已受理的独立裁定；
- 身份裁定可强制某联系人采用特定版本关联，即使该版本资料到件更早也不例外。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .models import Ruling
from .rulings import InterpretedRuling, interpret_all


@dataclass(frozen=True)
class Basis:
    name: str
    as_of: date
    rulings: tuple[Ruling, ...] = field(default_factory=tuple)

    @property
    def interpreted(self) -> list[InterpretedRuling]:
        return interpret_all(list(self.rulings))

    def forced_identity_versions(self) -> dict[str, int]:
        """裁定强制的 联系人 -> 关联版本。"""
        forced: dict[str, int] = {}
        for item in self.interpreted:
            if item.effect.value == "identity" and item.person and item.version:
                forced[item.person] = item.version
        return forced

    def confirmed_carry_events(self) -> set[str]:
        """裁定确认的实际承运 event_id 集合。"""
        return {item.event_id for item in self.interpreted
                if item.effect.value == "activity" and item.event_id}

    @classmethod
    def baseline(cls, name: str, as_of: date) -> "Basis":
        """原月结基准：无裁定。"""
        return cls(name=name, as_of=as_of)

    @classmethod
    def adjudicated(cls, name: str, as_of: date, rulings: list[Ruling]) -> "Basis":
        """裁定后基准：纳入全部已受理裁定。"""
        return cls(name=name, as_of=as_of, rulings=tuple(rulings))
