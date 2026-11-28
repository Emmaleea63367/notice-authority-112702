"""引擎端到端判定：签收效力、起算依据、决定复核、时间重放、修订隔离。"""
import unittest

from workbench.engine import (
    DECISION_REVIEW,
    DECISION_VALID,
    STATE_ABSENT,
    STATE_ACKNOWLEDGED,
    STATE_OFFERED,
    STATE_PENDING,
    Workbench,
)


class EngineFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wb = Workbench.load()

    def test_n8_rev1_ack_invalid_after_mandate_correction(self):
        view = self.wb.document_view("n-8", 1)
        self.assertEqual(view.states, frozenset({STATE_ACKNOWLEDGED}))
        # rev3 把 A:m4 区间缩短到 [0,10)，t=12 的签收当时已无权利。
        self.assertIsNone(view.clock)
        self.assertEqual(len(view.invalid_acks), 2)  # seq19 与重复的 seq26
        self.assertTrue(
            all(judgment.reason == "no_mandate" for judgment in view.invalid_acks)
        )

    def test_duplicate_ack_does_not_create_second_basis(self):
        attempt = self.wb._attempts["a1"]
        self.assertEqual(len(attempt.acknowledgments), 2)
        self.assertEqual(attempt.valid_acknowledgments, [])

    def test_n9_personal_document_holder_ack_is_valid_clock(self):
        view = self.wb.document_view("n-9", 1)
        self.assertIsNotNone(view.clock)
        self.assertEqual(view.clock.occurred, 19)
        self.assertEqual(view.clock.attempt.attempt, "a2")
        self.assertEqual(str(view.clock.judgment.person), "A:27")

    def test_n10_absent_is_distinct_from_pending(self):
        view = self.wb.document_view("n-10", 1)
        self.assertEqual(view.states, frozenset({STATE_ABSENT}))
        self.assertIsNone(view.clock)
        (attempt,) = view.attempts
        self.assertEqual(attempt.state, STATE_ABSENT)

    def test_n8_rev2_is_separate_revision_with_own_rights(self):
        old = self.wb.document_view("n-8", 1)
        new = self.wb.document_view("n-8", 2)
        self.assertIsNone(new.clock)
        self.assertEqual(new.states, frozenset({STATE_OFFERED}))
        (attempt,) = new.attempts
        self.assertEqual(attempt.attempt, "a4")
        self.assertTrue(attempt.offer_view.allowed)
        # 旧修订的签收没有被带到新修订。
        self.assertEqual(
            {item.attempt for item in new.attempts}, {"a4"}
        )
        self.assertNotEqual(
            [item.attempt for item in old.attempts],
            [item.attempt for item in new.attempts],
        )

    def test_unverified_principal_attempt_cannot_resolve_person(self):
        attempt = self.wb._attempts["a4"]
        self.assertTrue(attempt.identity_verified)
        # portal:95 未核准；直接以其身份核对必须拒绝。
        from workbench.rules import can_view
        from workbench.model import DocumentRecord

        verdict = can_view(
            self.wb._documents[("n-8", 2)],
            "portal:95",
            29,
            self.wb.identities,
            self.wb.mandates,
        )
        self.assertEqual(verdict.reason, "identity_unverified")


class DecisionReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wb = Workbench.load()
        cls.views = {view.decision.decision: view for view in cls.wb.decision_views()}

    def test_clock8_based_on_invalid_ack_is_suspended(self):
        view = self.views["clock-8"]
        self.assertEqual(view.status, DECISION_REVIEW)
        self.assertEqual(view.reason, "invalid_ack")
        self.assertTrue(view.suspended)
        self.assertIsNotNone(view.judgment)
        self.assertFalse(view.judgment.valid)

    def test_clock9_based_on_holder_ack_remains_valid(self):
        view = self.views["clock-9"]
        self.assertEqual(view.status, DECISION_VALID)
        self.assertIsNone(view.reason)
        # 暂停一份文书不影响同案其他有效决定。
        self.assertFalse(view.suspended)

    def test_review_flag_propagates_to_document_view(self):
        n8 = self.wb.document_view("n-8", 1)
        n9 = self.wb.document_view("n-9", 1)
        self.assertTrue(n8.review_notice)
        self.assertFalse(n9.review_notice)


class TimeTravelTest(unittest.TestCase):
    def test_before_ack_arrives_docket_sees_pending_only(self):
        # seq17：a1 仍是 pending，签收 seq19 与决定 seq22 均未到达。
        wb = Workbench.load(through=17)
        view = wb.document_view("n-8", 1)
        self.assertEqual(view.states, frozenset({STATE_PENDING}))
        self.assertIsNone(view.clock)
        self.assertEqual(wb.decision_views(), [])

    def test_ack_was_legal_under_then_current_revision(self):
        # seq19 时最新登记为 rev2（区间 [0,14)），t=12 签收当时合法。
        wb = Workbench.load(through=19)
        view = wb.document_view("n-8", 1)
        self.assertIsNotNone(view.clock)
        self.assertEqual(view.clock.occurred, 12)
        judgment = view.clock.judgment
        self.assertTrue(judgment.valid)
        self.assertEqual(judgment.reason, "mandate_granted")

    def test_after_correction_same_ack_becomes_invalid(self):
        # seq25 到达 rev3 后，同一历史签收按最新完整修订判定为无权。
        wb = Workbench.load(through=26)
        view = wb.document_view("n-8", 1)
        self.assertIsNone(view.clock)
        self.assertTrue(view.invalid_acks)

    def test_docket_acceptance_before_revision_correction_is_later_flagged(self):
        # seq22 决定采纳时签收仍合法；seq25 更正后回头复核应挂起。
        wb22 = Workbench.load(through=23)
        decisions = {
            view.decision.decision: view for view in wb22.decision_views()
        }
        self.assertEqual(decisions["clock-8"].status, DECISION_VALID)
        wb_now = Workbench.load()
        current = {
            view.decision.decision: view for view in wb_now.decision_views()
        }
        self.assertEqual(current["clock-8"].status, DECISION_REVIEW)


if __name__ == "__main__":
    unittest.main()
