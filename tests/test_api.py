"""API regression tests via Flask test client (stdlib unittest, no extra deps).

Covers: index, health, assets, evidence, decision, knowledge (empty),
defects sample labeling, reviews (unconfigured/configured-validation via
module-attr override and stubbed BigQuery module), explain disabled,
invalid assets and payloads. Asserts fields/types/invariants, never dumps
full evidence lists.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.main as main_module  # noqa: E402


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Force local-only state regardless of environment.
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        response = cls.client.get("/api/assets")
        assert response.status_code == 200, response.get_data(as_text=True)
        assets = response.get_json()
        assert len(assets) >= 1
        cls.asset_id = assets[0]["asset_id"]

    def test_index(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.content_type)

    def test_health(self):
        body = self.client.get("/api/health").get_json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data_source"], "bundled_sample")
        self.assertEqual(body["knowledge_source"], "empty")
        self.assertEqual(body["target_equipment_type_code"], "0408001")
        self.assertEqual(body["decision_component"], "data_readiness_rules_v1")
        self.assertFalse(body["laya_enabled"])
        self.assertFalse(body["gemini_enabled"])
        self.assertIsInstance(body["target_equipment_type_code"], str)

    def test_assets(self):
        assets = self.client.get("/api/assets").get_json()
        self.assertIsInstance(assets, list)
        self.assertGreaterEqual(len(assets), 1)
        first = assets[0]
        self.assertIsInstance(first["asset_id"], str)
        self.assertEqual(first["equipment_type_code"], "0408001")
        self.assertIsInstance(first["observation_count"], int)
        self.assertEqual(first["data_origin"], "observed")

    def test_evidence_invariants(self):
        body = self.client.get(f"/api/assets/{self.asset_id}/evidence").get_json()
        for key in ("asset_id", "equipment_type_code", "snapshot_measurements",
                    "history_summary", "quality_warnings", "evidence_ids",
                    "decision", "assessment_status"):
            self.assertIn(key, body)
        self.assertEqual(body["data_origin"], "observed")
        self.assertFalse(body["oil_immersed_confirmed"])
        self.assertIsInstance(body["evidence_ids"], list)
        self.assertGreater(len(body["evidence_ids"]), 0)
        # Legacy alias route returns the same asset.
        alias = self.client.get(f"/api/assets/{self.asset_id}").get_json()
        self.assertEqual(alias["asset_id"], body["asset_id"])

    def test_decision_contract(self):
        body = self.client.get(f"/api/assets/{self.asset_id}/decision").get_json()
        decision = body["decision"]
        self.assertIsNone(decision["failure_probability"])
        self.assertEqual(decision["fault_state"], "not_assessed")
        self.assertEqual(decision["review_route"], "human_review")
        self.assertEqual(decision["component"], "data_readiness_rules_v1")
        self.assertIn("oil_immersed_construction_unconfirmed", decision["reason_codes"])

    def test_unknown_asset_404(self):
        for path in ("evidence", "decision", "knowledge"):
            response = self.client.get(f"/api/assets/no-such-asset/{path}")
            self.assertEqual(response.status_code, 404, path)
        response = self.client.post("/api/assets/no-such-asset/explain")
        # Explain is disabled locally, so the disabled-route guard fires first.
        self.assertIn(response.status_code, (404, 503))

    def test_knowledge_empty_is_success(self):
        body = self.client.get(f"/api/assets/{self.asset_id}/knowledge").get_json()
        self.assertEqual(body["knowledge_status"], "empty")
        self.assertEqual(body["retrieval_mode"], "not_configured")
        self.assertEqual(body["items"], [])
        search = self.client.get("/api/knowledge/search?q=transformer").get_json()
        self.assertEqual(search["knowledge_status"], "empty")
        self.assertEqual(search["items"], [])
        missing = self.client.get("/api/knowledge/search")
        self.assertEqual(missing.status_code, 400)

    def test_defects_labeled_sample_only(self):
        body = self.client.get("/api/defects").get_json()
        self.assertEqual(body["sample_status"], "raw_reference_sample_only")
        self.assertFalse(body["approved_guidance"])
        self.assertEqual(body["translation_status"], "draft_unreviewed")
        self.assertIn("equipment_scope_note", body)
        self.assertIsInstance(body["count"], int)
        self.assertGreater(body["count"], 0)
        self.assertEqual(len(body["items"]), body["count"])
        # Search narrows but never claims approval.
        filtered = self.client.get("/api/defects?q=switchgear").get_json()
        self.assertLessEqual(filtered["count"], body["count"])
        self.assertFalse(filtered["approved_guidance"])

    def test_explain_disabled(self):
        response = self.client.post(f"/api/assets/{self.asset_id}/explain")
        self.assertEqual(response.status_code, 503)

    def test_reviews_unconfigured_503(self):
        main_module.REVIEW_TABLE = ""
        payload = {"asset_id": self.asset_id, "outcome": "review",
                   "rationale": "Units unconfirmed; needs human review."}
        response = self.client.post("/api/reviews", json=payload)
        self.assertEqual(response.status_code, 503)

    def test_reviews_validation_400_and_unknown_asset_404(self):
        main_module.REVIEW_TABLE = "proj.ds.human_review"
        try:
            bad_payloads = [
                {},
                {"asset_id": self.asset_id, "outcome": "bogus", "rationale": "x"},
                {"asset_id": self.asset_id, "outcome": "review", "rationale": ""},
                {"asset_id": self.asset_id, "outcome": "review", "rationale": "x",
                 "evidence_ids": "not-a-list"},
                {"asset_id": self.asset_id, "outcome": "review", "rationale": "x",
                 "evidence_ids": ["ev_not_belonging_to_asset"]},
                {"asset_id": self.asset_id, "outcome": "review", "rationale": "x",
                 "knowledge_chunk_ids": "not-a-list"},
            ]
            for payload in bad_payloads:
                response = self.client.post("/api/reviews", json=payload)
                self.assertEqual(response.status_code, 400, json.dumps(payload))
            unknown = {"asset_id": "no-such-asset", "outcome": "review",
                       "rationale": "Reviewer note."}
            response = self.client.post("/api/reviews", json=unknown)
            self.assertEqual(response.status_code, 404)
        finally:
            main_module.REVIEW_TABLE = ""

    def test_reviews_configured_path_never_fakes_success(self):
        """Stub the BigQuery SDK (not installed) to cover persist/fail paths."""
        import types
        main_module.REVIEW_TABLE = "proj.ds.human_review"
        saved = {name: sys.modules.get(name) for name in
                 ("google", "google.cloud", "google.cloud.bigquery")}
        try:
            bigquery = types.ModuleType("google.cloud.bigquery")

            class FakeClient:
                behavior = "ok"

                def __init__(self, *args, **kwargs):
                    pass

                def insert_rows_json(self, table, rows):
                    if FakeClient.behavior == "ok":
                        self.captured = (table, rows)
                        return []
                    return [{"errors": "boom"}]

            bigquery.Client = FakeClient
            google = types.ModuleType("google")
            cloud = types.ModuleType("google.cloud")
            cloud.bigquery = bigquery
            sys.modules["google"] = google
            sys.modules["google.cloud"] = cloud
            sys.modules["google.cloud.bigquery"] = bigquery

            evidence = self.client.get(f"/api/assets/{self.asset_id}/evidence").get_json()
            payload = {"asset_id": self.asset_id, "outcome": "review",
                       "rationale": "Reviewer confirmed evidence gaps.",
                       "evidence_ids": evidence["evidence_ids"][:1],
                       "knowledge_chunk_ids": []}
            FakeClient.behavior = "ok"
            ok_response = self.client.post("/api/reviews", json=payload)
            self.assertEqual(ok_response.status_code, 201)
            self.assertIn("review_id", ok_response.get_json())

            FakeClient.behavior = "fail"
            fail_response = self.client.post("/api/reviews", json=payload)
            self.assertEqual(fail_response.status_code, 502)
            self.assertNotIn("review_id", fail_response.get_json())
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
            main_module.REVIEW_TABLE = ""


if __name__ == "__main__":
    unittest.main()
