"""Tests for capability and explanation safeguards (stdlib unittest)."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.validate import ValidationError, validate  # noqa: E402

class CapabilityContractTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app.main as main_module
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        cls.asset_id = main_module._assets()[0]["asset_id"]

    def test_no_fault_or_health_keys_in_responses(self):
        bodies = [
            self.client.get(f"/api/assets/{self.asset_id}/evidence").get_json(),
            self.client.get(f"/api/assets/{self.asset_id}/decision").get_json(),
            self.client.post("/api/monitoring-reviews",
                             json={"asset_id": self.asset_id}).get_json(),
        ]
        for body in bodies:
            blob = json.dumps(body)
            # Positive assertions are forbidden; safeguard fields that name a
            # blocked conclusion (or an explicit null) are the contract itself.
            for key in ("fault_diagnosis", "health_score",
                        "maintenance_action", "work_order"):
                self.assertNotIn(f'"{key}"', blob)
            self.assertNotRegex(blob, r'"failure_probability":\s*[0-9]')
            self.assertNotRegex(blob, r'"fault_state":\s*"fault"')
            for blocked in ("fault_classification", "health_index"):
                for match in re.finditer(f'"{blocked}"', blob):
                    context = blob[max(0, match.start() - 200):match.end() + 200]
                    self.assertIn("blocked", context)

    def test_synthetic_origin_never_served(self):
        for body in (self.client.get("/api/assets").get_json(),
                     self.client.get(f"/api/assets/{self.asset_id}/evidence")
                     .get_json()["snapshot_measurements"]):
            items = body if isinstance(body, list) else [body]
            for item in items if isinstance(body, list) else body:
                self.assertNotEqual(item.get("data_origin"), "synthetic")

    def test_prediction_schema_bans_failure_probability(self):
        schema = json.loads((ROOT / "schemas/v1/prediction.schema.json")
                            .read_text(encoding="utf-8"))
        base = {"asset_id": "a", "output_type": "anomaly_score",
                "model_name": "m", "model_version": "1", "created_at": "t",
                "evidence_ids": [], "data_origin": "synthetic",
                "schema_version": "1.0.0"}
        validate(base, schema)
        with self.assertRaises(ValidationError):
            validate(dict(base, failure_probability=0.1), schema)
        with self.assertRaises(ValidationError):
            validate(dict(base, output_type="failure_prediction"), schema)

    def test_retrieval_predicate_requires_approved_reviewed(self):
        source = (ROOT / "app/main.py").read_text(encoding="utf-8")
        for predicate in ("status = 'approved'", "language = 'en'",
                          "translation_status = 'reviewed'"):
            self.assertIn(predicate, source)

    def test_draft_sample_excluded_from_knowledge(self):
        knowledge = self.client.get("/api/knowledge/search?q=transformer").get_json()
        self.assertEqual(knowledge["items"], [])
        defects = self.client.get("/api/defects").get_json()
        self.assertGreater(defects["count"], 0)
        self.assertFalse(defects["approved_guidance"])


if __name__ == "__main__":
    unittest.main()
