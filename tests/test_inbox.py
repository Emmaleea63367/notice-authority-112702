"""个人收件箱：本人全途径、代理人受限、未核准不泄露、待确认与无签收可辨。"""
import unittest

from workbench.engine import (
    STATE_ABSENT,
    STATE_ACKNOWLEDGED,
    STATE_OFFERED,
    Workbench,
)


class InboxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wb = Workbench.load()

    def _items(self, principal: str):
        result = self.wb.inbox(principal)
        return result, {
            (item.document.notice, item.document.rev,
             item.attempt.attempt if item.attempt else None): item
            for item in result.items
        }

    def test_holder_sees_every_route_and_revision(self):
        _, items = self._items("portal:91")  # A:27 当事人本人
        self.assertEqual(
            set(items),
            {
                ("n-8", 1, "a1"),
                ("n-8", 2, "a4"),
                ("n-9", 1, "a2"),
                ("n-10", 1, "a3"),
            },
        )
        self.assertTrue(all(item.view_now.allowed for item in items.values()))

    def test_old_agent_keeps_history_but_current_rights_are_denied(self):
        result, items = self._items("portal:93")  # A:35 许可已被撤销
        self.assertTrue(result.verified)
        self.assertEqual(set(items), {("n-8", 1, "a1")})
        item = items[("n-8", 1, "a1")]
        self.assertEqual(item.state, STATE_ACKNOWLEDGED)
        self.assertFalse(item.view_now.allowed)
        self.assertFalse(item.ack_now.allowed)

    def test_new_agent_sees_only_new_revision_route(self):
        _, items = self._items("portal:94")  # B:41 新代理人
        self.assertEqual(set(items), {("n-8", 2, "a4")})
        item = items[("n-8", 2, "a4")]
        self.assertEqual(item.state, STATE_OFFERED)
        self.assertTrue(item.ack_now.allowed)
        # 看不到旧代理人的签收细节。
        self.assertNotIn(("n-8", 1, "a1"), items)

    def test_unverified_principal_gets_empty_inbox(self):
        result = self.wb.inbox("portal:95")  # 关联 verified=false
        self.assertFalse(result.verified)
        self.assertEqual(result.items, [])

    def test_absent_feedback_visible_and_distinguishable_from_pending(self):
        _, items = self._items("portal:92")  # B:27 仅 open 权
        item = items[("n-10", 1, "a3")]
        self.assertEqual(item.state, STATE_ABSENT)

    def test_agent_without_rights_sees_nothing_for_others_documents(self):
        # portal:94 对 n-9/n-10 没有任何途径，也看不到对方细节。
        _, items = self._items("portal:94")
        self.assertNotIn(("n-9", 1, "a2"), items)
        self.assertNotIn(("n-10", 1, "a3"), items)


if __name__ == "__main__":
    unittest.main()
