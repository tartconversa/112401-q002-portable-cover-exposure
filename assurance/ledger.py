"""合同月度赔付额度台账。

额度占用分三层、互相区别显示：
1. ``paid``       既有实际给付，任何新流程不可挤占；
2. ``confirmed``  已由某一平台/责任组合确认占用（含确定裁定）；
3. ``contingent`` 尚有多个可能承担方、取决于未裁定组合的或然预留。

纪律：
- 同一金额不能在多起事故中重复占用；
- 可拆给付的报案按裁定责任组合分别占用，互斥解释只保留或然额度；
- 预留不能挤占既有已赔给付，冲突先交经办人（返回 Escalation）；
- 新证据确认责任 -> 或然转确认；排除责任 -> 释放，排队报案按受理顺序续办；
- 逆向更正不改写 paid，差额生成对账项转下期，报案人不会再次全额可领。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .claims import ClaimAssessment, LiabilityCombination
from .models import Claim
from .settlement import q_money


@dataclass(frozen=True)
class Reservation:
    claim: str
    cover_id: str
    amount: Decimal
    kind: str                  # confirmed / contingent
    combo_id: str
    rationale: str


@dataclass(frozen=True)
class PaymentPlanItem:
    claim: str
    cover_id: str
    amount: Decimal
    kind: str                  # pay / reserve / queue
    combo_id: str
    note: str


@dataclass(frozen=True)
class Escalation:
    claim: str
    cover_id: str
    requested_amount: Decimal
    blocked_by_paid: Decimal
    note: str


@dataclass(frozen=True)
class CorrectionItem:
    """逆向更正对账项：原给付固化，差额转下期核对。"""

    claim: str
    paid_cover: str
    paid_amount: Decimal
    correct_cover: str
    correct_amount: Decimal
    month: str
    note: str


class CoverLedger:
    def __init__(self, limits: dict[str, Decimal], month: str):
        self.month = month
        self.limits = dict(limits)
        self.paid: dict[str, Decimal] = {cid: Decimal("0") for cid in limits}
        self.confirmed: dict[str, list[Reservation]] = {cid: [] for cid in limits}
        self.contingent: dict[str, list[Reservation]] = {cid: [] for cid in limits}
        self.plan: list[PaymentPlanItem] = []
        self.escalations: list[Escalation] = []
        self.corrections: list[CorrectionItem] = []
        self._paid_claims: set[str] = set()
        # 每个合同上排队等待额度的确认占用，按受理顺序保存
        self.queued: dict[str, list[Reservation]] = {cid: [] for cid in limits}

    # ---------- 占用读数 ----------
    def paid_total(self, cover_id: str) -> Decimal:
        return self.paid.get(cover_id, Decimal("0"))

    def confirmed_total(self, cover_id: str) -> Decimal:
        return self.paid_total(cover_id) + sum(
            (r.amount for r in self.confirmed.get(cover_id, [])), Decimal("0"))

    def contingent_total(self, cover_id: str) -> Decimal:
        """同一合同上各互斥组合的或然预留取最大值，不重复相加。"""
        groups: dict[str, Decimal] = {}
        for r in self.contingent.get(cover_id, []):
            groups[r.combo_id] = groups.get(r.combo_id, Decimal("0")) + r.amount
        return max(groups.values(), default=Decimal("0"))

    def available_now(self, cover_id: str) -> Decimal:
        """当期真正可再占用额度：限额 - 已赔 - 确认预留。

        或然预留不扣减可用读数，但在 availability_view 中单独提示风险。
        """
        if cover_id not in self.limits:
            return Decimal("0")
        return q_money(self.limits[cover_id] - self.confirmed_total(cover_id))

    def availability_view(self) -> list[dict]:
        rows = []
        for cid in sorted(self.limits):
            contingent = self.contingent_total(cid)
            rows.append({
                "cover_id": cid, "month": self.month,
                "limit": self.limits[cid],
                "paid": self.paid_total(cid),
                "confirmed_reserved": q_money(
                    sum((r.amount for r in self.confirmed[cid]), Decimal("0"))),
                "contingent_reserved": q_money(contingent),
                "available_now": self.available_now(cid),
                "queued": q_money(sum((r.amount for r in self.queued.get(cid, [])),
                                      Decimal("0"))),
                "contingent_note": ("含未裁定组合，若确认将占用"
                                    if contingent else "")})
        return rows

    # ---------- 入账 ----------
    def record_paid(self, claim: Claim) -> None:
        """登记既有给付事实；不做任何重算或退回。"""
        if not claim.paid_cover or claim.paid <= 0:
            return
        if claim.claim in self._paid_claims:
            raise ValueError(f"报案 {claim.claim} 的给付已登记，禁止重复入账")
        self.paid[claim.paid_cover] = self.paid.get(
            claim.paid_cover, Decimal("0")) + claim.paid
        self._paid_claims.add(claim.claim)

    def apply_assessment(self, assessment: ClaimAssessment) -> None:
        claim = assessment.claim
        if claim.claim in self._paid_claims:
            return  # 已赔报案不产生新的额度需求
        certain = [c for c in assessment.combinations if not c.contingent]
        uncertain = [c for c in assessment.combinations if c.contingent]

        # 确定责任优先占用（同一事故的确定组合至多一个）
        for combo in certain:
            self._apply_combo(claim.claim, combo, contingent=False)
        # 互斥的待裁解释：各自以或然形式保留，不相互累加
        for combo in uncertain:
            self._apply_combo(claim.claim, combo, contingent=True)

    def _apply_combo(self, claim_id: str, combo: LiabilityCombination,
                     contingent: bool) -> None:
        for cover_id, amount in combo.allocations.items():
            reservation = Reservation(
                claim=claim_id, cover_id=cover_id, amount=amount,
                kind="contingent" if contingent else "confirmed",
                combo_id=combo.combo_id, rationale=combo.rationale)
            if contingent:
                self.contingent.setdefault(cover_id, []).append(reservation)
                self.plan.append(PaymentPlanItem(
                    claim_id, cover_id, amount, "reserve", combo.combo_id,
                    f"或然预留：{combo.rationale}"))
                continue
            self._take_confirmed(cover_id, reservation, combo)

    def _take_confirmed(self, cover_id: str, reservation: Reservation,
                        combo: LiabilityCombination) -> None:
        if cover_id not in self.limits:
            self.escalations.append(Escalation(
                reservation.claim, cover_id, reservation.amount, Decimal("0"),
                "责任合同不在当月额度目录中，交经办人处理"))
            return
        available = self.available_now(cover_id)
        if reservation.amount <= available:
            self.confirmed[cover_id].append(reservation)
            self.plan.append(PaymentPlanItem(
                reservation.claim, cover_id, reservation.amount, "pay",
                combo.combo_id, "额度内确认占用，可给付"))
            return
        # 不可挤占已赔/确认额度：能付多少付多少，余额排队，冲突升级
        if available > 0:
            part = Reservation(reservation.claim, cover_id, available,
                               reservation.kind, reservation.combo_id,
                               reservation.rationale)
            self.confirmed[cover_id].append(part)
            self.plan.append(PaymentPlanItem(
                reservation.claim, cover_id, available, "pay", combo.combo_id,
                "部分额度内给付，余额排队等待释放"))
        remaining = reservation.amount - available
        if remaining > 0:
            self.queued.setdefault(cover_id, []).append(
                Reservation(reservation.claim, cover_id, remaining,
                            reservation.kind, reservation.combo_id,
                            reservation.rationale))
            self.plan.append(PaymentPlanItem(
                reservation.claim, cover_id, remaining, "queue", combo.combo_id,
                "可用额度不足，按受理顺序排队"))
        self.escalations.append(Escalation(
            reservation.claim, cover_id, reservation.amount,
            self.confirmed_total(cover_id),
            "预留不得挤占既有已赔/确认给付，冲突交经办人处理"))

    # ---------- 裁定驱动的转换/释放 ----------
    def confirm_combo(self, claim_id: str, combo_id: str) -> list[PaymentPlanItem]:
        """新证据确认某责任组合：或然转确认，同案其他或然组合释放。

        只能确认当前以或然形式保留的组合；确定组合无需转换，重复确认将报错。
        """
        chosen = [r for r in self._all_contingent()
                  if r.claim == claim_id and r.combo_id == combo_id]
        if not chosen:
            raise ValueError(
                f"报案 {claim_id} 没有或然组合 {combo_id} 可供确认"
                "（确定组合无需转换，未知组合请先核对裁定）")
        for reservation in chosen:
            self._remove_contingent(reservation)
            self._take_confirmed(reservation.cover_id, reservation,
                                 _Combo(combo_id, reservation.rationale))
        self.release_claim(claim_id, keep_combo=combo_id)
        return chosen

    def release_claim(self, claim_id: str, keep_combo: str | None = None,
                      reprocess: bool = True) -> None:
        """排除责任：释放报案的或然预留（可保留被确认的组合）。

        释放后默认按受理顺序重办该额度上排队的确认占用。
        """
        touched_covers: set[str] = set()
        for cover_id, reservations in list(self.contingent.items()):
            kept = [
                r for r in reservations
                if not (r.claim == claim_id
                        and (keep_combo is None or r.combo_id != keep_combo))]
            if len(kept) != len(reservations):
                touched_covers.add(cover_id)
            self.contingent[cover_id] = kept
        if reprocess:
            for cover_id in touched_covers:
                self.reprocess_queue(cover_id)

    def release_confirmed(self, claim_id: str,
                          cover_id: str | None = None) -> Decimal:
        """裁定排除已确认责任：撤回该报案的确认预留并触发排队续办。

        不触碰既有 paid；返回释放出的额度。
        """
        released = Decimal("0")
        for cid, reservations in list(self.confirmed.items()):
            if cover_id is not None and cid != cover_id:
                continue
            kept = [r for r in reservations if r.claim != claim_id]
            released += sum((r.amount for r in reservations
                             if r.claim == claim_id), Decimal("0"))
            self.confirmed[cid] = kept
        if cover_id is not None:
            self.reprocess_queue(cover_id)
        else:
            for cid in self.queued:
                self.reprocess_queue(cid)
        return q_money(released)

    def reprocess_queue(self, cover_id: str) -> list[PaymentPlanItem]:
        """额度释放后，按受理顺序重办排队占用，直到额度再次用尽。"""
        produced: list[PaymentPlanItem] = []
        queue = self.queued.get(cover_id, [])
        remaining_queue: list[Reservation] = []
        for reservation in queue:
            available = self.available_now(cover_id)
            if available <= 0:
                remaining_queue.append(reservation)
                continue
            take = min(available, reservation.amount)
            part = Reservation(reservation.claim, cover_id, take,
                               reservation.kind, reservation.combo_id,
                               reservation.rationale)
            self.confirmed[cover_id].append(part)
            item = PaymentPlanItem(
                reservation.claim, cover_id, take, "pay", reservation.combo_id,
                "额度释放后按受理顺序续办")
            self.plan.append(item)
            produced.append(item)
            if reservation.amount > take:
                remaining_queue.append(Reservation(
                    reservation.claim, cover_id, reservation.amount - take,
                    reservation.kind, reservation.combo_id,
                    reservation.rationale))
        self.queued[cover_id] = remaining_queue
        return produced

    def record_correction(self, assessment: ClaimAssessment,
                          correct_cover: str, correct_amount: Decimal) -> CorrectionItem:
        """逆向更正登记：原 paid 不动，生成下期对账项；该报案不再产生给付。"""
        claim = assessment.claim
        if not claim.paid_cover:
            raise ValueError(f"报案 {claim.claim} 无既有给付，不适用更正流程")
        item = CorrectionItem(
            claim=claim.claim, paid_cover=claim.paid_cover,
            paid_amount=claim.paid, correct_cover=correct_cover,
            correct_amount=q_money(correct_amount), month=assessment.month,
            note="原给付固化可核对，差额转下期；报案人不可再次全额领款")
        self.corrections.append(item)
        self.release_claim(claim.claim)
        return item

    def _all_contingent(self) -> list[Reservation]:
        return [r for group in self.contingent.values() for r in group]

    def _remove_contingent(self, target: Reservation) -> None:
        group = self.contingent.get(target.cover_id, [])
        self.contingent[target.cover_id] = [
            r for r in group if not (r.claim == target.claim
                                     and r.combo_id == target.combo_id
                                     and r.amount == target.amount)]


@dataclass
class _Combo:
    combo_id: str
    rationale: str


def build_month_ledger(month: str, limits: dict[str, Decimal],
                       claims: list[Claim],
                       assessments: list[ClaimAssessment]) -> CoverLedger:
    """按受理顺序（claims 已排序）构建当月台账。"""
    ledger = CoverLedger(limits, month)
    by_id = {a.claim.claim: a for a in assessments}
    for claim in claims:
        ledger.record_paid(claim)
    for claim in claims:
        assessment = by_id.get(claim.claim)
        if assessment is None or claim.claim in ledger._paid_claims:
            # 已赔报案只做责任核对/逆向更正，绝不产生新的额度占用
            continue
        ledger.apply_assessment(assessment)
    return ledger
