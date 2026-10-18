"""Tests for reconciliation lifecycle states (stdlib unittest, synthetic data)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.evidence_reconciliation import lifecycle_state, reconcile  # noqa: E402


def package(*ids):
    return {"requests": [{"evidence_id": i, "priority": 1} for i in ids]}


def register(*ids):
    return {"items": [{"id": i, "status": "evidence_pending",
                       "reviewer_required": "domain reviewer"} for i in ids]}


class ReconciliationTestCase(unittest.TestCase):
    def test_zero_baseline(self):
        report = reconcile(package("EV-A"), register("EV-A"), [], [])
        self.assertEqual(report["summary"]["requests_sent"], 0)
        self.assertEqual(report["rows"][0]["lifecycle_state"], "drafted")
        self.assertFalse(report["rows"][0]["request_sent"])

    def test_dispatch_progression(self):
        self.assertEqual(lifecycle_state(
            [{"event": "approved_for_dispatch"}], []), "approved_for_dispatch")
        self.assertEqual(lifecycle_state(
            [{"event": "approved_for_dispatch"}, {"event": "sent"}], []), "sent")

    def test_receipt_progression(self):
        sent = [{"event": "sent"}]
        self.assertEqual(lifecycle_state(
            sent, [{"intake_status": "received", "created_at": "t"}]),
            "response_received")
        self.assertEqual(lifecycle_state(
            sent, [{"intake_status": "ready_for_review", "created_at": "t"}]),
            "ready_for_review")
        self.assertEqual(lifecycle_state(
            sent, [{"intake_status": "accepted", "created_at": "t"}]),
            "accepted_or_rejected")

    def test_no_inference_without_records(self):
        report = reconcile(package("EV-A"), register("EV-A"), [], [])
        row = report["rows"][0]
        self.assertIsNone(row["provenance_complete"])
        self.assertIsNone(row["answers_question"])
        self.assertEqual(row["missing_fields"], ["response itself"])


if __name__ == "__main__":
    unittest.main()
