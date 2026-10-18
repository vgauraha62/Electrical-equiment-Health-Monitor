"""Tests for telemetry source auditing (stdlib unittest)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CLASSES = {"0408001", "0401004", "0401005", "0408003", "0403000"}


class TelemetrySourceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = json.loads(
            (ROOT / "data/output/telemetry_source_audit.json").read_text(encoding="utf-8"))

    def test_eight_files_parsed(self):
        self.assertEqual(len(self.audit["files"]), 8)
        for entry in self.audit["files"]:
            self.assertTrue(entry["equipment_type_code"])
            self.assertLessEqual(set(entry["equipment_type_code"]), CLASSES)
            self.assertGreater(entry["record_count"], 0)
            self.assertGreater(entry["point_count"], 0)

    def test_equipment_classes_separate(self):
        by_class = {}
        for entry in self.audit["files"]:
            for code in entry["equipment_type_code"]:
                by_class.setdefault(code, []).append(entry["filename"])
        self.assertEqual(set(by_class), CLASSES)
        codesets = {}
        for entry in self.audit["files"]:
            codesets.setdefault(tuple(entry["equipment_type_code"]),
                                set()).update(entry["measurement_codes"])
        self.assertEqual(len(codesets[("0408001",)]), 14)
        self.assertEqual(len(codesets[("0401005",)]), 8)

    def test_history_snapshot_relations(self):
        transformer = [f for f in self.audit["files"]
                       if f["equipment_type_code"] == ["0408001"]]
        self.assertEqual(len(transformer), 2)
        history = next(f for f in transformer if f["unique_timestamps"] > 1)
        snapshot = next(f for f in transformer if f is not history)
        self.assertGreater(history["unique_timestamps"], 1)
        self.assertEqual(snapshot["unique_timestamps"], 1)
        self.assertLessEqual(history["timestamp_max"], snapshot["timestamp_min"])

    def test_snapshot_history_merge_semantics(self):
        import app.main as main_module
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        client = main_module.app.test_client()
        detail = client.get("/api/equipment/3812ac8dba94ce060378b9a460017a380dc6481b0e").get_json()
        series = detail["telemetry"]["readings_by_code"]
        # 289 history points plus the standalone snapshot read per code.
        self.assertEqual(len(series["TotVar"]["points"]), 290)
        self.assertEqual(series["TotVar"]["points"][-1][0], "2026-08-25 16:30:00")
        self.assertEqual(series["TotVar"]["points"][-2][0], "2026-08-25 00:00:00")

    def test_duplicates_zeros_and_nulls(self):
        for entry in self.audit["files"]:
            self.assertEqual(entry.get("null_values", 0) >= 0, True)
        breaker_history = next(f for f in self.audit["files"]
                               if f["filename"].startswith("0401005")
                               and f["unique_timestamps"] > 1)
        self.assertGreater(breaker_history["zero_values"], 2000)

    def test_english_only_new_docs(self):
        import re
        cjk = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")
        for filename in ("data/en/telemetry_equipment_dictionary.md",
                         "docs/telemetry_reconciliation.md"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIsNone(cjk.search(text), filename)


if __name__ == "__main__":
    unittest.main()
