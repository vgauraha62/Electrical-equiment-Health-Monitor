"""Tests for the equipment directory (stdlib unittest)."""
import json
import math
import re
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")
TARGET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"


class DirectoryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app.main as main_module
        cls.main = main_module
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        catalog = json.loads((ROOT / "data/output/equipment_catalog.json")
                             .read_text(encoding="utf-8"))
        cls.catalog = catalog

    def test_catalog_counts_and_unique_ids(self):
        self.assertEqual(len(self.catalog["records"]), 3811)
        psr_ids = [r["psr_id"] for r in self.catalog["records"]]
        self.assertEqual(len(set(psr_ids)), 3811)
        self.assertEqual(self.catalog["source"]["record_count"], 3811)

    def test_directory_counts(self):
        body = self.client.get("/api/equipment?per_page=1").get_json()
        counts = body["counts"]
        self.assertEqual(counts["ledger_records"], 3811)
        self.assertEqual(counts["telemetry_ids"], 13)
        self.assertEqual(counts["exact_matches"], 2)
        self.assertEqual(len(counts["unmatched_telemetry_ids"]), 11)
        # Default list is telemetry-backed only; full set needs coverage=all.
        self.assertEqual(body["total"], 13)
        self.assertTrue(all(i["coverage"] != "ledger only" for i in body["items"]))
        full = self.client.get("/api/equipment?coverage=all&per_page=3822").get_json()
        self.assertEqual(full["total"], 3822)
        self.assertIn(TARGET, [i["asset_id"] for i in full["items"]
                               if i["coverage"] == "both"])

    def test_exact_merge_no_fuzzy(self):
        from app.equipment import normalize_id
        self.assertEqual(normalize_id("  28DKX-3681  "), "28DKX-3681")
        self.assertNotEqual(normalize_id("28DKX-3681"), "28dkx-3681")
        self.assertIsNone(normalize_id("   "))
        body = self.client.get("/api/equipment?q=28DKX-3681").get_json()
        self.assertTrue(all("28DKX-3681" in i["asset_id"] for i in body["items"]))

    def test_coverage_labels_and_filters(self):
        both = self.client.get("/api/equipment?coverage=both").get_json()
        self.assertTrue(all(i["coverage"] == "both" for i in both["items"]))
        self.assertEqual(both["total"], 2)
        ledger_only = self.client.get("/api/equipment?coverage=ledger+only").get_json()
        self.assertTrue(all(i["coverage"] == "ledger only" for i in ledger_only["items"]))
        bad = self.client.get("/api/equipment?coverage=something")
        self.assertEqual(bad.status_code, 400)
        typed = self.client.get("/api/equipment?type=0408001").get_json()
        self.assertTrue(all("0408001" in i["equipment_type_codes"]
                            for i in typed["items"] if i["equipment_type_codes"]))

    def test_pagination_and_search(self):
        first = self.client.get("/api/equipment?page=1&per_page=100").get_json()
        self.assertEqual(first["total"], 13)
        self.assertEqual(len(first["items"]), 13)
        second = self.client.get("/api/equipment?page=2&per_page=100").get_json()
        self.assertEqual(second["items"], [])
        full_first = self.client.get("/api/equipment?coverage=all&page=1&per_page=100").get_json()
        full_second = self.client.get("/api/equipment?coverage=all&page=2&per_page=100").get_json()
        self.assertEqual(len(full_first["items"]), 100)
        self.assertFalse({i["asset_id"] for i in full_first["items"]}
                         & {i["asset_id"] for i in full_second["items"]})
        self.assertEqual(self.client.get("/api/equipment?page=x").status_code, 400)
        missing = self.client.get("/api/equipment/no-such-asset")
        self.assertEqual(missing.status_code, 404)
        by_readings = self.client.get("/api/equipment?sort=readings&per_page=5").get_json()
        counts = [i["observation_count"] for i in by_readings["items"]]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(self.client.get("/api/equipment?sort=bogus").status_code, 400)

    def test_ledger_only_detail(self):
        ledger_id = next(i["asset_id"] for i in
                         self.client.get("/api/equipment?coverage=ledger+only&per_page=5")
                         .get_json()["items"])
        body = self.client.get(f"/api/equipment/{ledger_id}").get_json()
        self.assertEqual(body["coverage"], "ledger only")
        self.assertIsNone(body["telemetry"])
        self.assertEqual(body["note"],
                         "No telemetry readings are available for this equipment.")
        self.assertTrue(body["ledger"]["psr_id"])
        blob = json.dumps(body, ensure_ascii=False)
        self.assertIsNone(CJK.search(blob))

    def test_non_transformer_concise_note(self):
        body = self.client.get("/api/equipment?coverage=telemetry+only").get_json()
        candidate = next(i["asset_id"] for i in body["items"])
        detail = self.client.get(f"/api/equipment/{candidate}").get_json()
        self.assertEqual(detail["analysis_note"],
                         "Readings are shown descriptively; no reviewed assessment "
                         "is configured for this equipment type.")
        self.assertNotIn("unsupported", detail)
        self.assertNotIn("decision", json.dumps(detail))
        self.assertNotIn("phase_current_spread", json.dumps(detail))
        self.assertIn("readings_by_code", detail["telemetry"])
        codes = detail["telemetry"]["readings_by_code"]
        self.assertGreater(len(codes), 0)
        sample = next(iter(codes.values()))
        self.assertIn("points", sample)
        self.assertTrue(all(len(point) == 2 for point in sample["points"]))
        self.assertIn("Raw values only", detail["value_caveat"])

    def test_transformer_behavior_intact(self):
        detail = self.client.get(f"/api/equipment/{TARGET}").get_json()
        self.assertEqual(detail["coverage"], "both")
        self.assertIn("unconfirmed", detail["value_caveat"])
        codes = detail["telemetry"]["readings_by_code"]
        self.assertEqual(len(codes), 14)
        self.assertIn("A_phsA", codes)
        self.assertIn("TotW", codes)
        self.assertIn("TotVar", codes)
        for code, series in codes.items():
            self.assertIn("observation_count", series)
            self.assertIn("minimum", series)
            self.assertIn("maximum", series)
            self.assertIn("mean", series)
            self.assertIn("stdev", series)
            self.assertIn("zero_count", series)
            self.assertIn("zero_proportion", series)
        # Existing transformer endpoints unchanged.
        evidence = self.client.get(f"/api/assets/{TARGET}/evidence").get_json()
        self.assertEqual(evidence["observation_count"], 1372)

    def test_transformer_readings_match_source_baseline(self):
        # The per-code statistics are defined against source observations, so
        # cross-check the endpoint against the frozen M1.6 baseline derived from
        # the canonical measurements store (scripts/analyze_measurements.py).
        detail = self.client.get(f"/api/equipment/{TARGET}").get_json()
        codes = detail["telemetry"]["readings_by_code"]
        baseline = json.loads(
            (ROOT / "data/output/descriptive_analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["scope"]["asset_id"], TARGET)
        self.assertEqual(baseline["scope"]["scoped_records"], 1372)
        frozen = baseline["series"]
        self.assertEqual(set(codes), set(frozen))
        for code, series in codes.items():
            ref = frozen[code]
            self.assertEqual(series["observation_count"], ref["observation_count"])
            self.assertEqual(series["minimum"], ref["minimum"])
            self.assertEqual(series["maximum"], ref["maximum"])
            self.assertEqual(series["mean"], ref["mean"])
            # Endpoint key is `stdev`; the frozen baseline calls it
            # `stdev_population` (same population standard deviation).
            self.assertEqual(series["stdev"], ref["stdev_population"])
            self.assertEqual(series["zero_count"], ref["zero_count"])
            self.assertEqual(series["zero_proportion"], ref["zero_proportion"])

    def test_transformer_readings_stats_are_self_consistent(self):
        # Independently recompute every statistic from the returned raw points.
        detail = self.client.get(f"/api/equipment/{TARGET}").get_json()
        for series in detail["telemetry"]["readings_by_code"].values():
            finite = [v for _, v in series["points"]
                      if isinstance(v, (int, float)) and not isinstance(v, bool)
                      and math.isfinite(v)]
            zeros = sum(1 for v in finite if v == 0)
            if finite:
                self.assertEqual(series["minimum"], round(min(finite), 6))
                self.assertEqual(series["maximum"], round(max(finite), 6))
                self.assertEqual(series["mean"], round(statistics.fmean(finite), 6))
                self.assertEqual(series["zero_count"], zeros)
                self.assertEqual(series["zero_proportion"],
                                 round(zeros / len(finite), 6))
            else:
                self.assertIsNone(series["minimum"])
                self.assertIsNone(series["maximum"])
                self.assertIsNone(series["mean"])
                self.assertIsNone(series["zero_proportion"])
            if len(finite) >= 2:
                self.assertEqual(series["stdev"], round(statistics.pstdev(finite), 6))
            else:
                self.assertIsNone(series["stdev"])

    def test_non_transformer_readings_stats_are_self_consistent(self):
        # The same descriptive summary is exposed for non-transformer telemetry.
        body = self.client.get("/api/equipment?coverage=telemetry+only").get_json()
        candidate = next(i["asset_id"] for i in body["items"])
        detail = self.client.get(f"/api/equipment/{candidate}").get_json()
        for series in detail["telemetry"]["readings_by_code"].values():
            for key in ("observation_count", "minimum", "maximum", "mean",
                        "stdev", "zero_count", "zero_proportion"):
                self.assertIn(key, series)

    def test_english_only_directory(self):
        blob = self.client.get("/api/equipment?per_page=50").get_data(as_text=True)
        self.assertIsNone(CJK.search(blob))
        catalog_text = (ROOT / "data/output/equipment_catalog.json").read_text(
            encoding="utf-8")
        self.assertIsNone(CJK.search(catalog_text))


if __name__ == "__main__":
    unittest.main()
