"""报案核定：按事故时段与承保边界枚举可承担合同及互斥责任组合。

关键纪律：
- 实际承运优先；只有纯等单时才使用等单保障条款；
- 一个时段同时缺证跨 carry/wait 两种状态 -> pending（待核）；
- 表面时间重叠但身份关联不支持 -> identity_recheck（工时份额与报案资格重查）；
- 互斥的责任解释只为同一宗事故保留；未确定平台不得直接挤掉确定承运者。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .basis import Basis
from .exposure import CoverBook, slice_at
from .identity import IdentityResolver
from .models import Claim, WorkState
from .timeline import PersonTimeline
from .timeutil import contract_month

ELIGIBLE = "eligible"
PENDING = "pending"
IDENTITY_RECHECK = "identity_recheck"
NO_COVER = "no_cover"
ALREADY_PAID = "already_paid"


@dataclass(frozen=True)
class LiabilityCombination:
    """一组互斥的责任解释：cover_id -> 建议承担金额。"""

    combo_id: str
    rationale: str
    allocations: dict[str, Decimal]
    contingent: bool          # True：依赖尚未裁定的假设
    state_basis: str          # carry / wait / unknown


@dataclass(frozen=True)
class ClaimAssessment:
    claim: Claim
    month: str
    decision: str
    person_on_timeline: str | None
    combinations: tuple[LiabilityCombination, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)
    already_paid: bool = False
    """既有实际给付事实：只可核对/更正，不产生新的可领金额。"""

    @property
    def confirmed_covers(self) -> set[str]:
        """确定责任组合占用的合同（非待裁假设）。"""
        return {cid for c in self.combinations if not c.contingent
                for cid in c.allocations}

    @property
    def contingent_covers(self) -> set[str]:
        return {cid for c in self.combinations if c.contingent
                for cid in c.allocations}


def assess_claim(claim: Claim,
                 timelines: dict[str, PersonTimeline],
                 resolver: IdentityResolver,
                 cover_book: CoverBook,
                 basis: Basis) -> ClaimAssessment:
    month = contract_month(claim.instant)
    notes: list[str] = []

    # 1) 身份重查：事故时刻该联系人是否有生效关联、时间轴上是否真是此人
    links = resolver.accounts_for(claim.person, claim.instant)
    if not links:
        return ClaimAssessment(claim, month, IDENTITY_RECHECK, None, (),
                               ("事故时刻无生效身份关联，需重新核查代号归属",))
    timeline = timelines.get(claim.person)
    if timeline is None or slice_at(timeline, claim.instant, cover_book) is None:
        return ClaimAssessment(claim, month, IDENTITY_RECHECK, None, (),
                               ("表面时间重叠但事故时刻该联系人无在场切片",))

    located = slice_at(timeline, claim.instant, cover_book)
    assert located is not None
    slice_, assessment = located
    combos: list[LiabilityCombination] = []

    # 2) 确定承运者 -> 承运合同平分
    if assessment.carriers:
        allocations: dict[str, Decimal] = {}
        missing: list[str] = []
        part = claim.requested / len(assessment.carriers)
        for platform_state in assessment.carriers:
            cover = cover_book.find(month, platform_state.platform,
                                    claim.zone, WorkState.CARRY)
            if cover is None:
                missing.append(platform_state.platform)
                continue
            allocations[cover.cover_id] = allocations.get(cover.cover_id, Decimal("0")) + part
        if missing:
            notes.append(f"承运平台 {','.join(missing)} 在 {month} 无对应承运合同")
        if allocations:
            combos.append(LiabilityCombination(
                combo_id="C0", rationale="事故时段实际承运，承运合同平分",
                allocations=_round_alloc(allocations, claim.requested),
                contingent=False, state_basis=WorkState.CARRY.value))

    # 3) 纯等单 -> 等单保障条款（仅参保平台）
    elif assessment.waiters:
        allocations = {}
        for platform_state in assessment.waiters:
            cover = cover_book.find(month, platform_state.platform,
                                    claim.zone, WorkState.WAIT)
            if cover is not None:
                allocations[cover.cover_id] = Decimal("0")
        if allocations:
            part = claim.requested / len(allocations)
            for cid in allocations:
                allocations[cid] = part
            combos.append(LiabilityCombination(
                combo_id="W0", rationale="事故时段纯等单，适用等单保障条款",
                allocations=_round_alloc(allocations, claim.requested),
                contingent=False, state_basis=WorkState.WAIT.value))
        else:
            notes.append("等单平台均无等单保障合同")

    # 4) 未补报断点 -> 待核：为每个可能平台列互斥假设
    if assessment.unknowns:
        for idx, platform_state in enumerate(assessment.unknowns, start=1):
            carry_cover = cover_book.find(month, platform_state.platform,
                                          claim.zone, WorkState.CARRY)
            wait_cover = cover_book.find(month, platform_state.platform,
                                         claim.zone, WorkState.WAIT)
            target = carry_cover or wait_cover
            if target is None:
                notes.append(
                    f"断点平台 {platform_state.platform} 在 {month} 无适用合同，假设不产生额度占用")
                continue
            basis_state = (WorkState.CARRY.value if carry_cover
                           else WorkState.WAIT.value)
            combos.append(LiabilityCombination(
                combo_id=f"U{idx}",
                rationale=f"未补报断点假设：{platform_state.platform} 在线并承担",
                allocations={target.cover_id: claim.requested},
                contingent=True, state_basis=basis_state))
        notes.append("缺证跨状态，责任组合待独立裁定确认或排除")

    # 5) 已知等单但缺证可能升级为承运 -> 待核组合
    if assessment.waiters and assessment.unknowns and not assessment.carriers:
        notes.append("事故时段已知为等单，断点若补报为承运则责任份额改变")

    if not combos:
        return ClaimAssessment(claim, month, NO_COVER, claim.person, (),
                               tuple(notes), already_paid=bool(claim.paid_cover))
    decision = PENDING if any(c.contingent for c in combos) and \
        not any(not c.contingent for c in combos) else ELIGIBLE
    # 同时存在确定与待裁组合时仍为 eligible，但待裁组合以或然形式保留
    if any(c.contingent for c in combos) and any(not c.contingent for c in combos):
        decision = PENDING

    # 既有给付事实：责任组合仅用于核对原给付是否正确，不得形成新的可领金额。
    # 若当前认定责任与原给付合同不一致，交逆向更正流程（差额转下期）。
    if claim.paid_cover and claim.paid > 0:
        paid_note = (
            f"原已由 {claim.paid_cover} 实际给付 {claim.paid} 元，给付固化可核对；"
            "下列组合只用于责任核对，不产生新给付，错赔走逆向更正")
        notes = tuple(notes) + (paid_note,)
        return ClaimAssessment(claim, month, ALREADY_PAID, claim.person,
                               tuple(combos), notes, already_paid=True)
    return ClaimAssessment(claim, month, decision, claim.person,
                           tuple(combos), tuple(notes))


def _round_alloc(allocations: dict[str, Decimal],
                 total: Decimal) -> dict[str, Decimal]:
    """平分后尾差并入第一个合同，保证组合金额合计等于请求额。"""
    from .settlement import q_money
    rounded = {cid: q_money(amount) for cid, amount in allocations.items()}
    diff = total - sum(rounded.values(), Decimal("0"))
    if diff:
        first = next(iter(rounded))
        rounded[first] += diff
    return rounded


def assess_all(claims: list[Claim],
               timelines: dict[str, PersonTimeline],
               resolver: IdentityResolver,
               cover_book: CoverBook,
               basis: Basis) -> list[ClaimAssessment]:
    return [assess_claim(c, timelines, resolver, cover_book, basis) for c in claims]
