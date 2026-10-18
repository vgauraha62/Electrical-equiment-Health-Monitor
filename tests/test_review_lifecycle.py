"""Tests for the persistent review lifecycle (stdlib unittest, isolated storage)."""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")


class LifecycleTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="review_cases_")
        os.environ["REVIEW_CASES_DIR"] = cls.tmp
        import app.main as main_module
        cls.main = main_module
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        cls.asset_id = main_module._assets()[0]["asset_id"]

    def test_full_lifecycle(self):
        create = self.client.post("/api/review-cases", json={
            "asset_id": self.asset_id, "idempotency_key": "demo-1",
            "reviewer": "analyst", "note": "Initial triage."})
        self.assertEqual(create.status_code, 201)
        case = create.get_json()
        case_id = case["case_id"]
        self.assertEqual(case["status"], "open")
        self.assertEqual(case["version"], 1)
        self.assertFalse(case["reviewer"]["verified"])
        self.assertIn("digest_sha256", case["monitoring"])
        self.assertEqual(len(case["history"]), 1)

        # Idempotent replay returns the same case without duplication.
        replay = self.client.post("/api/review-cases", json={
            "asset_id": self.asset_id, "idempotency_key": "demo-1"})
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.get_json()["replayed"])
        self.assertEqual(replay.get_json()["case_id"], case_id)

        # Move into review, record a note, request evidence, resume, complete.
        for to_status, version in (("in_review", 1),):
            move = self.client.post(f"/api/review-cases/{case_id}/transitions",
                                    json={"from_version": version,
                                          "to_status": to_status})
            self.assertEqual(move.status_code, 200, move.get_data(as_text=True))
        note = self.client.post(f"/api/review-cases/{case_id}/notes",
                                json={"from_version": 2, "note": "Checked gaps.",
                                      "reviewer": "analyst"})
        self.assertEqual(note.status_code, 200)
        need = self.client.post(f"/api/review-cases/{case_id}/transitions",
                                json={"from_version": 3,
                                      "to_status": "evidence_required",
                                      "note": "Need rated capacity."})
        self.assertEqual(need.status_code, 200)
        back = self.client.post(f"/api/review-cases/{case_id}/transitions",
                                json={"from_version": 4, "to_status": "in_review"})
        self.assertEqual(back.status_code, 200)
        done = self.client.post(f"/api/review-cases/{case_id}/transitions",
                                json={"from_version": 5, "to_status": "completed",
                                      "note": "Evidence reviewed; no condition claimed."})
        self.assertEqual(done.status_code, 200)
        final = done.get_json()
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["version"], 6)
        seqs = [e["seq"] for e in final["history"]]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))

        # Terminal state rejects further transitions; stale versions conflict.
        dead = self.client.post(f"/api/review-cases/{case_id}/transitions",
                                json={"from_version": 6, "to_status": "in_review"})
        self.assertEqual(dead.status_code, 422)
        stale = self.client.post(f"/api/review-cases/{case_id}/notes",
                                 json={"from_version": 1, "note": "Late note."})
        self.assertEqual(stale.status_code, 409)
        bad = self.client.post(f"/api/review-cases/{case_id}/transitions",
                               json={"from_version": 6, "to_status": "open"})
        self.assertIn(bad.status_code, (404, 422))

        # Retrieval, filtering, and evidence pinning.
        fetched = self.client.get(f"/api/review-cases/{case_id}").get_json()
        self.assertEqual(fetched["case_id"], case_id)
        listed = self.client.get(
            f"/api/review-cases?asset={self.asset_id}&status=completed").get_json()
        self.assertTrue(any(c["case_id"] == case_id for c in listed))
        from app.review_store import monitoring_digest
        self.assertEqual(fetched["monitoring"]["digest_sha256"],
                         monitoring_digest(fetched["monitoring"]["snapshot"]))
        self.assertEqual(fetched["monitoring"]["snapshot"]["readiness"]["fault_state"],
                         "not_assessed")

        # Simulated restart: a fresh client on the same directory sees the case.
        fresh = self.main.app.test_client()
        again = fresh.get(f"/api/review-cases/{case_id}").get_json()
        self.assertEqual(again["status"], "completed")
        self.assertEqual(again["version"], 6)

        # English-only response.
        self.assertIsNone(CJK.search(json.dumps(fetched, ensure_ascii=False)))

    def test_invalid_requests(self):
        self.assertEqual(self.client.post("/api/review-cases", json={}).status_code, 400)
        unknown = self.client.post("/api/review-cases", json={"asset_id": "nope"})
        self.assertEqual(unknown.status_code, 404)
        missing = self.client.get("/api/review-cases/does-not-exist")
        self.assertEqual(missing.status_code, 404)
        create = self.client.post("/api/review-cases",
                                  json={"asset_id": self.asset_id}).get_json()
        case_id = create["case_id"]
        empty_note = self.client.post(f"/api/review-cases/{case_id}/notes",
                                      json={"from_version": 1, "note": "  "})
        self.assertEqual(empty_note.status_code, 400)
        skip = self.client.post(f"/api/review-cases/{case_id}/transitions",
                                json={"from_version": 1, "to_status": "completed"})
        self.assertEqual(skip.status_code, 422)

    def test_empty_filter_params_list_everything(self):
        created = self.client.post(
            "/api/review-cases", json={"asset_id": self.asset_id}).get_json()
        unfiltered = self.client.get("/api/review-cases").get_json()
        self.assertTrue(any(c["case_id"] == created["case_id"] for c in unfiltered))
        empty_params = self.client.get("/api/review-cases?asset=&status=").get_json()
        self.assertTrue(any(c["case_id"] == created["case_id"] for c in empty_params))
        filtered = self.client.get(
            "/api/review-cases?asset=no-such-asset").get_json()
        self.assertEqual(filtered, [])


if __name__ == "__main__":
    unittest.main()
