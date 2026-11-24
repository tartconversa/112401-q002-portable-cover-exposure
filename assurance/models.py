"""领域模型：身份关联、活动、断点、合同、报案、裁定、异议。

字段名与 samples/*.csv 的列保持一致，方便资料与系统对象互相核对。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class WorkState(str, Enum):
    """平台观测状态。pause（挂起）不属于在线。"""

    WAIT = "wait"
    CARRY = "carry"
    PAUSE = "pause"


class ClaimState(str, Enum):
    """报案处理状态（含系统内部流转态，CSV 回执只出现前两类初始态）。"""

    PAID = "paid"          # 已有实际给付事实
    REVIEW = "review"      # 待核 / 在审
    RESERVED = "reserved"  # 已占用或预留额度，待最终裁定/给付
    PAYABLE = "payable"    # 裁定后可给付
    QUEUED = "queued"      # 额度不足，按受理顺序排队
    REJECTED = "rejected"  # 无承保边界或被裁定排除
    CORRECTED = "corrected"  # 逆向更正：原给付固化，差额转下期


class RulingEffect(str, Enum):
    """裁定事实对系统假设的作用方式。"""

    ACTIVITY_FACT = "activity"      # 对活动区间的认定（如 confirmed-actual-carry）
    IDENTITY_FACT = "identity"      # 对代号归属的认定（如 v2 关联适用）


@dataclass(frozen=True)
class IdentityLink:
    """平台本地代号与演练联系人码的版本化关联。"""

    platform: str
    account: str
    person: str
    valid_from: date
    valid_to: date
    version: int
    received: date

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day < self.valid_to


@dataclass(frozen=True)
class ActivityEvent:
    """平台上报的活动区间。"""

    event_id: str
    platform: str
    account: str
    zone: str
    state: WorkState
    start: datetime
    end: datetime
    task: str
    received: date

    @property
    def interval(self) -> tuple[datetime, datetime]:
        return self.start, self.end


@dataclass(frozen=True)
class GapDeclaration:
    """收报系统断档。resolved 有值表示补报已到，空白表示未收到。"""

    gap: str
    platform: str
    account: str
    start: datetime
    end: datetime
    resolved: date | None

    @property
    def interval(self) -> tuple[datetime, datetime]:
        return self.start, self.end


@dataclass(frozen=True)
class Cover:
    """一份合同在某月、某工作区、某工作状态下的月额度与小时费率。

    同一 cover_id 分列 carry/wait 时共用一个月额度，不相加。
    """

    cover_id: str
    month: str
    platform: str
    zone: str
    state: WorkState
    limit: Decimal
    rate_per_hour: Decimal

    def matches(self, platform: str, zone: str, state: WorkState, month: str) -> bool:
        return (self.platform == platform and self.zone == zone
                and self.state == state and self.month == month)


@dataclass(frozen=True)
class Claim:
    """事故报案流水；paid_cover/paid 是此前实际给付事实，系统不得改写。"""

    claim: str
    person: str
    zone: str
    instant: datetime
    requested: Decimal
    received: datetime
    state: str
    paid_cover: str | None
    paid: Decimal


@dataclass(frozen=True)
class Ruling:
    """独立调查裁定；accepted_fact 为可机读的事实码。"""

    ruling: str
    record: str
    accepted_fact: str
    received: date


@dataclass(frozen=True)
class Objection:
    """核查员提交的异议。"""

    objection: str
    record: str
    reason: str
    received: date
    status: str = "open"  # open / accepted / rejected


@dataclass(frozen=True)
class PlatformState:
    """某平台在某切片内的观测结论。"""

    platform: str
    account: str | None
    state: WorkState | None       # None 表示断点内、状态未确定
    task: str | None = None
    source: tuple[str, ...] = field(default_factory=tuple)
    unknown: bool = False         # True：处于未补报断点，只能给上下界


@dataclass(frozen=True)
class TimelineSlice:
    """人维度时间轴的一个原子切片。"""

    start: datetime
    end: datetime
    zone: str
    platform_states: tuple[PlatformState, ...]

    @property
    def active_states(self) -> tuple[PlatformState, ...]:
        """在线平台（pause 与离线均不在内）。"""
        return tuple(p for p in self.platform_states
                     if p.state in (WorkState.WAIT, WorkState.CARRY))

    @property
    def has_unknown(self) -> bool:
        return any(p.unknown for p in self.platform_states)
