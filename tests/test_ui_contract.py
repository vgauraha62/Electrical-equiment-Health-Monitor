"""UI safety-labeling and API/UI contract tests (stdlib unittest, no browser deps).

Verifies the static defects catalog carries an explicit sample-only caption,
that UI wording never presents drafts as validated terminology, that app.js
still uses the preserved API fields, and that the API contract values the UI
depends on remain exact.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.main as main_module  # noqa: E402

CAPTION = ("Raw reference sample only \u2014 translations are draft and have not "
           "been approved as transformer diagnostic guidance.")


class UiContractTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        cls.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def test_defect_caption_present_exact(self):
        self.assertIn(CAPTION, self.html)

    def test_caption_adjacent_to_catalog(self):
        section = self.html.find("defect-library")
        caption = self.html.find(CAPTION)
        search_box = self.html.find("defect-search")
        results = self.html.find("defect-results")
        self.assertNotEqual(section, -1)
        self.assertTrue(section < caption < search_box < results,
                        "caption must sit inside the catalog header, before search/results")

    def test_no_validated_terminology_claim(self):
        lowered = self.html.lower() + self.js.lower()
        self.assertNotIn("validat", lowered)
        self.assertNotIn("approved transformer", lowered.replace(CAPTION.lower(), ""))

    def test_js_uses_preserved_fields(self):
        self.assertIn("data.items", self.js)
        self.assertIn("data.count", self.js)

    def test_api_sample_status_contract(self):
        body = self.client.get("/api/defects").get_json()
        self.assertEqual(body["sample_status"], "raw_reference_sample_only")
        self.assertFalse(body["approved_guidance"])
        self.assertEqual(body["translation_status"], "draft_unreviewed")
        self.assertIn("equipment_scope_note", body)
        self.assertIn("items", body)

    def test_api_empty_kb_and_readiness_nulls(self):
        assets = self.client.get("/api/assets").get_json()
        asset_id = assets[0]["asset_id"]
        knowledge = self.client.get(f"/api/assets/{asset_id}/knowledge").get_json()
        self.assertEqual(knowledge["knowledge_status"], "empty")
        self.assertEqual(knowledge["items"], [])
        decision = self.client.get(f"/api/assets/{asset_id}/decision").get_json()["decision"]
        self.assertIsNone(decision["failure_probability"])
        self.assertEqual(decision["fault_state"], "not_assessed")
        self.assertEqual(decision["review_route"], "human_review")


if __name__ == "__main__":
    unittest.main()
