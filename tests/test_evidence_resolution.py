"""Tests for evidence-resolution requests (stdlib unittest)."""
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_resolution_requests import (ACCEPTANCE,  # noqa: E402
                                               validate_resolution_transition)

CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")


class ResolutionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = json.loads(
            (ROOT / "docs/evidence_resolution_requests.json").read_text(encoding="utf-8"))
        cls.register_ids = {i["id"] for i in json.loads(
            (ROOT / "docs/evidence_register.json").read_text(encoding="utf-8"))["items"]}

    def test_ten_stable_unique_requests(self):
        ids = [r["request_id"] for r in self.package["requests"]]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)
        self.assertEqual(ids, sorted(ids))

    def test_required_fields_and_linkage(self):
        required = {"request_id", "priority", "owner_role", "title",
                    "affected_fields_or_identifiers", "unresolved_decision",
                    "required_evidence", "linked_register_ids",
                    "acceptance_criterion", "acceptance_text",
                    "resolution_status"}
        for request in self.package["requests"]:
            self.assertTrue(required.issubset(request.keys()), request["request_id"])
            self.assertIn(request["acceptance_criterion"], ACCEPTANCE)
            for rid in request["linked_register_ids"]:
                self.assertIn(rid, self.register_ids, request["request_id"])
            self.assertEqual(request["resolution_status"], "drafted")
            self.assertIsNone(request["reviewer"])
            self.assertNotIn("verified", json.dumps(request).lower().replace(
                "never", "").replace("verification", ""))

    def test_acceptance_covers_seven_categories(self):
        self.assertEqual(len(ACCEPTANCE), 7)
        for name, text in ACCEPTANCE.items():
            self.assertTrue(len(text) > 40, name)

    def test_invalid_transitions_rejected(self):
        validate_resolution_transition("drafted", "awaiting_owner")
        validate_resolution_transition("answered", "under_review")
        validate_resolution_transition("under_review", "closed")
        for bad in (("drafted", "closed"), ("drafted", "under_review"),
                    ("awaiting_owner", "closed"), ("closed", "drafted"),
                    ("under_review", "awaiting_owner"), ("bogus", "closed")):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_resolution_transition(*bad)

    def test_no_owner_names_invented(self):
        blob = json.dumps(self.package)
        self.assertNotIn("@", blob)
        for request in self.package["requests"]:
            # Roles only: no two-word capitalized personal names.
            self.assertNotRegex(request["owner_role"], r"[A-Z][a-z]+ [A-Z][a-z]+")
            self.assertIsNone(request["reviewer"])

    def test_english_only(self):
        self.assertIsNone(CJK.search(json.dumps(self.package, ensure_ascii=False)))

    def test_rebuild_does_not_touch_register(self):
        import subprocess
        before = hashlib.sha256(
            (ROOT / "docs/evidence_register.json").read_bytes()).hexdigest()
        run = subprocess.run([sys.executable, "scripts/build_resolution_requests.py"],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stderr)
        after = hashlib.sha256(
            (ROOT / "docs/evidence_register.json").read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
