"""接收人在具体时刻对具体文书修订的权利规则。

动作含义：
- open：可以打开/查看（普通通知以此为足）
- ack：可以签收正式文书并据以起算
- supply：只能补交材料，不产生查看或签收权利

文书类别与动作的对应：
- ordinary（普通通知）：open 即可查看与签收，同一文书可保留多条收件途径
- formal（正式文书，涉隐私）：需要单独准许的 ack 才能签收
- personal_ack=True：必须当事人本人确认；代理人即便获准 open 也不能替本人起算
"""
from __future__ import annotations

from dataclasses import dataclass

from .books import IdentityBook, MandateBook
from .model import DocumentRecord, PersonId

# 查看权所需动作。
OPEN_ACTION = "open"
# 签收起算权所需动作。
ACK_ACTION = "ack"


@dataclass(frozen=True)
class RightVerdict:
    """一次权利核对结论。"""

    allowed: bool
    reason: str
    actions: frozenset[str]
    as_holder: bool

    def explain(self) -> str:
        return REASON_TEXT[self.reason]


# 结论原因码与中文说明，展示层统一取这里的文案。
REASON_TEXT: dict[str, str] = {
    "holder_self": "当事人本人收件权始终有效",
    "mandate_granted": "代理登记在生效区间内准许该动作",
    "identity_unverified": "登录身份与登记人员的关联尚未核准，不能代表具体人员",
    "no_mandate": "该时刻没有覆盖本案、本动作的生效代理许可",
    "action_missing": "纸面委托只准许其他动作，未准许本次所需动作",
    "personal_holder_only": "文书要求当事人本人确认，代理人不能替本人起算",
}


def _actor_person(
    principal: str,
    identities: IdentityBook,
) -> tuple[PersonId | None, RightVerdict | None]:
    person = identities.resolve(principal)
    if person is None:
        return None, RightVerdict(
            allowed=False,
            reason="identity_unverified",
            actions=frozenset(),
            as_holder=False,
        )
    return person, None


def can_view(
    document: DocumentRecord,
    principal: str,
    moment: int,
    identities: IdentityBook,
    mandates: MandateBook,
) -> RightVerdict:
    """文书在 moment 可打开时，核对该登录主体是否有查看权。"""
    person, denied = _actor_person(principal, identities)
    if denied is not None:
        return denied
    assert person is not None

    if person == document.holder:
        return RightVerdict(True, "holder_self", frozenset({"open", ACK_ACTION}), True)

    actions = mandates.actions_for(document.holder, document.case_id, person, moment)
    if not actions:
        return RightVerdict(False, "no_mandate", actions, False)
    if OPEN_ACTION not in actions:
        return RightVerdict(False, "action_missing", actions, False)
    return RightVerdict(True, "mandate_granted", actions, False)


def can_ack(
    document: DocumentRecord,
    principal: str,
    moment: int,
    identities: IdentityBook,
    mandates: MandateBook,
) -> RightVerdict:
    """核对该登录主体在 moment 能否替本修订签收并起算。"""
    person, denied = _actor_person(principal, identities)
    if denied is not None:
        return denied
    assert person is not None

    # 当事人本人的收件权在代理撤销后仍然有效。
    if person == document.holder:
        return RightVerdict(True, "holder_self", frozenset({"open", ACK_ACTION}), True)

    # 需本人确认的文书：代理人即使获准读取也不能替本人起算。
    if document.personal_ack:
        actions = mandates.actions_for(
            document.holder, document.case_id, person, moment
        )
        return RightVerdict(False, "personal_holder_only", actions, False)

    actions = mandates.actions_for(document.holder, document.case_id, person, moment)
    if not actions:
        return RightVerdict(False, "no_mandate", actions, False)

    # 普通通知 open 即可签收；正式文书需要单独准许的 ack。
    required = OPEN_ACTION if document.kind == "ordinary" else ACK_ACTION
    if required not in actions:
        return RightVerdict(False, "action_missing", actions, False)
    return RightVerdict(True, "mandate_granted", actions, False)
