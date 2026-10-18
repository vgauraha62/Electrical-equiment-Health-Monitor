"""Tests for source-file auditing (stdlib unittest)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_source_files import pdf_pages  # noqa: E402


class SourceAuditTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = json.loads(
            (ROOT / "data/output/source_file_audit.json").read_text(encoding="utf-8"))

    def test_expected_files_present(self):
        names = {f["filename"] for f in self.audit["files"]}
        for required in ("缺陷记录.xlsx", "设备台账池.xlsx"):
            self.assertTrue(any(required in name for name in names), required)

    def test_row_counts_match_known_values(self):
        by_name = {}
        for entry in self.audit["files"]:
            for sheet, count in (entry.get("sheets") or {}).items():
                by_name[(entry["filename"], sheet)] = count
        matches = [v for (name, _), v in by_name.items() if "缺陷记录" in name]
        self.assertIn(1012, matches)
        matches = [v for (name, _), v in by_name.items() if "设备台账" in name]
        self.assertIn(3812, matches)

    def test_pdf_page_counts(self):
        pages = {f["filename"]: f.get("pages") for f in self.audit["files"]}
        std = [v for k, v in pages.items() if "运维规程" in k]
        guide = [v for k, v in pages.items() if "评价导则" in k]
        self.assertEqual(std, [49])
        self.assertEqual(guide, [56])

    def test_matrix_covers_six_files(self):
        matrix = json.loads(
            (ROOT / "docs/translation_review_matrix.json").read_text(encoding="utf-8"))
        ids = {row["source_id"] for row in matrix["rows"]}
        for expected in ("SRC-STD-OPS-2014", "SRC-STD-EVAL-GUIDE", "SRC-DEFECT-LIB",
                         "SRC-DEFECT-RECORDS", "SRC-LEDGER", "SRC-CALC-RULES"):
            self.assertIn(expected, ids)
        for row in matrix["rows"]:
            self.assertNotIn(row["status"], ("approved", "reviewed"))


if __name__ == "__main__":
    unittest.main()
