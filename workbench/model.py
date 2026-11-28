"""工作台领域对象。

时间一律使用相对演练起点的整数分钟；生效区间为左闭右开 ``[start, end)``，
``end is None`` 表示暂无终止时刻。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonId:
    """登记机关命名范围内的人员编号，如 ``A:27``。

    不同登记机关各有命名范围，编号只在机关范围内可比，不能跨机关等同。
    """

    authority: str
    number: str

    @classmethod
    def parse(cls, value: str) -> "PersonId":
        authority, sep, number = value.partition(":")
        if not sep or not authority or not number:
            raise ValueError(f"人员编号缺少机关命名范围：{value!r}")
        return cls(authority=authority, number=number)

    def __str__(self) -> str:
        return f"{self.authority}:{self.number}"


@dataclass(frozen=True)
class Interval:
    """左闭右开生效区间。"""

    start: int
    end: int | None

    def covers(self, moment: int) -> bool:
        if moment < self.start:
            return False
        return self.end is None or moment < self.end

    def __str__(self) -> str:
        end = "∞" if self.end is None else str(self.end)
        return f"[{self.start}, {end})"


@dataclass(frozen=True)
class IdentityLink:
    """登录核验方的一条人员关联。

    verified 为 False 时，登录主体尚不能代表登记中的具体人员。
    """

    seq: int
    principal: str
    person: PersonId
    verified: bool


@dataclass(frozen=True)
class MandateRevision:
    """代理登记处的一条完整单项许可修订。

    同一 ``ref`` 的后续修订是完整替换而非增量补丁；actions 只覆盖本行列出的动作。
    """

    seq: int
    ref: str
    rev: int
    person: PersonId
    holder: PersonId
    case_id: str
    actions: frozenset[str]
    interval: Interval


@dataclass(frozen=True)
class DocumentRecord:
    """办案台登记的一份文书修订。"""

    seq: int
    notice: str
    rev: int
    case_id: str
    holder: PersonId
    kind: str
    personal_ack: bool


@dataclass(frozen=True)
class DeliveryEvent:
    """收件端的一条状态记录。

    result 取值：offered（可打开）/ pending（待确认）/
    acknowledged（已签收）/ absent（明确无签收）。
    """

    seq: int
    attempt: str
    notice: str
    rev: int
    principal: str
    result: str
    occurred: int | None


@dataclass(frozen=True)
class DocketDecision:
    """期限台已作出的外部起算决定，basis 指向收件端尝试。"""

    seq: int
    decision: str
    notice: str
    rev: int
    basis: str
    accepted_on: int
