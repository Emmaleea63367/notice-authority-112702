"""来源记录装载与登记册。

来源是五个标准 JSONL，另可叠加送达员在 ``local/`` 下追加的同构记录。
所有记录按全局 ``seq`` 合并，便于按到达顺序还原“迟到的更正/签收”。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .model import (
    DeliveryEvent,
    DocketDecision,
    DocumentRecord,
    IdentityLink,
    Interval,
    MandateRevision,
    PersonId,
)

SOURCES_DIR = Path(__file__).resolve().parent.parent / "sources"
LOCAL_DIR = Path(__file__).resolve().parent.parent / "local"

# 每个来源允许的字段，保持与既有演练资料一致。
SOURCE_FIELDS: dict[str, frozenset[str]] = {
    "identity": frozenset({"seq", "principal", "person", "verified"}),
    "mandates": frozenset(
        {"seq", "ref", "rev", "person", "holder", "case", "actions", "from", "until"}
    ),
    "documents": frozenset(
        {"seq", "notice", "rev", "case", "holder", "kind", "personal_ack"}
    ),
    "delivery": frozenset(
        {"seq", "attempt", "notice", "rev", "principal", "result", "occurred"}
    ),
    "docket": frozenset(
        {"seq", "decision", "notice", "rev", "basis", "accepted_on"}
    ),
}

DELIVERY_RESULTS = frozenset({"offered", "pending", "acknowledged", "absent"})


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path} 第 {line_number} 行不是对象")
            rows.append(row)
    return rows


def load_rows(
    sources_dir: Path = SOURCES_DIR,
    local_dir: Path = LOCAL_DIR,
) -> dict[str, list[dict]]:
    """合并标准来源与本地追加记录，各来源内按 seq 排序。"""
    merged: dict[str, list[dict]] = {name: [] for name in SOURCE_FIELDS}
    for directory in (sources_dir, local_dir):
        if not directory.exists():
            continue
        for name in merged:
            path = directory / f"{name}.jsonl"
            for row in _rows(path):
                extra = set(row) - SOURCE_FIELDS[name]
                if extra:
                    raise ValueError(f"{name} 出现未知字段 {sorted(extra)}")
                merged[name].append(row)
    for name, rows in merged.items():
        seqs = [row["seq"] for row in rows]
        if len(seqs) != len(set(seqs)):
            raise ValueError(f"{name} 存在重复到达序号")
        rows.sort(key=lambda row: row["seq"])
    return merged


@dataclass
class Snapshot:
    """某一到达时点（seq 上限）看到的全部记录。"""

    identities: list[IdentityLink]
    mandates: list[MandateRevision]
    documents: list[DocumentRecord]
    delivery: list[DeliveryEvent]
    docket: list[DocketDecision]
    max_seq: int | None = None

    @classmethod
    def from_rows(
        cls,
        rows: dict[str, list[dict]],
        through: int | None = None,
    ) -> "Snapshot":
        def arrived(name: str) -> list[dict]:
            return [
                row for row in rows[name] if through is None or row["seq"] <= through
            ]

        identities = [
            IdentityLink(
                seq=row["seq"],
                principal=row["principal"],
                person=PersonId.parse(row["person"]),
                verified=bool(row["verified"]),
            )
            for row in arrived("identity")
        ]
        mandates = [
            MandateRevision(
                seq=row["seq"],
                ref=row["ref"],
                rev=int(row["rev"]),
                person=PersonId.parse(row["person"]),
                holder=PersonId.parse(row["holder"]),
                case_id=row["case"],
                actions=frozenset(row["actions"]),
                interval=Interval(start=int(row["from"]), end=row["until"]),
            )
            for row in arrived("mandates")
        ]
        documents = [
            DocumentRecord(
                seq=row["seq"],
                notice=row["notice"],
                rev=int(row["rev"]),
                case_id=row["case"],
                holder=PersonId.parse(row["holder"]),
                kind=row["kind"],
                personal_ack=bool(row["personal_ack"]),
            )
            for row in arrived("documents")
        ]
        delivery: list[DeliveryEvent] = []
        for row in arrived("delivery"):
            if row["result"] not in DELIVERY_RESULTS:
                raise ValueError(f"未知收件结果 {row['result']!r}")
            delivery.append(
                DeliveryEvent(
                    seq=row["seq"],
                    attempt=row["attempt"],
                    notice=row["notice"],
                    rev=int(row["rev"]),
                    principal=row["principal"],
                    result=row["result"],
                    occurred=row["occurred"],
                )
            )
        docket = [
            DocketDecision(
                seq=row["seq"],
                decision=row["decision"],
                notice=row["notice"],
                rev=int(row["rev"]),
                basis=row["basis"],
                accepted_on=int(row["accepted_on"]),
            )
            for row in arrived("docket")
        ]
        max_seq = max(
            (row["seq"] for source in rows.values() for row in source),
            default=None,
        )
        if through is not None:
            max_seq = through if max_seq is not None else min(through, 0)
        return cls(
            identities=identities,
            mandates=mandates,
            documents=documents,
            delivery=delivery,
            docket=docket,
            max_seq=max_seq,
        )


@dataclass
class IdentityBook:
    """登录主体到登记机关人员的关联册。"""

    links: list[IdentityLink] = field(default_factory=list)

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> "IdentityBook":
        # 同一 principal 若多次出现，以到达序号最大者为准。
        latest: dict[str, IdentityLink] = {}
        for link in snapshot.identities:
            current = latest.get(link.principal)
            if current is None or link.seq > current.seq:
                latest[link.principal] = link
        return cls(list(latest.values()))

    def resolve(self, principal: str) -> PersonId | None:
        """只有已核准的关联才能把登录身份落到具体人员。"""
        for link in self.links:
            if link.principal == principal and link.verified:
                return link.person
        return None

    def is_verified(self, principal: str) -> bool:
        link = next((item for item in self.links if item.principal == principal), None)
        return link is not None and link.verified


@dataclass
class MandateBook:
    """代理许可修订册，按登记项 ref 保留同一 ref 的最新完整修订。"""

    revisions: list[MandateRevision] = field(default_factory=list)

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> "MandateBook":
        latest: dict[str, MandateRevision] = {}
        for revision in sorted(snapshot.mandates, key=lambda item: item.seq):
            current = latest.get(revision.ref)
            if current is None or revision.rev > current.rev or (
                revision.rev == current.rev and revision.seq > current.seq
            ):
                latest[revision.ref] = revision
        return cls(list(latest.values()))

    def effective_revisions(self, moment: int) -> list[MandateRevision]:
        """时刻 moment 仍处于生效区间内的最新修订。"""
        return [
            revision
            for revision in self.revisions
            if revision.interval.covers(moment)
        ]

    def revisions_for(
        self,
        holder: PersonId,
        case_id: str,
        person: PersonId,
        moment: int,
    ) -> list[MandateRevision]:
        return [
            revision
            for revision in self.effective_revisions(moment)
            if revision.holder == holder
            and revision.case_id == case_id
            and revision.person == person
        ]

    def actions_for(
        self,
        holder: PersonId,
        case_id: str,
        person: PersonId,
        moment: int,
    ) -> frozenset[str]:
        """同一代理人可持有多份单项许可，动作取并集；撤销后区间不覆盖即为空。"""
        granted: set[str] = set()
        for revision in self.revisions_for(holder, case_id, person, moment):
            granted.update(revision.actions)
        return frozenset(granted)
