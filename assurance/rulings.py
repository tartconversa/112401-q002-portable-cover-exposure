"""裁定事实码的解释规则。

试点阶段约定两类可机读事实码：
- ``confirmed-actual-carry``：确认某活动记录（event_id）为实际承运；
- ``person-{P}-identity-link-v{n}-applies``：确认联系人 P 适用某版本身份关联。
无法识别的事实码显式报错，避免静默吞掉新证据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Ruling, RulingEffect

_IDENTITY_FACT = re.compile(r"^person-(?P<person>[A-Za-z0-9_]+)-identity-link-v(?P<version>\d+)-applies$")


@dataclass(frozen=True)
class InterpretedRuling:
    ruling: Ruling
    effect: RulingEffect
    person: str | None = None
    version: int | None = None
    event_id: str | None = None


def interpret(ruling: Ruling) -> InterpretedRuling:
    fact = ruling.accepted_fact.strip()
    if fact == "confirmed-actual-carry":
        return InterpretedRuling(ruling=ruling, effect=RulingEffect.ACTIVITY_FACT,
                                 event_id=ruling.record)
    match = _IDENTITY_FACT.match(fact)
    if match:
        return InterpretedRuling(
            ruling=ruling, effect=RulingEffect.IDENTITY_FACT,
            person=match.group("person"),
            version=int(match.group("version")))
    raise ValueError(f"无法识别的裁定事实码：{ruling.ruling} -> {fact!r}")


def interpret_all(rulings: list[Ruling]) -> list[InterpretedRuling]:
    return [interpret(r) for r in rulings]
