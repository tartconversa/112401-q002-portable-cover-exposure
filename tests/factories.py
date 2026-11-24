"""测试数据构造小工具：直接生成领域对象，不依赖 CSV。"""
from datetime import date, datetime
from decimal import Decimal

from assurance.models import (ActivityEvent, Cover, GapDeclaration,
                              IdentityLink, WorkState)
from assurance.timeutil import parse_dt


def ev(event_id, platform, account, state, start, end, zone="Z",
       task=None, received="2026-07-01"):
    return ActivityEvent(
        event_id=event_id, platform=platform, account=account, zone=zone,
        state=WorkState(state), start=parse_dt(start), end=parse_dt(end),
        task=task, received=date.fromisoformat(received))


def gap(gid, platform, account, start, end, resolved=None):
    return GapDeclaration(
        gap=gid, platform=platform, account=account,
        start=parse_dt(start), end=parse_dt(end),
        resolved=date.fromisoformat(resolved) if resolved else None)


def identity(platform, account, person, vfrom="2026-06-01",
             vto="2026-09-01", version=1, received="2026-06-01"):
    return IdentityLink(platform, account, person,
                        date.fromisoformat(vfrom), date.fromisoformat(vto),
                        version, date.fromisoformat(received))


def cover(cid, platform, state, limit="1000", rate="10", zone="Z",
          month="2026-07"):
    return Cover(cid, month, platform, zone, WorkState(state),
                 Decimal(limit), Decimal(rate))
