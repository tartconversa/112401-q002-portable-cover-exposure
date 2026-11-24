"""合同时间工具：UTC+08:00 日界、ISO 8601、左闭右开时段。"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

CONTRACT_TZ = ZoneInfo("Asia/Shanghai")
MONTH_FMT = "%Y-%m"
DATE_FMT = "%Y-%m-%d"


def parse_dt(text: str) -> datetime:
    """解析 ISO 8601 时间；无偏移时按合同时区解释。"""
    text = text.strip()
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CONTRACT_TZ)
    return moment.astimezone(CONTRACT_TZ)


def parse_day(text: str) -> date:
    """解析日期字段（如 received、resolved、version 关联的 from/to）。"""
    text = text.strip()
    return date.fromisoformat(text)


def contract_month(moment: datetime) -> str:
    """某时刻所属合同月（按 UTC+08:00 日界）。"""
    return moment.astimezone(CONTRACT_TZ).strftime(MONTH_FMT)


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """合同月的 [起, 止) 时刻，均为 UTC+08:00 零点。"""
    start_day = datetime.strptime(month, MONTH_FMT).replace(tzinfo=CONTRACT_TZ)
    year, mon = start_day.year, start_day.month
    if mon == 12:
        end_day = start_day.replace(year=year + 1, month=1)
    else:
        end_day = start_day.replace(month=mon + 1)
    return start_day, end_day


def split_by_month(start: datetime, end: datetime) -> list[tuple[str, datetime, datetime]]:
    """把左闭右开时段按合同月切开，返回 (月, 子段起, 子段止)。"""
    if end <= start:
        raise ValueError(f"非法时段：{start} >= {end}")
    pieces: list[tuple[str, datetime, datetime]] = []
    cursor = start
    while cursor < end:
        month = contract_month(cursor)
        _, month_end = month_bounds(month)
        piece_end = min(end, month_end)
        pieces.append((month, cursor, piece_end))
        cursor = piece_end
    return pieces


def hours(start: datetime, end: datetime) -> float:
    """时段小时数（秒级精确，展示层再做量化）。"""
    return (end - start).total_seconds() / 3600.0


def now_contract_day() -> date:
    """当前合同日期。"""
    return datetime.now(tz=CONTRACT_TZ).date()
