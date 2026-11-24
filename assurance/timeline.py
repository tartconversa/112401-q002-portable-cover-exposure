"""人维度时间轴：把各平台活动与断档切成原子切片。

认定要点：
- 只纳入基准日前收到的活动上报；被已受理裁定直接引用的事件随裁定生效；
- 已补报断点（resolved <= 基准日）不再制造不确定性，窗口内以上报为准，
  窗口内无上报即视为无平台在场；
- 未补报断点内：有上报的部分按观测认定并标注断点来源，无上报的部分
  记为 unknown（只能参与上界，不能凭空算给任何平台）；
- 同一平台同一切片多状态并存时，实际承运优先，其次等单，挂起最低；
- 挂起（pause）不属于在线；
- 跨月/跨身份边界的长事件逐切片重新认定归属，同一事件可分属不同联系人。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from .basis import Basis
from .identity import IdentityResolver
from .intervals import overlap
from .models import (ActivityEvent, GapDeclaration, PlatformState,
                     TimelineSlice, WorkState)
from .timeutil import CONTRACT_TZ

_STATE_RANK = {WorkState.CARRY: 3, WorkState.WAIT: 2, WorkState.PAUSE: 1}


@dataclass
class PersonTimeline:
    person: str
    slices: list[TimelineSlice]


@dataclass
class TimelineBuildResult:
    timelines: dict[str, PersonTimeline]
    unassigned_events: list[ActivityEvent]
    """有上报但在基准下全程无生效身份关联的活动（不计入任何人的暴露）。"""


def _gap_open(gap: GapDeclaration, basis: Basis) -> bool:
    return gap.resolved is None or gap.resolved > basis.as_of


def _slices_with_cuts(sources, extra_cuts) -> list[tuple[datetime, datetime]]:
    """原子切片，并允许注入额外切点（如身份易主零点）。"""
    cuts: set[datetime] = set(extra_cuts)
    for start, end in sources:
        if end <= start:
            raise ValueError(f"非法时段：{start} >= {end}")
        cuts.add(start)
        cuts.add(end)
    ordered = sorted(cuts)
    covered = [(a, b) for a, b in zip(ordered, ordered[1:]) if a < b]
    spans = [(s, e) for s, e in sources]
    return [(a, b) for a, b in covered
            if any(s <= a and b <= e for s, e in spans)]


def _day_start(day) -> datetime:
    return datetime.combine(day, time(), tzinfo=CONTRACT_TZ)


def _probe_moments(visible, gaps, resolver: IdentityResolver) -> list[datetime]:
    """归属可能发生变化的全部时刻：事件/断点端点 + 关联版本生效零点。"""
    moments = ({e.start for e in visible} | {e.end for e in visible}
               | {g.start for g in gaps} | {g.end for g in gaps})
    for link in resolver._visible:
        moments.add(_day_start(link.valid_from))
        moments.add(_day_start(link.valid_to))
    return sorted(moments)


def _owners_over_interval(resolver: IdentityResolver, start: datetime,
                          end: datetime, platform: str,
                          account: str) -> set[str]:
    """某账号在 [start, end) 内各探测时刻归属过的全部联系人。"""
    owners: set[str] = set()
    for link in resolver._visible:
        if link.platform != platform or link.account != account:
            continue
        link_start = max(start, _day_start(link.valid_from))
        link_end = min(end, _day_start(link.valid_to))
        if link_start < link_end:
            resolved = resolver.resolve_account(platform, account, link_start)
            if resolved is not None:
                owners.add(resolved.person)
    return owners


def build_timelines(activities: list[ActivityEvent],
                    gaps: list[GapDeclaration],
                    resolver: IdentityResolver,
                    basis: Basis) -> TimelineBuildResult:
    confirmed = basis.confirmed_carry_events()
    for event_id in confirmed:
        if not any(e.event_id == event_id for e in activities):
            raise ValueError(f"裁定 {event_id} 确认的活动记录在资料中不存在")

    visible = [e for e in activities
               if e.received <= basis.as_of or e.event_id in confirmed]
    accounts = sorted({(e.platform, e.account) for e in visible}
                      | {(g.platform, g.account) for g in gaps})
    probe_moments = _probe_moments(visible, gaps, resolver)

    persons: set[str] = set()
    unassigned: list[ActivityEvent] = []
    for event in visible:
        owners = _owners_over_interval(resolver, event.start, event.end,
                                       event.platform, event.account)
        if not owners:
            unassigned.append(event)
        persons |= owners
    for gap in gaps:
        persons |= _owners_over_interval(resolver, gap.start, gap.end,
                                         gap.platform, gap.account)

    timelines: dict[str, PersonTimeline] = {}
    for person in sorted(persons):
        owned_accounts = _accounts_of_person(resolver, person, accounts,
                                             probe_moments)
        events = [e for e in visible
                  if (e.platform, e.account) in owned_accounts]
        person_gap_list = [g for g in gaps
                           if (g.platform, g.account) in owned_accounts]
        # 身份版本生效零点也是切片切点，保证跨身份的长事件在易主处被切开
        identity_cuts = [
            m for m in probe_moments
            if any((e.platform, e.account) in owned_accounts
                   and e.start < m < e.end for e in events)
            or any((g.platform, g.account) in owned_accounts
                   and g.start < m < g.end for g in person_gap_list)]
        slices: list[TimelineSlice] = []
        for lo, hi in _slices_with_cuts(
                [e.interval for e in events]
                + [g.interval for g in person_gap_list], identity_cuts):
            if not _slice_belongs(person, events, person_gap_list, resolver, lo):
                continue
            slices.append(_build_slice(person, lo, hi, events, person_gap_list,
                                       resolver, basis, confirmed))
        timelines[person] = PersonTimeline(person=person, slices=slices)
    return TimelineBuildResult(timelines=timelines, unassigned_events=unassigned)


def _accounts_of_person(resolver: IdentityResolver, person: str,
                        accounts, moments) -> set[tuple[str, str]]:
    owned: set[tuple[str, str]] = set()
    for platform, account in accounts:
        for moment in moments:
            link = resolver.resolve_account(platform, account, moment)
            if link is not None and link.person == person:
                owned.add((platform, account))
                break
    return owned


def _slice_belongs(person: str, events, gaps,
                   resolver: IdentityResolver, lo) -> bool:
    touched = {(e.platform, e.account) for e in events if e.start <= lo < e.end}
    touched |= {(g.platform, g.account) for g in gaps if g.start <= lo < g.end}
    for platform, account in touched:
        link = resolver.resolve_account(platform, account, lo)
        if link is not None and link.person == person:
            return True
    return False


def _build_slice(person: str, lo, hi, events, gaps, resolver: IdentityResolver,
                 basis: Basis, confirmed: set[str]) -> TimelineSlice:
    zone: str | None = None
    by_platform: dict[str, list[ActivityEvent]] = {}
    gap_platforms: dict[str, list[GapDeclaration]] = {}
    unknown_platforms: dict[str, str | None] = {}

    def owner_is_person(platform: str, account: str) -> bool:
        link = resolver.resolve_account(platform, account, lo)
        return link is not None and link.person == person

    for event in events:
        if overlap(event.start, event.end, lo, hi) is None:
            continue
        if not owner_is_person(event.platform, event.account):
            continue  # 跨身份长事件：该切片已归别人，不混入此人时间轴
        by_platform.setdefault(event.platform, []).append(event)
        zone = event.zone
    for gap in gaps:
        if overlap(gap.start, gap.end, lo, hi) is None:
            continue
        if not owner_is_person(gap.platform, gap.account):
            continue
        gap_platforms.setdefault(gap.platform, []).append(gap)
        if _gap_open(gap, basis) and gap.platform not in by_platform:
            unknown_platforms[gap.platform] = gap.account

    platform_states: list[PlatformState] = []
    for platform, platform_events in by_platform.items():
        # 挂起压制同切片的等单/承运：该平台此段不属于在线
        if any(e.state == WorkState.PAUSE for e in platform_events):
            chosen = next(e for e in platform_events
                          if e.state == WorkState.PAUSE)
            src = tuple(sorted({e.event_id for e in platform_events}))
            platform_states.append(PlatformState(
                platform=platform, account=chosen.account,
                state=WorkState.PAUSE, task=None, source=src, unknown=False))
            continue
        chosen = max(platform_events, key=lambda e: _STATE_RANK[e.state])
        tasks = sorted({e.task for e in platform_events if e.task})
        src = tuple(sorted({e.event_id for e in platform_events}))
        open_gaps = tuple(sorted(g.gap for g in gap_platforms.get(platform, [])
                                 if _gap_open(g, basis)))
        if chosen.event_id in confirmed:
            src = src + (f"ruling:{chosen.event_id}",)
        platform_states.append(PlatformState(
            platform=platform, account=chosen.account, state=chosen.state,
            task="|".join(tasks) if tasks else None,
            source=src + open_gaps, unknown=False))

    for platform, account in unknown_platforms.items():
        open_gaps = tuple(sorted(g.gap for g in gap_platforms[platform]
                                 if _gap_open(g, basis)))
        platform_states.append(PlatformState(
            platform=platform, account=account, state=None,
            source=open_gaps, unknown=True))

    return TimelineSlice(start=lo, end=hi, zone=zone or "",
                         platform_states=tuple(platform_states))
