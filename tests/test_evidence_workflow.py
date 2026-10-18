"""Tests for evidence-gap workflow (stdlib unittest)."""
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_evidence_register import (validate_register,  # noqa: E402
                                                validate_transition, RegisterError)

CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")


class EvidenceWorkflowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.register = json.loads(
            (ROOT / "docs/evidence_register.json").read_text(encoding="utf-8"))
        cls.matrix = json.loads(
            (ROOT / "docs/evidence_gap_matrix.json").read_text(encoding="utf-8"))

    def test_transition_policy(self):
        validate_transition("unverified", "evidence_pending")
        validate_transition("evidence_pending", "under_review")
        validate_transition("under_review", "verified")
        validate_transition("under_review", "evidence_pending")
        for bad in (("unverified", "verified"), ("unverified", "under_review"),
                    ("evidence_pending", "verified"), ("verified", "under_review"),
                    ("verified", "unverified"), ("bogus", "verified")):
            with self.assertRaises(RegisterError, msg=str(bad)):
                validate_transition(*bad)

    def test_pending_requires_location(self):
        register = json.loads(json.dumps(self.register))
        for item in register["items"]:
            if item["status"] == "evidence_pending":
                item["evidence_location"] = None
                break
        with self.assertRaises(RegisterError):
            validate_register(register)

    def test_applicability_present(self):
        for item in self.register["items"]:
            self.assertIn(item["applicability"], ("target_asset", "class_level"))

    def test_matrix_covers_register(self):
        register_ids = {i["id"] for i in self.register["items"]}
        matrix_ids = {i["evidence_id"] for i in self.matrix["items"]}
        self.assertEqual(register_ids, matrix_ids)
        for item in self.matrix["items"]:
            for field in ("question", "status", "translation_status",
                          "engineering_review_status", "acceptance_criteria",
                          "owner_role", "absence", "blocks"):
                self.assertTrue(item.get(field), field)
            self.assertEqual(item["engineering_review_status"], "not_started")

    def test_translation_never_implies_verified(self):
        statuses = {i["status"] for i in self.register["items"]}
        self.assertNotIn("verified", statuses)

    def test_source_archives_immutable(self):
        manifest = json.loads(
            (ROOT / "docs/english_source_manifest.json").read_text(encoding="utf-8"))
        by_name = {}
        for source in manifest["sources"]:
            if source.get("sha256"):
                by_name[source["original_filename"]] = source["sha256"]
        self.assertGreater(len(by_name), 0)
        for path in (ROOT / "data/source_archive/originals").iterdir():
            if path.name in by_name:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(digest, by_name[path.name], path.name)

    def test_evidence_endpoint_english_only(self):
        import app.main as main_module
        main_module.BQ_TABLE = ""
        client = main_module.app.test_client()
        response = client.get("/api/evidence-gaps")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["verified"], 0)
        self.assertEqual(body["total"], 30)
        self.assertIsNone(CJK.search(json.dumps(body, ensure_ascii=False)))


if __name__ == "__main__":
    unittest.main()
