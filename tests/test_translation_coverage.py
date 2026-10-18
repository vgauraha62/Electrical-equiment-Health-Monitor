"""Translation-coverage acceptance tests (stdlib unittest).

Verifies manifest completeness, count agreement between sources and translated
records, provenance on every translated record, explicit gap reporting, no
approved content without review, and unchanged measurement outputs.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPECTED_SOURCES = {
    "SRC-ZIP-RELATED", "SRC-STD-OPS-2014", "SRC-STD-EVAL-GUIDE",
    "SRC-DEFECT-LIB", "SRC-DEFECT-RECORDS", "SRC-LEDGER", "SRC-CALC-RULES",
    "SRC-TELEMETRY-TXT", "SRC-ZIP-AGENTS", "SRC-AGENT-MENU",
    "SRC-AGENT-DIAGRAMS", "SRC-PDF-HEALTH", "SRC-ZIP-EXPORT",
}


class TestCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (ROOT / "docs/english_source_manifest.json").read_text(encoding="utf-8"))

    def test_every_artifact_has_manifest_entry(self):
        ids = {s["source_id"] for s in self.manifest["sources"]}
        self.assertEqual(ids, EXPECTED_SOURCES)

    def test_no_approved_without_review(self):
        for source in self.manifest["sources"]:
            self.assertNotEqual(source.get("review_status"), "approved")
            self.assertNotEqual(source.get("translation_status"), "approved")

    def test_defect_record_counts_agree(self):
        records = json.loads(
            (ROOT / "data/en/defect_records_translated.json").read_text())
        self.assertEqual(len(records), 1011)
        rows = sorted(r["source"]["source_row"] for r in records)
        self.assertEqual(rows, list(range(2, 1013)))

    def test_defect_library_counts_agree(self):
        records = json.loads(
            (ROOT / "data/en/defect_library_translated.json").read_text())
        self.assertEqual(len(records), 104)
        self.assertTrue(all(r["source"]["workbook"] == "SRC-DEFECT-LIB" for r in records))

    def test_every_record_has_provenance(self):
        for filename in ("data/en/defect_records_translated.json",
                         "data/en/defect_library_translated.json"):
            for record in json.loads((ROOT / filename).read_text()):
                source = record.get("source", {})
                self.assertTrue(source.get("workbook"))
                self.assertTrue(source.get("sheet"))
                self.assertIsInstance(source.get("source_row"), int)
                self.assertEqual(record.get("translation_status"), "translated_draft")

    def test_gaps_reported_not_silent(self):
        flagged = 0
        total = 0
        for filename in ("data/en/defect_records_translated.json",
                         "data/en/defect_library_translated.json"):
            for record in json.loads((ROOT / filename).read_text()):
                for value in record.values():
                    if not isinstance(value, dict):
                        continue
                    total += 1
                    spans = value.get("untranslated_spans", [])
                    if spans:
                        flagged += 1
                        self.assertTrue(value.get("needs_review"),
                                        f"spans without needs_review in {filename}")
        self.assertGreater(flagged, 0)
        calc = (ROOT / "data/en/calc_rules.md").read_text(encoding="utf-8")
        self.assertIn("AMBIGUOUS", calc)

    def test_personal_data_excluded(self):
        text = (ROOT / "data/en/defect_records_translated.json").read_text()
        blob = json.loads(text)
        field_names = set()
        for record in blob:
            field_names.update(record.keys())
        for excluded in ("discoverer_name", "eliminated_user_name", "creater_name",
                         "maintainer_name", "register_name", "editor_name"):
            self.assertNotIn(excluded, field_names)

    def test_measurement_outputs_unchanged(self):
        for filename, expected in (("data/output/canonical_measurements.ndjson", 5970),
                                   ("data/output/telemetry_observations.ndjson", 5970)):
            lines = (ROOT / filename).read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum(1 for line in lines if line.strip()), expected)


if __name__ == "__main__":
    unittest.main()
