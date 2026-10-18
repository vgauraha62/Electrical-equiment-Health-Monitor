"""Tests for the acquisition package (stdlib unittest)."""
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")


class AcquisitionPackageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = json.loads(
            (ROOT / "docs/evidence_acquisition_package.json").read_text(encoding="utf-8"))
        cls.register = json.loads(
            (ROOT / "docs/evidence_register.json").read_text(encoding="utf-8"))

    def test_schema_and_coverage(self):
        required = {"evidence_id", "priority", "priority_group", "status",
                    "question", "why_it_matters_technical", "why_it_matters_plain",
                    "existing_sources", "required_external", "applicability",
                    "acceptance_criteria", "dependencies", "owner_role", "blocks"}
        register_ids = {i["id"] for i in self.register["items"]}
        package_ids = set()
        for request in self.package["requests"]:
            self.assertTrue(required.issubset(request.keys()), request.get("evidence_id"))
            self.assertIn(request["priority"], (1, 2, 3, 4, 5, 6))
            self.assertTrue(request["blocks"])
            package_ids.add(request["evidence_id"])
        self.assertEqual(package_ids, register_ids)

    def test_prioritization(self):
        order = [r["priority"] for r in self.package["requests"]]
        self.assertEqual(order, sorted(order))
        groups = {r["evidence_id"]: r["priority_group"] for r in self.package["requests"]}
        self.assertTrue(all(groups[f"EV-CODE-{c}"] == "measurement_semantics"
                            for c in ("A_phsA", "TotW")))

    def test_package_changes_no_status(self):
        before = hashlib.sha256(
            (ROOT / "docs/evidence_register.json").read_bytes()).hexdigest()
        import subprocess
        run = subprocess.run(
            [sys.executable, "scripts/build_acquisition_package.py"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stderr)
        after = hashlib.sha256(
            (ROOT / "docs/evidence_register.json").read_bytes()).hexdigest()
        self.assertEqual(before, after)
        again = json.loads(
            (ROOT / "docs/evidence_acquisition_package.json").read_text(encoding="utf-8"))
        self.assertEqual(self.package, again)

    def test_english_only_briefs(self):
        for filename in ("docs/m1_13_review_brief.md",
                         "docs/m1_13_client_request.md",
                         "docs/m1_13_traceability.md"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIsNone(CJK.search(text), filename)


if __name__ == "__main__":
    unittest.main()
