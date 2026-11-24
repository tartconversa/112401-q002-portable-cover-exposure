"""接单暴露工时的认定与平台分摊。

分摊规则（逐原子切片）：
1. 至少一个有效平台在线才有一份暴露；挂起、全员离线/未确定则无暴露；
2. 实际承运（carry）优先于纯等单（wait）；
3. 存在实际承运时，承运平台平分该切片暴露（不论其是否参保——工时份额与
   保费份额是两层）；纯等单时仅由当段在线且有对应等单合同的平台平分；
4. 未补报断点形成的 unknown 平台：
   - 与已知在线平台同段时，已知份额照常认定，unknown 只进入上界；
   - 切片仅由 unknown 覆盖时，下界为 0，上界按"任一 unknown 平台在线"列示，
     归属写 UNDETERMINED，绝不把少报平台的工时挪给另一家。
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Cover, PlatformState, TimelineSlice, WorkState
from .timeutil import contract_month, hours

UNDETERMINED = "UNDETERMINED"


@dataclass(frozen=True)
class ExposureSegment:
    month: str
    person: str
    zone: str
    start: object
    end: object
    status: str                 # carry / wait / undetermined
    platform: str               # UNDETERMINED 表示归属未确定
    share_hours: float          # 该平台在本切片的下界暴露工时
    lower_hours: float          # 切片确定暴露（仅下界段非零）
    upper_extra_hours: float    # 仅在断点假设下才可能增加的工时
    insured: bool
    cover_id: str | None
    rate_per_hour: object | None
    sources: tuple[str, ...]
    note: str = ""
    candidate_platforms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SliceAssessment:
    """单切片的完整结论，供报案时点判断复用。"""

    month: str
    zone: str
    status: str
    lower_shares: dict[str, float]
    upper_options: dict[str, float]
    carriers: tuple[PlatformState, ...]
    waiters: tuple[PlatformState, ...]
    unknowns: tuple[PlatformState, ...]
    has_unknown: bool


class CoverBook:
    """合同目录：按 月/平台/工作区/状态 定位，同 cover_id 双状态共用额度。"""

    def __init__(self, covers: list[Cover]):
        self.covers = covers
        self._index = {(c.month, c.platform, c.zone, c.state): c for c in covers}

    def find(self, month: str, platform: str, zone: str,
             state: WorkState) -> Cover | None:
        return self._index.get((month, platform, zone, state))

    def by_id(self, cover_id: str) -> list[Cover]:
        return [c for c in self.covers if c.cover_id == cover_id]


def assess_slice(slice_: TimelineSlice, cover_book: CoverBook) -> SliceAssessment | None:
    """返回切片认定；无任何在场平台时返回 None。"""
    month = contract_month(slice_.start)
    unknowns = tuple(p for p in slice_.platform_states if p.unknown)
    known = tuple(p for p in slice_.active_states if not p.unknown)
    if not known and not unknowns:
        return None

    carriers = tuple(p for p in known if p.state == WorkState.CARRY)
    waiters = tuple(p for p in known if p.state == WorkState.WAIT)
    width = hours(slice_.start, slice_.end)
    lower: dict[str, float] = {}
    upper: dict[str, float] = {}

    if carriers:
        status = WorkState.CARRY.value
        part = width / len(carriers)
        for p in carriers:
            lower[p.platform] = lower.get(p.platform, 0.0) + part
        # 断点平台若实际在线也只能参与上界；其若承运则与已知承运者重新平分
        for p in unknowns:
            upper[p.platform] = width / (len(carriers) + 1)
    elif waiters:
        status = WorkState.WAIT.value
        insured_waiters = [p for p in waiters
                           if cover_book.find(month, p.platform, slice_.zone,
                                              WorkState.WAIT) is not None]
        pool = insured_waiters or waiters  # 全未参保时仍记录工时，保费层为 0
        part = width / len(pool)
        for p in pool:
            lower[p.platform] = lower.get(p.platform, 0.0) + part
        for p in unknowns:
            upper[p.platform] = width / (len(pool) + 1)
    else:
        # 切片只有未补报断点：下界为 0，上界按每个可能平台列示
        status = "undetermined"
        for p in unknowns:
            upper[p.platform] = width

    return SliceAssessment(
        month=month, zone=slice_.zone, status=status,
        lower_shares=lower, upper_options=upper,
        carriers=carriers, waiters=waiters, unknowns=unknowns,
        has_unknown=bool(unknowns))


def split_timeline(person: str, timeline, cover_book: CoverBook,
                   month: str | None = None) -> list[ExposureSegment]:
    """把一个人的时间轴展开为暴露段（可先按合同月过滤）。"""
    segments: list[ExposureSegment] = []
    for slice_ in timeline.slices:
        if month is not None and contract_month(slice_.start) != month:
            continue
        assessment = assess_slice(slice_, cover_book)
        if assessment is None:
            continue
        width = hours(slice_.start, slice_.end)
        sources = tuple(sorted({s for p in slice_.platform_states for s in p.source}))
        if assessment.status == "undetermined":
            candidates = tuple(sorted(assessment.upper_options))
            for platform, upper_hours in assessment.upper_options.items():
                segments.append(_segment(
                    assessment, person, slice_, UNDETERMINED, 0.0, 0.0,
                    upper_hours, sources,
                    note=f"未补报断点：候选平台 {platform}，下界为0，上界待定",
                    candidate_platforms=candidates))
            continue
        state = WorkState(assessment.status)
        for platform, share in sorted(assessment.lower_shares.items()):
            cover = cover_book.find(assessment.month, platform, slice_.zone, state)
            segments.append(_segment(
                assessment, person, slice_, platform, share, share,
                0.0, sources, cover=cover))
        for platform, upper_hours in sorted(assessment.upper_options.items()):
            segments.append(_segment(
                assessment, person, slice_, platform, 0.0, 0.0,
                upper_hours, sources,
                note="未补报断点：平台若在线可能增加的上界工时"))
    return segments


def _segment(assessment: SliceAssessment, person: str, slice_: TimelineSlice,
             platform: str, share: float, lower: float, upper_extra: float,
             sources: tuple[str, ...], cover: Cover | None = None,
             note: str = "",
             candidate_platforms: tuple[str, ...] = ()) -> ExposureSegment:
    return ExposureSegment(
        month=assessment.month, person=person, zone=slice_.zone or "UNKNOWN",
        start=slice_.start, end=slice_.end, status=assessment.status,
        platform=platform, share_hours=share, lower_hours=lower,
        upper_extra_hours=upper_extra,
        insured=cover is not None,
        cover_id=cover.cover_id if cover else None,
        rate_per_hour=cover.rate_per_hour if cover else None,
        sources=sources, note=note, candidate_platforms=candidate_platforms)


def slice_at(timeline, instant, cover_book: CoverBook
             ) -> tuple[TimelineSlice, SliceAssessment] | None:
    """定位某事故时刻所在切片及其认定（左闭右开，端点相接不计入）。"""
    for slice_ in timeline.slices:
        if slice_.start <= instant < slice_.end:
            return slice_, assess_slice(slice_, cover_book)
    return None
