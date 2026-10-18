"""Tests for evidence intake (stdlib unittest, isolated storage)."""
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.validate import ValidationError, validate  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")
CONTENT = b"statement of units: phase current in A (example fixture, not evidence)"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


class IntakeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="intake_")
        os.environ["EVIDENCE_INTAKE_DIR"] = cls.tmp
        import app.main as main_module
        cls.main = main_module
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        cls.client = main_module.app.test_client()

    def test_schema_accepts_valid_rejects_bad(self):
        schema = json.loads((ROOT / "schemas/v1/evidence_submission.schema.json")
                            .read_text(encoding="utf-8"))
        good = {"submission_id": "s1", "evidence_request_id": "EV-CODE-A_phsA",
                "received_at": "2026-01-01T00:00:00+00:00",
                "received_timezone": "UTC", "intake_status": "received",
                "reviewer_status": "not_reviewed", "schema_version": "1.0.0"}
        validate(good, schema)
        bad = dict(good, intake_status="auto_approved")
        with self.assertRaises(ValidationError):
            validate(bad, schema)

    def test_full_intake_lifecycle(self):
        create = self.client.post("/api/evidence-submissions", json={
            "evidence_request_id": "EV-CODE-A_phsA",
            "idempotency_key": "intake-1",
            "files": [{"original_filename": "units.txt", "sha256": DIGEST}],
            "stated_uncertainty": "Example statement, not verified."})
        self.assertEqual(create.status_code, 201)
        body = create.get_json()
        sid = body["submission_id"]
        self.assertEqual(body["intake_status"], "received")
        self.assertEqual(body["reviewer_status"], "not_reviewed")
        self.assertEqual(body["received_timezone"], "UTC")

        replay = self.client.post("/api/evidence-submissions", json={
            "evidence_request_id": "EV-CODE-A_phsA", "idempotency_key": "intake-1"})
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.get_json()["replayed"])

        attach = self.client.post(f"/api/evidence-submissions/{sid}/files", json={
            "filename": "units.txt",
            "content_base64": base64.b64encode(CONTENT).decode()})
        self.assertEqual(attach.status_code, 200)

        again = self.client.post(f"/api/evidence-submissions/{sid}/files", json={
            "filename": "units.txt",
            "content_base64": base64.b64encode(CONTENT).decode()})
        self.assertEqual(again.status_code, 409)

        tampered = self.client.post("/api/evidence-submissions", json={
            "evidence_request_id": "EV-CODE-A_phsA",
            "files": [{"original_filename": "other.txt", "sha256": DIGEST}]})
        tampered_id = tampered.get_json()["submission_id"]
        bad_bytes = self.client.post(
            f"/api/evidence-submissions/{tampered_id}/files", json={
                "filename": "other.txt",
                "content_base64": base64.b64encode(b"different bytes").decode()})
        self.assertEqual(bad_bytes.status_code, 422)

        for to_status in ("intake_validated", "ready_for_review", "accepted"):
            move = self.client.post(
                f"/api/evidence-submissions/{sid}/transitions",
                json={"to_status": to_status})
            self.assertEqual(move.status_code, 200, move.get_data(as_text=True))
        final = move.get_json()
        self.assertEqual(final["intake_status"], "accepted")
        self.assertEqual(final["reviewer_status"], "not_reviewed")
        seqs = [e["seq"] for e in final["history"]]
        self.assertEqual(seqs, sorted(seqs))
        dead = self.client.post(f"/api/evidence-submissions/{sid}/transitions",
                                json={"to_status": "received"})
        self.assertEqual(dead.status_code, 422)

        listed = self.client.get(
            "/api/evidence-submissions?request_id=EV-CODE-A_phsA").get_json()
        self.assertTrue(any(s["submission_id"] == sid for s in listed))
        fetched = self.client.get(f"/api/evidence-submissions/{sid}").get_json()
        self.assertEqual(fetched["submission_id"], sid)
        self.assertIsNone(CJK.search(json.dumps(fetched, ensure_ascii=False)))

    def test_invalid_requests(self):
        self.assertEqual(
            self.client.post("/api/evidence-submissions", json={}).status_code, 400)
        bad_file = self.client.post("/api/evidence-submissions", json={
            "evidence_request_id": "EV-X", "files": [{"original_filename": "x"}]})
        self.assertEqual(bad_file.status_code, 400)
        missing = self.client.get("/api/evidence-submissions/nope")
        self.assertEqual(missing.status_code, 404)

    def test_acceptance_never_verifies_register(self):
        digest_before = hashlib.sha256(
            (ROOT / "docs/evidence_register.json").read_bytes()).hexdigest()
        import subprocess
        run = subprocess.run(
            [sys.executable, "scripts/evidence_reconciliation.py"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads((ROOT / "data/output/evidence_reconciliation.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["requests_sent"], 0)
        self.assertEqual(report["summary"]["responses_received"], 0)
        self.assertEqual(len(report["rows"]), 30)
        digest_after = hashlib.sha256(
            (ROOT / "docs/evidence_register.json").read_bytes()).hexdigest()
        self.assertEqual(digest_before, digest_after)


if __name__ == "__main__":
    unittest.main()
