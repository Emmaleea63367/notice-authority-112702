"""权利规则单元测试：身份核准、动作准许、本人确认、撤销与本人保留权。"""
import unittest

from workbench.books import IdentityBook, MandateBook, Snapshot
from workbench.model import (
    DocumentRecord,
    IdentityLink,
    Interval,
    MandateRevision,
    PersonId,
)
from workbench.rules import can_ack, can_view

A27 = PersonId("A", "27")
A35 = PersonId("A", "35")
B41 = PersonId("B", "41")


def document(kind: str = "formal", personal_ack: bool = False) -> DocumentRecord:
    return DocumentRecord(
        seq=1,
        notice="n-x",
        rev=1,
        case_id="case-81",
        holder=A27,
        kind=kind,
        personal_ack=personal_ack,
    )


def identity(verified: bool = True) -> IdentityBook:
    return IdentityBook(
        [
            IdentityLink(seq=1, principal="portal:1", person=A35, verified=verified),
            IdentityLink(seq=2, principal="portal:9", person=A27, verified=True),
        ]
    )


def mandate(actions: list[str], interval: Interval, ref: str = "A:m1") -> MandateBook:
    return MandateBook(
        [
            MandateRevision(
                seq=1,
                ref=ref,
                rev=1,
                person=A35,
                holder=A27,
                case_id="case-81",
                actions=frozenset(actions),
                interval=interval,
            )
        ]
    )


class IdentityTest(unittest.TestCase):
    def test_unverified_link_cannot_represent_person(self):
        view = can_view(document(), "portal:1", 5, identity(False), MandateBook())
        ack = can_ack(document(), "portal:1", 5, identity(False), MandateBook())
        self.assertFalse(view.allowed)
        self.assertEqual(view.reason, "identity_unverified")
        self.assertFalse(ack.allowed)
        self.assertEqual(ack.reason, "identity_unverified")

    def test_unknown_principal_is_treated_as_unverified(self):
        verdict = can_view(document(), "portal:xxx", 5, identity(), MandateBook())
        self.assertEqual(verdict.reason, "identity_unverified")


class MandateRulesTest(unittest.TestCase):
    def test_formal_document_requires_ack_action(self):
        book = mandate(["open"], Interval(0, 30))
        verdict = can_ack(document("formal"), "portal:1", 10, identity(), book)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "action_missing")

    def test_ordinary_notice_ack_allowed_with_open_only(self):
        book = mandate(["open"], Interval(0, 30))
        verdict = can_ack(document("ordinary"), "portal:1", 10, identity(), book)
        self.assertTrue(verdict.allowed)

    def test_supply_only_grants_no_view_or_ack(self):
        book = mandate(["supply"], Interval(0, 30))
        view = can_view(document(), "portal:1", 10, identity(), book)
        ack = can_ack(document("ordinary"), "portal:1", 10, identity(), book)
        self.assertEqual(view.reason, "action_missing")
        self.assertEqual(ack.reason, "action_missing")

    def test_revocation_interval_removes_rights_but_holder_keeps_own(self):
        # 区间缩短到 [0, 10)，t=12 时代理许可已失效。
        book = mandate(["open", "ack"], Interval(0, 10))
        agent = can_ack(document(), "portal:1", 12, identity(), book)
        self.assertEqual(agent.reason, "no_mandate")
        holder = can_ack(document(), "portal:9", 12, identity(), book)
        self.assertTrue(holder.allowed)
        self.assertTrue(holder.as_holder)

    def test_separate_mandate_rows_union_actions(self):
        book = MandateBook(
            [
                MandateRevision(
                    seq=1,
                    ref="A:m1",
                    rev=1,
                    person=A35,
                    holder=A27,
                    case_id="case-81",
                    actions=frozenset(["open"]),
                    interval=Interval(0, 30),
                ),
                MandateRevision(
                    seq=2,
                    ref="A:m2",
                    rev=1,
                    person=A35,
                    holder=A27,
                    case_id="case-81",
                    actions=frozenset(["ack"]),
                    interval=Interval(0, 30),
                ),
            ]
        )
        verdict = can_ack(document("formal"), "portal:1", 10, identity(), book)
        self.assertTrue(verdict.allowed)
        self.assertEqual(set(verdict.actions), {"open", "ack"})


class PersonalAckTest(unittest.TestCase):
    def test_agent_can_read_but_cannot_start_clock_for_personal_document(self):
        book = mandate(["open", "ack"], Interval(0, 30))
        personal = document("personal", personal_ack=True)
        view = can_view(personal, "portal:1", 10, identity(), book)
        ack = can_ack(personal, "portal:1", 10, identity(), book)
        self.assertTrue(view.allowed)
        self.assertFalse(ack.allowed)
        self.assertEqual(ack.reason, "personal_holder_only")

    def test_holder_can_ack_personal_document(self):
        verdict = can_ack(
            document("personal", True), "portal:9", 10, identity(), MandateBook()
        )
        self.assertTrue(verdict.allowed)
        self.assertTrue(verdict.as_holder)


class RevisionReplacementTest(unittest.TestCase):
    def test_later_complete_revision_replaces_interval(self):
        snapshot = Snapshot(
            identities=[],
            mandates=[
                MandateRevision(
                    seq=1,
                    ref="A:m1",
                    rev=1,
                    person=A35,
                    holder=A27,
                    case_id="case-81",
                    actions=frozenset(["open", "ack"]),
                    interval=Interval(0, 80),
                ),
                MandateRevision(
                    seq=2,
                    ref="A:m1",
                    rev=2,
                    person=A35,
                    holder=A27,
                    case_id="case-81",
                    actions=frozenset(["open", "ack"]),
                    interval=Interval(0, 10),
                ),
            ],
            documents=[],
            delivery=[],
            docket=[],
        )
        book = MandateBook.from_snapshot(snapshot)
        self.assertEqual(
            book.actions_for(A27, "case-81", A35, 9), frozenset({"open", "ack"})
        )
        self.assertEqual(book.actions_for(A27, "case-81", A35, 12), frozenset())


if __name__ == "__main__":
    unittest.main()
