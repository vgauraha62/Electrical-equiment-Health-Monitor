"""Tests for the deterministic monitoring workflow (stdlib unittest)."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.main as main_module  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")


class WorkflowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        cls.asset_id = main_module._assets()[0]["asset_id"]

    def post(self, payload):
        return self.client.post("/api/monitoring-reviews", json=payload)

    def test_valid_request(self):
        response = self.post({"asset_id": self.asset_id})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        for key in ("request_id", "created_at", "request_asset_id", "analysis",
                    "provenance", "readiness", "population", "descriptive",
                    "review_candidates", "unknowns", "knowledge", "human_review",
                    "unsupported", "ai"):
            self.assertIn(key, body)
        self.assertEqual(body["analysis"]["version"], "monitoring_workflow_v1")
        self.assertTrue(body["analysis"]["deterministic"])
        self.assertEqual(body["readiness"]["fault_state"], "not_assessed")
        self.assertIsNone(body["readiness"]["failure_probability"])
        self.assertEqual(body["readiness"]["review_route"], "human_review")
        self.assertEqual(body["population"]["observation_count"], 1372)
        self.assertEqual(len(body["descriptive"]), 14)
        for candidate in body["review_candidates"]:
            self.assertEqual(candidate["method"], "tukey_fences")
            self.assertEqual(candidate["status"], "review_candidate_only")
        self.assertEqual(body["unknowns"]["evidence_register"]["verified"], 0)
        self.assertEqual(body["knowledge"]["status"], "empty")
        self.assertFalse(body["human_review"]["persistence_configured"])
        self.assertFalse(body["ai"]["available"])
        conclusions = {u["conclusion"] for u in body["unsupported"]}
        self.assertIn("failure_probability", conclusions)
        self.assertTrue(all(u["state"] == "blocked" for u in body["unsupported"]))

    def test_invalid_requests(self):
        self.assertEqual(self.post({}).status_code, 400)
        self.assertEqual(self.post({"asset_id": ""}).status_code, 400)
        self.assertEqual(self.post("not-a-dict").status_code, 400)
        self.assertEqual(self.post({"asset_id": self.asset_id,
                                    "analysis_type": "fault_diagnosis"}).status_code, 422)
        self.assertEqual(self.post({"asset_id": self.asset_id,
                                    "dataset_version": "9.9"}).status_code, 400)
        self.assertEqual(self.post({"asset_id": self.asset_id,
                                    "window": "soon"}).status_code, 400)
        windowed = self.post({"asset_id": self.asset_id,
                              "window": {"start": "2026-08-24 00:00:00",
                                         "end": "2026-08-25 00:00:00"}})
        self.assertEqual(windowed.status_code, 422)
        self.assertIn("timezone", windowed.get_json()["error"].lower())

    def test_unknown_asset(self):
        response = self.post({"asset_id": "no-such-asset"})
        self.assertEqual(response.status_code, 404)

    def test_deterministic_apart_from_ids(self):
        first = self.post({"asset_id": self.asset_id}).get_json()
        second = self.post({"asset_id": self.asset_id}).get_json()
        for body in (first, second):
            body.pop("request_id")
            body.pop("created_at")
        self.assertEqual(first, second)

    def test_english_only_and_no_personal_data(self):
        import json as jsonlib
        text = jsonlib.dumps(self.post({"asset_id": self.asset_id}).get_json(),
                             ensure_ascii=False)
        self.assertIsNone(CJK.search(text))
        for banned in ("owner_name", "discoverer", "maintainer_name", "creater_name"):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()
