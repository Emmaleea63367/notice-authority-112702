"""送达判定引擎。

引擎只依据登记册的当前状态回答历史问题：
- “签收当时有无权利”按签收事件的 occurred 时刻，代入当前已知的最新登记修订判断；
- 同一登记 ref 的旧修订已被完整替换，撤销/更正通过新区间自然生效；
- 起算只承认收件端确认的实际签收（acknowledged 的 occurred），
  办案台是否已采纳属于期限台决定，在 decision_views 中单独复核。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .books import IdentityBook, MandateBook, Snapshot, load_rows
from .model import DeliveryEvent, DocketDecision, DocumentRecord, PersonId
from .rules import RightVerdict, can_ack, can_view

# 尝试/文书聚合状态码，中文文案集中在 cli 层。
STATE_OFFERED = "offered"
STATE_PENDING = "pending"
STATE_ACKNOWLEDGED = "acknowledged"
STATE_ABSENT = "absent"
STATE_NOT_SENT = "not_sent"

# 期限决定复核状态码。
DECISION_VALID = "valid"
DECISION_REVIEW = "review"

REVIEW_REASON_TEXT: dict[str, str] = {
    "invalid_ack": "签收当时该接收人没有签收权，不能作为起算依据",
    "no_acknowledgment": "收件端没有该尝试的有效签收记录",
    "attempt_missing": "起算依据指向的收件尝试不存在",
    "accepted_before_ack": "采纳时间早于实际签收时间",
    "revision_missing": "期限决定指向的文书修订在办案台不存在",
}


@dataclass(frozen=True)
class AckJudgment:
    """对一条签收事件的权利判定。"""

    event: DeliveryEvent
    person: PersonId | None
    verdict: RightVerdict | None
    valid: bool
    reason: str


@dataclass(frozen=True)
class AttemptView:
    """一次收件尝试（同一 attempt 标识的全部状态记录）。"""

    attempt: str
    notice: str
    rev: int
    principal: str
    person: PersonId | None
    identity_verified: bool
    offer: DeliveryEvent | None
    last_event: DeliveryEvent
    state: str
    offer_view: RightVerdict | None
    acknowledgments: list[AckJudgment]

    @property
    def valid_acknowledgments(self) -> list[AckJudgment]:
        return [item for item in self.acknowledgments if item.valid]


@dataclass(frozen=True)
class ClockBasis:
    """一份文书修订当前唯一的起算依据。"""

    attempt: AttemptView
    judgment: AckJudgment

    @property
    def occurred(self) -> int:
        return self.judgment.event.occurred  # type: ignore[return-value]


@dataclass(frozen=True)
class DocumentView:
    """一份文书修订的送达全景。"""

    document: DocumentRecord
    attempts: list[AttemptView]
    clock: ClockBasis | None
    review_notice: bool = False

    @property
    def states(self) -> frozenset[str]:
        if not self.attempts:
            return frozenset({STATE_NOT_SENT})
        return frozenset(attempt.state for attempt in self.attempts)

    @property
    def valid_acks(self) -> list[AckJudgment]:
        items = [
            judgment
            for attempt in self.attempts
            for judgment in attempt.valid_acknowledgments
        ]
        return sorted(
            items,
            key=lambda item: (item.event.occurred, item.event.seq),
        )

    @property
    def invalid_acks(self) -> list[AckJudgment]:
        return [
            judgment
            for attempt in self.attempts
            for judgment in attempt.acknowledgments
            if not judgment.valid
        ]


@dataclass(frozen=True)
class DecisionView:
    """期限台一条起算决定的复核结论。"""

    decision: DocketDecision
    status: str
    reason: str | None
    attempt: AttemptView | None
    judgment: AckJudgment | None

    @property
    def suspended(self) -> bool:
        return self.status == DECISION_REVIEW

    def reason_text(self) -> str | None:
        if self.reason is None:
            return None
        return REVIEW_REASON_TEXT[self.reason]


@dataclass(frozen=True)
class InboxItem:
    """个人收件箱中的一条。"""

    document: DocumentRecord
    attempt: AttemptView | None
    state: str
    view_now: RightVerdict | None
    ack_now: RightVerdict | None


@dataclass(frozen=True)
class InboxResult:
    principal: str
    person: PersonId | None
    verified: bool
    items: list[InboxItem]


class Workbench:
    """基于某一到达时点快照的工作台。"""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.identities = IdentityBook.from_snapshot(snapshot)
        self.mandates = MandateBook.from_snapshot(snapshot)
        self._documents = self._index_documents()
        self._attempts = self._build_attempts()

    # ---- 装载 --------------------------------------------------------

    @classmethod
    def load(
        cls,
        through: int | None = None,
        sources_dir: Path | str | None = None,
        local_dir: Path | str | None = None,
    ) -> "Workbench":
        kwargs: dict[str, Path] = {}
        if sources_dir is not None:
            kwargs["sources_dir"] = Path(sources_dir)
        if local_dir is not None:
            kwargs["local_dir"] = Path(local_dir)
        rows = load_rows(**kwargs)
        return cls(Snapshot.from_rows(rows, through=through))

    def _index_documents(self) -> dict[tuple[str, int], DocumentRecord]:
        index: dict[tuple[str, int], DocumentRecord] = {}
        for document in self.snapshot.documents:
            key = (document.notice, document.rev)
            if key in index:
                raise ValueError(f"文书修订重复登记：{key}")
            index[key] = document
        return index

    # ---- 尝试聚合与权利判定 ------------------------------------------

    def _judge_ack(
        self,
        event: DeliveryEvent,
        offer: DeliveryEvent | None,
    ) -> AckJudgment:
        person = self.identities.resolve(event.principal)
        document = self._documents.get((event.notice, event.rev))
        if person is None:
            return AckJudgment(
                event=event,
                person=None,
                verdict=None,
                valid=False,
                reason="identity_unverified",
            )
        if document is None:
            return AckJudgment(
                event=event,
                person=person,
                verdict=None,
                valid=False,
                reason="revision_missing",
            )
        if offer is None:
            return AckJudgment(
                event=event,
                person=person,
                verdict=None,
                valid=False,
                reason="no_offer",
            )
        if event.occurred is None:
            return AckJudgment(
                event=event,
                person=person,
                verdict=None,
                valid=False,
                reason="missing_occurred",
            )
        verdict = can_ack(
            document,
            event.principal,
            event.occurred,
            self.identities,
            self.mandates,
        )
        return AckJudgment(
            event=event,
            person=person,
            verdict=verdict,
            valid=verdict.allowed,
            reason=verdict.reason,
        )

    def _build_attempts(self) -> dict[str, AttemptView]:
        grouped: dict[str, list[DeliveryEvent]] = {}
        for event in sorted(self.snapshot.delivery, key=lambda item: item.seq):
            grouped.setdefault(event.attempt, []).append(event)

        views: dict[str, AttemptView] = {}
        for attempt_id, events in grouped.items():
            head = events[0]
            for event in events:
                if (event.notice, event.rev, event.principal) != (
                    head.notice,
                    head.rev,
                    head.principal,
                ):
                    raise ValueError(f"尝试 {attempt_id} 的文书修订或登录主体不一致")

            offer = next(
                (event for event in events if event.result == "offered"), None
            )
            last_event = events[-1]
            state = self._terminal_state(events)
            person = self.identities.resolve(head.principal)
            document = self._documents.get((head.notice, head.rev))

            offer_view: RightVerdict | None = None
            if offer is not None and document is not None and offer.occurred is not None:
                offer_view = can_view(
                    document,
                    head.principal,
                    offer.occurred,
                    self.identities,
                    self.mandates,
                )

            acknowledgments = [
                self._judge_ack(event, offer)
                for event in events
                if event.result == "acknowledged"
            ]
            views[attempt_id] = AttemptView(
                attempt=attempt_id,
                notice=head.notice,
                rev=head.rev,
                principal=head.principal,
                person=person,
                identity_verified=self.identities.is_verified(head.principal),
                offer=offer,
                last_event=last_event,
                state=state,
                offer_view=offer_view,
                acknowledgments=acknowledgments,
            )
        return views

    @staticmethod
    def _terminal_state(events: list[DeliveryEvent]) -> str:
        results = {event.result for event in events}
        if STATE_ACKNOWLEDGED in results:
            return STATE_ACKNOWLEDGED
        if STATE_ABSENT in results:
            return STATE_ABSENT
        if STATE_PENDING in results:
            return STATE_PENDING
        return STATE_OFFERED

    # ---- 文书视图 ----------------------------------------------------

    def document_views(self) -> list[DocumentView]:
        attempts_by_doc: dict[tuple[str, int], list[AttemptView]] = {
            key: [] for key in self._documents
        }
        for attempt in self._attempts.values():
            attempts_by_doc.setdefault((attempt.notice, attempt.rev), []).append(attempt)

        review_keys = {
            (view.decision.notice, view.decision.rev)
            for view in self.decision_views()
            if view.suspended
        }

        views: list[DocumentView] = []
        for key, document in self._documents.items():
            attempts = sorted(
                attempts_by_doc.get(key, []),
                key=lambda item: (
                    item.offer.occurred if item.offer and item.offer.occurred is not None else 10**9,
                    item.attempt,
                ),
            )
            valid = sorted(
                [
                    judgment
                    for attempt in attempts
                    for judgment in attempt.valid_acknowledgments
                ],
                key=lambda item: (item.event.occurred, item.event.seq),
            )
            clock = (
                ClockBasis(
                    attempt=self._attempt_for(valid[0].event.attempt),
                    judgment=valid[0],
                )
                if valid
                else None
            )
            views.append(
                DocumentView(
                    document=document,
                    attempts=attempts,
                    clock=clock,
                    review_notice=key in review_keys,
                )
            )
        return views

    def _attempt_for(self, attempt_id: str) -> AttemptView:
        return self._attempts[attempt_id]

    def document_view(self, notice: str, rev: int | None = None) -> DocumentView:
        if rev is not None:
            try:
                return next(
                    view
                    for view in self.document_views()
                    if view.document.notice == notice and view.document.rev == rev
                )
            except StopIteration:
                raise KeyError(f"找不到文书 {notice} 修订 {rev}") from None
        matches = self.document_views_for(notice)
        if not matches:
            raise KeyError(f"找不到文书 {notice}")
        return max(matches, key=lambda view: view.document.rev)

    def document_views_for(self, notice: str) -> list[DocumentView]:
        return [
            view
            for view in self.document_views()
            if view.document.notice == notice
        ]

    # ---- 期限决定复核 ------------------------------------------------

    def decision_views(self) -> list[DecisionView]:
        views: list[DecisionView] = []
        for decision in self.snapshot.docket:
            views.append(self._review_decision(decision))
        return views

    def _review_decision(self, decision: DocketDecision) -> DecisionView:
        document = self._documents.get((decision.notice, decision.rev))
        if document is None:
            return DecisionView(
                decision=decision,
                status=DECISION_REVIEW,
                reason="revision_missing",
                attempt=None,
                judgment=None,
            )
        attempt = self._attempts.get(decision.basis)
        if attempt is None or (attempt.notice, attempt.rev) != (
            decision.notice,
            decision.rev,
        ):
            return DecisionView(
                decision=decision,
                status=DECISION_REVIEW,
                reason="attempt_missing",
                attempt=attempt,
                judgment=None,
            )
        if not attempt.acknowledgments:
            return DecisionView(
                decision=decision,
                status=DECISION_REVIEW,
                reason="no_acknowledgment",
                attempt=attempt,
                judgment=None,
            )
        judgment = min(
            attempt.acknowledgments,
            key=lambda item: (item.event.occurred, item.event.seq),
        )
        if not judgment.valid:
            return DecisionView(
                decision=decision,
                status=DECISION_REVIEW,
                reason="invalid_ack",
                attempt=attempt,
                judgment=judgment,
            )
        if (
            judgment.event.occurred is None
            or decision.accepted_on < judgment.event.occurred
        ):
            return DecisionView(
                decision=decision,
                status=DECISION_REVIEW,
                reason="accepted_before_ack",
                attempt=attempt,
                judgment=judgment,
            )
        return DecisionView(
            decision=decision,
            status=DECISION_VALID,
            reason=None,
            attempt=attempt,
            judgment=judgment,
        )

    # ---- 个人收件箱 --------------------------------------------------

    def current_moment(self) -> int:
        moments = [
            event.occurred
            for event in self.snapshot.delivery
            if event.occurred is not None
        ]
        moments.extend(decision.accepted_on for decision in self.snapshot.docket)
        return max(moments, default=0)

    def inbox(self, principal: str, now: int | None = None) -> InboxResult:
        moment = self.current_moment() if now is None else now
        person = self.identities.resolve(principal)
        verified = self.identities.is_verified(principal)
        if not verified:
            # 关联未核准前不暴露任何待签收件或他人收件细节。
            return InboxResult(
                principal=principal, person=person, verified=False, items=[]
            )

        items: list[InboxItem] = []
        for view in self.document_views():
            document = view.document
            own_document = person == document.holder
            own_attempts = [
                attempt
                for attempt in view.attempts
                if attempt.principal == principal
            ]

            if own_document:
                # 当事人本人看得到本案文书的全部收件途径。
                if view.attempts:
                    for attempt in view.attempts:
                        items.append(
                            self._inbox_item(
                                document, attempt, moment, principal
                            )
                        )
                else:
                    items.append(
                        self._inbox_item(document, None, moment, principal)
                    )
                continue

            # 代理人只能看到发往自己登录身份的途径：
            # 发件当时有查看权，或当前权利仍覆盖该文书（含 absent/pending 反馈）。
            for attempt in own_attempts:
                current_view = can_view(
                    document,
                    principal,
                    moment,
                    self.identities,
                    self.mandates,
                )
                offered_ok = attempt.offer_view is not None and attempt.offer_view.allowed
                if offered_ok or current_view.allowed:
                    items.append(
                        self._inbox_item(document, attempt, moment, principal)
                    )
        return InboxResult(
            principal=principal,
            person=person,
            verified=True,
            items=sorted(
                items,
                key=lambda item: (
                    item.document.notice,
                    item.document.rev,
                    item.attempt.attempt if item.attempt else "",
                ),
            ),
        )

    def _inbox_item(
        self,
        document: DocumentRecord,
        attempt: AttemptView | None,
        moment: int,
        viewer_principal: str,
    ) -> InboxItem:
        state = attempt.state if attempt is not None else STATE_NOT_SENT
        # 当前可查看/可签收一律按查看者本人的权利展示，
        # 当事人查看代理人途径时不会借用代理人的权利结论。
        view_now = can_view(
            document,
            viewer_principal,
            moment,
            self.identities,
            self.mandates,
        )
        ack_now = can_ack(
            document,
            viewer_principal,
            moment,
            self.identities,
            self.mandates,
        )
        return InboxItem(
            document=document,
            attempt=attempt,
            state=state,
            view_now=view_now,
            ack_now=ack_now,
        )
