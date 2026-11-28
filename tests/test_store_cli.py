"""本地提交存储与命令行冒烟。"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from workbench import cli
from workbench.books import load_rows
from workbench.engine import Workbench
from workbench.store import SubmissionError, append_record, next_seq

ROOT = Path(__file__).resolve().parent.parent


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.local = self.tmp / "local"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_seq_continues_global_arrival_order(self):
        self.assertEqual(next_seq(self.local), 31)
        record = append_record(
            "delivery",
            {
                "attempt": "a5",
                "notice": "n-8",
                "rev": 2,
                "principal": "portal:94",
                "result": "pending",
                "occurred": None,
            },
            local_dir=self.local,
        )
        self.assertEqual(record["seq"], 31)
        self.assertEqual(next_seq(self.local), 32)

    def test_new_ack_then_clock_starts_without_second_basis_on_resend(self):
        append_record(
            "documents",
            {
                "notice": "n-11",
                "rev": 1,
                "case": "case-81",
                "holder": "A:27",
                "kind": "ordinary",
                "personal_ack": False,
            },
            local_dir=self.local,
        )
        append_record(
            "delivery",
            {
                "attempt": "a6",
                "notice": "n-11",
                "rev": 1,
                "principal": "portal:91",
                "result": "offered",
                "occurred": 31,
            },
            local_dir=self.local,
        )
        append_record(
            "delivery",
            {
                "attempt": "a6",
                "notice": "n-11",
                "rev": 1,
                "principal": "portal:91",
                "result": "acknowledged",
                "occurred": 33,
            },
            local_dir=self.local,
        )
        # 同修订再次发件不会制造第二个有效起算。
        append_record(
            "delivery",
            {
                "attempt": "a7",
                "notice": "n-11",
                "rev": 1,
                "principal": "portal:91",
                "result": "offered",
                "occurred": 34,
            },
            local_dir=self.local,
        )
        wb = Workbench.load(local_dir=self.local)
        view = wb.document_view("n-11", 1)
        self.assertIsNotNone(view.clock)
        self.assertEqual(view.clock.attempt.attempt, "a6")
        self.assertEqual(view.clock.occurred, 33)
        self.assertEqual(len(view.attempts), 2)

    def test_revision_resend_keeps_old_read_record_apart(self):
        # n-8 r2 后续签收只作用于 r2，不把 r1 的旧签收迁移过来。
        append_record(
            "delivery",
            {
                "attempt": "a4",
                "notice": "n-8",
                "rev": 2,
                "principal": "portal:94",
                "result": "acknowledged",
                "occurred": 40,
            },
            local_dir=self.local,
        )
        wb = Workbench.load(local_dir=self.local)
        r1 = wb.document_view("n-8", 1)
        r2 = wb.document_view("n-8", 2)
        self.assertIsNone(r1.clock)  # 旧件仍无有效起算
        self.assertEqual(r2.clock.occurred, 40)
        self.assertEqual(r2.clock.attempt.attempt, "a4")

    def test_validation_rejects_bad_payload(self):
        with self.assertRaises(SubmissionError):
            append_record(
                "delivery",
                {
                    "attempt": "a9",
                    "notice": "n-8",
                    "rev": 2,
                    "principal": "portal:94",
                    "result": "acknowledged",
                    "occurred": None,
                },
                local_dir=self.local,
            )
        with self.assertRaises(SubmissionError):
            append_record("documents", {"notice": "n-12"}, local_dir=self.local)

    def test_mandate_correction_arriving_later_changes_future_rights(self):
        append_record(
            "mandates",
            {
                "ref": "B:m6",
                "rev": 2,
                "person": "B:41",
                "holder": "A:27",
                "case": "case-81",
                "actions": ["open"],
                "from": 27,
                "until": 35,
            },
            local_dir=self.local,
        )
        wb = Workbench.load(local_dir=self.local)
        from workbench.model import PersonId
        from workbench.rules import can_ack

        document = wb._documents[("n-8", 2)]
        # t=40 时修订区间已结束，新签收将被判无权。
        verdict = can_ack(
            document, "portal:94", 40, wb.identities, wb.mandates
        )
        self.assertFalse(verdict.allowed)


class CliTest(unittest.TestCase):
    def _run(self, *argv: str) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.main(list(argv))
        return buffer.getvalue()

    def test_documents_and_trace_text(self):
        output = self._run("documents")
        self.assertIn("有争议决定待复核", output)
        trace = self._run("trace", "n-8", "--all-revs")
        self.assertIn("修订 r1", trace)
        self.assertIn("修订 r2", trace)
        self.assertIn("无效", trace)

    def test_decisions_json_is_machine_readable(self):
        output = self._run("decisions", "--only-review", "--json")
        payload = json.loads(output)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["decision"]["decision"], "clock-8")
        self.assertEqual(payload[0]["status"], "review")

    def test_inbox_through_replay(self):
        output = self._run("--through", "17", "inbox", "portal:93")
        self.assertIn("待确认", output)
        self.assertNotIn("已签收", output)

    def test_script_entrypoint_matches_module(self):
        output = subprocess.check_output(
            [sys.executable, "-m", "workbench.cli", "documents"],
            text=True,
            cwd=ROOT,
        )
        self.assertIn("n-9", output)

    def test_invalid_submission_prints_friendly_error(self):
        with self.assertRaises(SystemExit) as context:
            self._run(
                "submit",
                "delivery",
                json.dumps(
                    {
                        "attempt": "x",
                        "notice": "n-8",
                        "rev": 2,
                        "principal": "portal:94",
                        "result": "acknowledged",
                        "occurred": None,
                    }
                ),
            )
        self.assertEqual(context.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
