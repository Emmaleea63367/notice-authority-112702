"""命令行工作台。

子命令：
- documents  按文书修订查清送达状态、当下能否起算、旧决定是否有争议
- trace      单份文书的完整收件轨迹（区分修订，不迁移旧签收）
- decisions  期限台起算决定复核（有效/需复核并暂停）
- inbox      当事人视角的当前可查看与待确认文书
- submit     送达员受理新文书、新尝试或委托/身份变更
- seq        查看下一个可用到达序号
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .engine import (
    DECISION_REVIEW,
    DECISION_VALID,
    STATE_ABSENT,
    STATE_ACKNOWLEDGED,
    STATE_NOT_SENT,
    STATE_OFFERED,
    STATE_PENDING,
    Workbench,
)
from .rules import REASON_TEXT
from .store import LOCAL_DIR, SubmissionError, append_record

STATE_TEXT: dict[str, str] = {
    STATE_OFFERED: "已发件待处理",
    STATE_PENDING: "待确认",
    STATE_ACKNOWLEDGED: "已签收",
    STATE_ABSENT: "明确无签收",
    STATE_NOT_SENT: "未发件",
}

DECISION_TEXT: dict[str, str] = {
    DECISION_VALID: "有效，可继续办理",
    DECISION_REVIEW: "需复核：暂停依赖该起算的期限办理",
}


# ---------------------------------------------------------------- JSON


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, frozenset):
        return sorted(value)
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(_to_jsonable(payload), ensure_ascii=False, indent=2))
        return
    if isinstance(payload, str):
        print(payload)
    else:
        print("\n".join(payload))


# ------------------------------------------------------------- 展示文案


def _person(view_or_none: Any) -> str:
    person = getattr(view_or_none, "person", None)
    return str(person) if person is not None else "关联未核准"


def _verdict_text(verdict: Any) -> str:
    if verdict is None:
        return "无权利结论"
    mark = "允许" if verdict.allowed else "拒绝"
    return f"{mark}（{verdict.explain()}；动作：{sorted(verdict.actions) or '无'}）"


def _judgment_line(judgment: Any, indent: str = "    ") -> list[str]:
    event = judgment.event
    person = str(judgment.person) if judgment.person else "无法定位人员"
    lines = [
        f"{indent}签收事件 seq={event.seq} 时间={event.occurred} "
        f"登录={event.principal} 人员={person} "
        f"{'有效' if judgment.valid else '无效'}：{REASON_TEXT.get(judgment.reason, judgment.reason)}"
    ]
    return lines


def _attempt_lines(attempt: Any) -> list[str]:
    offer_time = attempt.offer.occurred if attempt.offer else None
    lines = [
        f"  尝试 {attempt.attempt}（修订 r{attempt.rev}，登录 {attempt.principal}，"
        f"人员 {_person(attempt)}）状态：{STATE_TEXT[attempt.state]}，发件时间：{offer_time}",
    ]
    if attempt.offer_view is not None:
        lines.append(f"    发件当时查看权：{_verdict_text(attempt.offer_view)}")
    if attempt.acknowledgments:
        lines.append("    签收判定：")
        for judgment in attempt.acknowledgments:
            lines.extend(_judgment_line(judgment))
    elif attempt.state == STATE_ABSENT:
        lines.append("    收件端明确反馈无签收（区别于尚未到达的回执）")
    elif attempt.state == STATE_PENDING:
        lines.append("    收件端已生成记录但仍待确认，尚未签收")
    return lines


def _clock_line(view: Any) -> list[str]:
    if view.clock is None:
        return ["  起算依据：无合法签收，期限不能开始（再次发件不制造第二个起算）"]
    basis = view.clock
    event = basis.judgment.event
    return [
        "  起算依据："
        f"尝试 {basis.attempt.attempt} 于分钟 {basis.occurred} 由 "
        f"{basis.judgment.person} 签收（事件 seq={event.seq}）"
    ]


def render_trace(view: Any) -> list[str]:
    document = view.document
    lines = [
        f"文书 {document.notice} 修订 r{document.rev}（案件 {document.case_id}，"
        f"当事人 {document.holder}，类别 {document.kind}，"
        f"{'须本人确认' if document.personal_ack else '可由代理人签收'}）",
        f"整体状态：{[STATE_TEXT[state] for state in sorted(view.states)]}",
    ]
    if view.review_notice:
        lines.append("⚠ 本修订存在已被采纳但需复核的起算决定，相关期限办理已暂停")
    if not view.attempts:
        lines.append("  尚无任何收件尝试")
    for attempt in view.attempts:
        lines.extend(_attempt_lines(attempt))
    lines.extend(_clock_line(view))
    if view.invalid_acks:
        lines.append("  无效签收保留留痕，不产生起算效力：")
        for judgment in view.invalid_acks:
            lines.extend(_judgment_line(judgment, indent="    - "))
    return lines


def render_document_row(view: Any) -> str:
    document = view.document
    states = "/".join(STATE_TEXT[state] for state in sorted(view.states))
    if view.clock:
        clock = f"可起算@分钟{view.clock.occurred}（{view.clock.attempt.attempt}）"
    else:
        clock = "不能起算"
    dispute = "；有争议决定待复核" if view.review_notice else ""
    return (
        f"{document.notice} r{document.rev} [{document.kind}] 案件 {document.case_id} "
        f"当事人 {document.holder}：{states}；{clock}{dispute}"
    )


def render_decision(decision_view: Any) -> list[str]:
    decision = decision_view.decision
    lines = [
        f"决定 {decision.decision}（{decision.notice} r{decision.rev}，"
        f"依据尝试 {decision.basis}，采纳于分钟 {decision.accepted_on}）："
        f"{DECISION_TEXT[decision_view.status]}"
    ]
    if decision_view.reason is not None:
        lines.append(f"  原因：{decision_view.reason_text()}")
    if decision_view.judgment is not None:
        lines.extend(_judgment_line(decision_view.judgment))
    lines.append("  处理：保留原签收与登记更正依据，待重新依法送达或登记复核明确后采用新依据")
    if not decision_view.suspended:
        lines[-1] = "  原签收与采纳记录继续有效"
    return lines


def render_inbox(result: Any) -> list[str]:
    if not result.verified:
        return [
            f"登录主体 {result.principal} 的身份关联尚未核准，不展示任何待签收件或收件细节"
        ]
    lines = [f"登录主体 {result.principal}（人员 {result.person}）的收件箱"]
    if not result.items:
        lines.append("  （空）")
        return lines
    for item in result.items:
        document = item.document
        attempt_id = item.attempt.attempt if item.attempt else "—"
        lines.append(
            f"- {document.notice} r{document.rev} [{document.kind}] "
            f"尝试 {attempt_id}：{STATE_TEXT[item.state]}"
        )
        lines.append(f"    当前查看：{_verdict_text(item.view_now)}")
        lines.append(f"    当前签收：{_verdict_text(item.ack_now)}")
    return lines


# --------------------------------------------------------------- 命令


def _load(args: argparse.Namespace) -> Workbench:
    return Workbench.load(through=args.through)


def cmd_documents(args: argparse.Namespace) -> None:
    workbench = _load(args)
    views = workbench.document_views()
    if args.case:
        views = [view for view in views if view.document.case_id == args.case]
    if args.json:
        emit(views, True)
    else:
        emit([render_document_row(view) for view in views], False)


def cmd_trace(args: argparse.Namespace) -> None:
    workbench = _load(args)
    if args.all_revs:
        views = workbench.document_views_for(args.notice)
        if not views:
            raise SystemExit(f"找不到文书 {args.notice}")
        if args.json:
            emit(views, True)
            return
        blocks = [render_trace(view) for view in views]
        emit(_join_blocks(blocks), False)
        return
    view = workbench.document_view(args.notice, args.rev)
    emit(view if args.json else render_trace(view), args.json)


def _join_blocks(blocks: list[list[str]]) -> list[str]:
    joined: list[str] = []
    for index, block in enumerate(blocks):
        if index:
            joined.append("")
        joined.extend(block)
    return joined


def cmd_decisions(args: argparse.Namespace) -> None:
    workbench = _load(args)
    views = workbench.decision_views()
    if args.only_review:
        views = [view for view in views if view.suspended]
    if args.json:
        emit(views, True)
        return
    lines: list[str] = []
    for view in views:
        lines.extend(render_decision(view))
    emit(lines or ["当前没有期限决定记录"], False)


def cmd_inbox(args: argparse.Namespace) -> None:
    workbench = _load(args)
    result = workbench.inbox(args.principal, now=args.now)
    emit(result if args.json else render_inbox(result), args.json)


def cmd_submit(args: argparse.Namespace) -> None:
    payload = json.loads(args.payload)
    record = append_record(args.source, payload, local_dir=Path(args.local_dir))
    emit([f"已写入 local/{args.source}.jsonl：{json.dumps(record, ensure_ascii=False)}"], False)


def cmd_seq(args: argparse.Namespace) -> None:
    from .store import next_seq

    emit([f"下一个到达序号：{next_seq(Path(args.local_dir))}"], False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地电子送达工作台")
    parser.add_argument("--through", type=int, help="只采用到达序号不超过该值的记录")
    sub = parser.add_subparsers(dest="command", required=True)

    documents = sub.add_parser("documents", help="列出文书修订的送达与起算状态")
    documents.add_argument("--case", help="按案件过滤")
    documents.add_argument("--json", action="store_true", help="输出 JSON")
    documents.set_defaults(func=cmd_documents)

    trace = sub.add_parser("trace", help="查看单份文书的完整收件轨迹")
    trace.add_argument("notice", help="文书编号，如 n-8")
    trace.add_argument("--rev", type=int, help="文书修订，缺省取最新修订")
    trace.add_argument("--all-revs", action="store_true", help="列出该文书的全部修订")
    trace.add_argument("--json", action="store_true", help="输出 JSON")
    trace.set_defaults(func=cmd_trace)

    decisions = sub.add_parser("decisions", help="复核期限台的起算决定")
    decisions.add_argument("--only-review", action="store_true", help="只看待复核项")
    decisions.add_argument("--json", action="store_true", help="输出 JSON")
    decisions.set_defaults(func=cmd_decisions)

    inbox = sub.add_parser("inbox", help="查看某登录主体的个人收件箱")
    inbox.add_argument("principal", help="登录主体，如 portal:91")
    inbox.add_argument("--now", type=int, help="核对当前权利的时刻，缺省取最新事实时刻")
    inbox.add_argument("--json", action="store_true", help="输出 JSON")
    inbox.set_defaults(func=cmd_inbox)

    submit = sub.add_parser("submit", help="追加一条本地记录")
    submit.add_argument(
        "source",
        choices=tuple(("documents", "delivery", "mandates", "identity", "docket")),
        help="目标来源文件",
    )
    submit.add_argument("payload", help="JSON 对象（不含 seq，序号自动分配）")
    submit.add_argument("--local-dir", default=str(LOCAL_DIR))
    submit.set_defaults(func=cmd_submit)

    seq = sub.add_parser("seq", help="查看下一个可用到达序号")
    seq.add_argument("--local-dir", default=str(LOCAL_DIR))
    seq.set_defaults(func=cmd_seq)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (SubmissionError, KeyError, ValueError) as exc:
        parser.exit(1, f"工作台拒绝执行：{exc}\n")


if __name__ == "__main__":
    main()
