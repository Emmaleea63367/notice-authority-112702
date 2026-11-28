"""送达员在本地追加的记录。

本地文件与标准来源同构，按来源分文件存放在 ``local/`` 下。
``seq`` 在全部来源共享的到达序号空间内取当前最大值之后顺延，
保证迟到的委托更正与收件回执仍可按真实到达顺序重放。
"""
from __future__ import annotations

import json
from pathlib import Path

from .books import LOCAL_DIR, SOURCES_DIR, load_rows

# 各来源必填字段。
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "documents": ("notice", "rev", "case", "holder", "kind", "personal_ack"),
    "delivery": ("attempt", "notice", "rev", "principal", "result", "occurred"),
    "mandates": (
        "ref",
        "rev",
        "person",
        "holder",
        "case",
        "actions",
        "from",
        "until",
    ),
    "identity": ("principal", "person", "verified"),
    "docket": ("decision", "notice", "rev", "basis", "accepted_on"),
}

DELIVERY_RESULTS = frozenset({"offered", "pending", "acknowledged", "absent"})
DOCUMENT_KINDS = frozenset({"ordinary", "formal", "personal"})


class SubmissionError(ValueError):
    """本地提交内容不合法。"""


def next_seq(local_dir: Path = LOCAL_DIR) -> int:
    """返回下一个全局到达序号。"""
    rows = load_rows(sources_dir=SOURCES_DIR, local_dir=local_dir)
    current = max(
        (row["seq"] for source in rows.values() for row in source),
        default=0,
    )
    return current + 1


def append_record(
    source: str,
    payload: dict,
    local_dir: Path = LOCAL_DIR,
) -> dict:
    """校验后把一条记录追加到 local/<source>.jsonl，返回写入的完整行。"""
    if source not in REQUIRED_FIELDS:
        raise SubmissionError(f"未知来源 {source}")
    missing = [field for field in REQUIRED_FIELDS[source] if field not in payload]
    if missing:
        raise SubmissionError(f"缺少字段：{', '.join(missing)}")
    unknown = set(payload) - set(REQUIRED_FIELDS[source])
    if unknown:
        raise SubmissionError(f"未知字段：{', '.join(sorted(unknown))}")

    record = dict(payload)
    record["seq"] = next_seq(local_dir)
    _validate(source, record)

    local_dir.mkdir(parents=True, exist_ok=True)
    path = local_dir / f"{source}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _validate(source: str, record: dict) -> None:
    if source == "delivery":
        if record["result"] not in DELIVERY_RESULTS:
            raise SubmissionError(
                f"result 必须是 {sorted(DELIVERY_RESULTS)} 之一"
            )
        if record["result"] == "acknowledged" and not isinstance(
            record["occurred"], int
        ):
            raise SubmissionError("签收记录必须给出实际签收分钟 occurred")
    if source == "documents":
        if record["kind"] not in DOCUMENT_KINDS:
            raise SubmissionError(f"kind 必须是 {sorted(DOCUMENT_KINDS)} 之一")
        if not isinstance(record["personal_ack"], bool):
            raise SubmissionError("personal_ack 必须是布尔值")
    if source == "mandates":
        if not isinstance(record["actions"], list) or not record["actions"]:
            raise SubmissionError("actions 必须是非空数组")
        if not isinstance(record["from"], int):
            raise SubmissionError("from 必须是整数分钟")
        if record["until"] is not None and not isinstance(record["until"], int):
            raise SubmissionError("until 必须是整数分钟或 null")
    if source == "identity" and not isinstance(record["verified"], bool):
        raise SubmissionError("verified 必须是布尔值")
