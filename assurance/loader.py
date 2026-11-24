"""CSV 资料装入。严格按 README 的读取契约：UTF-8、首行列名、空单元格为缺值。"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .basis import Basis
from .models import (ActivityEvent, Claim, ClaimState, Cover, GapDeclaration,
                     IdentityLink, Objection, Ruling, WorkState)
from .timeutil import parse_day, parse_dt


def _money(text: str | None) -> Decimal:
    if text is None or not text.strip():
        return Decimal("0")
    try:
        return Decimal(text.strip())
    except InvalidOperation as exc:
        raise ValueError(f"金额无法解析：{text!r}") from exc


def _optional_day(text: str | None) -> datetime | None:
    if text is None or not text.strip():
        return None
    return parse_day(text)


@dataclass
class DataPack:
    """一次装入的全部来源资料。"""

    identities: list[IdentityLink]
    activities: list[ActivityEvent]
    gaps: list[GapDeclaration]
    covers: list[Cover]
    claims: list[Claim]
    rulings: list[Ruling]

    def basis(self, name: str, as_of, with_rulings: bool = False) -> Basis:
        rulings = self.rulings if with_rulings else []
        if with_rulings:
            rulings = [r for r in self.rulings if r.received <= as_of]
        return Basis(name=name, as_of=as_of, rulings=tuple(rulings))


def load_pack(directory: str | Path) -> DataPack:
    directory = Path(directory)
    return DataPack(
        identities=_load_identity(directory / "identity.csv"),
        activities=_load_activity(directory / "activity.csv"),
        gaps=_load_gap(directory / "gaps.csv"),
        covers=_load_cover(directory / "cover.csv"),
        claims=_load_claim(directory / "claims.csv"),
        rulings=_load_ruling(directory / "rulings.csv"),
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        rows = []
        for row in reader:
            if None in row or None in row.values():
                raise ValueError(f"{path.name} 存在列数不齐或空列名行：{row}")
            rows.append({k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()})
        return rows


def _load_identity(path: Path) -> list[IdentityLink]:
    links = []
    for row in _rows(path):
        links.append(IdentityLink(
            platform=row["platform"], account=row["account"], person=row["person"],
            valid_from=parse_day(row["from"]), valid_to=parse_day(row["to"]),
            version=int(row["version"]), received=parse_day(row["received"])))
    return links


def _load_activity(path: Path) -> list[ActivityEvent]:
    events = []
    for row in _rows(path):
        start, end = parse_dt(row["start"]), parse_dt(row["end"])
        if end <= start:
            raise ValueError(f"活动 {row['event_id']} 时段非正：{start} ~ {end}")
        events.append(ActivityEvent(
            event_id=row["event_id"], platform=row["platform"], account=row["account"],
            zone=row["zone"], state=WorkState(row["state"]), start=start, end=end,
            task=row["task"] or None, received=parse_day(row["received"])))
    return events


def _load_gap(path: Path) -> list[GapDeclaration]:
    gaps = []
    for row in _rows(path):
        start, end = parse_dt(row["start"]), parse_dt(row["end"])
        if end <= start:
            raise ValueError(f"断点 {row['gap']} 时段非正：{start} ~ {end}")
        gaps.append(GapDeclaration(
            gap=row["gap"], platform=row["platform"], account=row["account"],
            start=start, end=end, resolved=_optional_day(row["resolved"])))
    return gaps


def _load_cover(path: Path) -> list[Cover]:
    covers = []
    for row in _rows(path):
        covers.append(Cover(
            cover_id=row["cover_id"], month=row["month"], platform=row["platform"],
            zone=row["zone"], state=WorkState(row["state"]),
            limit=_money(row["limit"]), rate_per_hour=_money(row["rate_per_hour"])))
    return covers


def _load_claim(path: Path) -> list[Claim]:
    claims = []
    for row in _rows(path):
        state = row["state"]
        ClaimState(state)  # 仅校验来源回执状态合法
        claims.append(Claim(
            claim=row["claim"], person=row["person"], zone=row["zone"],
            instant=parse_dt(row["instant"]), requested=_money(row["requested"]),
            received=parse_dt(row["received"]), state=state,
            paid_cover=row["paid_cover"] or None, paid=_money(row["paid"])))
    return claims


def _load_ruling(path: Path) -> list[Ruling]:
    rulings = []
    for row in _rows(path):
        rulings.append(Ruling(
            ruling=row["ruling"], record=row["record"],
            accepted_fact=row["accepted_fact"], received=parse_day(row["received"])))
    return rulings


def load_objections(path: str | Path) -> list[Objection]:
    """异议资料为可选输入：objection,record,reason,received[,status]。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for row in _rows(path):
        out.append(Objection(
            objection=row["objection"], record=row["record"], reason=row["reason"],
            received=parse_day(row["received"]),
            status=row.get("status", "open") or "open"))
    return out
