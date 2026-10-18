"""Tests for the lineage contract (stdlib unittest)."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")


class LineageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app.main as main_module
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        cls.inventory = json.loads(
            (ROOT / "data/output/lineage_inventory.json").read_text(encoding="utf-8"))

    def test_counts_match_audited_outputs(self):
        equipment = self.inventory["equipment"]
        self.assertEqual(equipment["telemetry_ids"], 13)
        self.assertEqual(equipment["telemetry_observations"], 5970)
        self.assertEqual(equipment["ledger_ids"], 3811)
        self.assertEqual(equipment["exact_id_matches"], 2)
        self.assertEqual(equipment["telemetry_unmatched"], 11)
        audit = json.loads((ROOT / "data/output/telemetry_source_audit.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(audit["asset_count"], 13)
        manifest = json.loads((ROOT / "data/output/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["record_counts"]["accepted"], 5970)

    def test_translated_copies_never_counted(self):
        translated = next(s for s in self.inventory["sources"]
                          if s["family"] == "translated telemetry equivalents")
        self.assertIn("never counted", translated["status"])
        self.assertEqual(translated["observations"], 5970)

    def test_unfinished_sources_excluded(self):
        for source in self.inventory["sources"]:
            if "unfinished" in source["status"] or source["review_status"] == "unreviewed":
                self.assertIn(source["retrieval"], ("excluded", "excluded by default",
                                                    "profiles only", "never", "served"))

    def test_explorer_matched(self):
        body = self.client.get(
            "/api/lineage/equipment/042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294").get_json()
        self.assertEqual(body["telemetry"]["observation_count"], 1372)
        self.assertTrue(body["ledger_match"]["matched"])
        self.assertIn("psr_id=", body["ledger_match"]["locator"]["key"])
        self.assertEqual(body["approved_references"], [])

    def test_explorer_carries_evidence_profile(self):
        body = self.client.get(
            "/api/lineage/equipment/042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294").get_json()
        profile = body["evidence_profile"]
        self.assertIsNotNone(profile)
        self.assertEqual(profile["profile_version"], "asset_evidence.v1")
        self.assertEqual(profile["context"]["recorded_count"], 9)
        self.assertEqual(profile["link"]["matching_method"], "exact_id_string")
        self.assertEqual(profile["link"]["verification_status"], "id_string_only")
        # A non-target equipment class carries no profile (the target class only).
        other = self.client.get("/api/lineage/equipment/28DKX-3681").get_json()
        self.assertIsNone(other["evidence_profile"])

    def test_explorer_unmatched_and_non_telemetry(self):
        body = self.client.get("/api/lineage/equipment/28DKX-3681").get_json()
        self.assertFalse(body["ledger_match"]["matched"])
        missing = self.client.get("/api/lineage/equipment/00197b62-6f37-4aa0-9962-d814c2f1d0e5")
        self.assertEqual(missing.status_code, 404)

    def test_unresolved_and_manifest_shape(self):
        self.assertGreaterEqual(len(self.inventory["unresolved"]), 6)
        manifest = self.inventory["retrieval_manifest"]
        self.assertEqual(set(manifest["source_types"]),
                         {"telemetry", "equipment_profile", "defect_record",
                          "knowledge_document"})

    def test_english_only_lineage(self):
        blob = self.client.get("/api/lineage").get_data(as_text=True)
        self.assertIsNone(CJK.search(blob))
        text = (ROOT / "data/output/lineage_inventory.json").read_text(encoding="utf-8")
        self.assertIsNone(CJK.search(text))


if __name__ == "__main__":
    unittest.main()
