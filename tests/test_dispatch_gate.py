"""Tests for the dispatch approval gate (stdlib unittest)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_dispatch_readiness import (readiness_report,  # noqa: E402
                                                 validate_approval,
                                                 validate_package)


class DispatchGateTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = json.loads(
            (ROOT / "docs/evidence_resolution_requests.json").read_text(encoding="utf-8"))
        cls.register_ids = {i["id"] for i in json.loads(
            (ROOT / "docs/evidence_register.json").read_text(encoding="utf-8"))["items"]}

    def test_package_structurally_valid(self):
        valid, blocked = validate_package(self.package, self.register_ids)
        self.assertEqual(len(valid), 10)
        self.assertEqual(blocked, [])

    def test_incomplete_requests_blocked(self):
        import copy
        broken = copy.deepcopy(self.package)
        broken["requests"][0] = dict(broken["requests"][0], required_evidence="")
        broken["requests"][1]["linked_register_ids"] = ["EV-NOPE"]
        valid, blocked = validate_package(broken, self.register_ids)
        self.assertEqual(len(valid), 8)
        self.assertEqual(len(blocked), 2)

    def test_no_dispatch_without_approval(self):
        authorized, reasons = validate_approval(None)
        self.assertFalse(authorized)
        authorized, _ = validate_approval({"decision": "not_approved"})
        self.assertFalse(authorized)

    def test_self_labels_cannot_authorize(self):
        label = {"decision": "approved", "approver_role": "analyst",
                 "approver_id": None, "date": "2026-01-01", "scope": ["REQ-01"]}
        authorized, reasons = validate_approval(label)
        self.assertFalse(authorized)
        self.assertTrue(any("label" in r or "approver" in r for r in reasons))

    def test_explicit_authorization_passes(self):
        approval = {"decision": "approved", "approver_role": "dispatch_authority",
                    "approver_id": "authority-1", "date": "2026-10-16",
                    "scope": ["REQ-01"]}
        authorized, reasons = validate_approval(approval)
        self.assertTrue(authorized)
        self.assertEqual(reasons, [])

    def test_readiness_report_default_deny(self):
        report = readiness_report()
        self.assertEqual(len(report["structurally_valid"]), 10)
        self.assertEqual(report["blocked_incomplete"], [])
        self.assertEqual(report["authorized_for_dispatch"], [])
        self.assertFalse(report["approval"]["authorized"])


if __name__ == "__main__":
    unittest.main()
