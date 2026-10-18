"""Tests for canonical-vs-serving comparison (stdlib unittest, synthetic data)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.compare_serving import compare  # noqa: E402


def canonical(evidence="ev_1", value=1.0, asset="a1", time="2026-08-24 00:00:00"):
    return {"evidence_id": evidence, "asset_id": asset,
            "measurement_point_id": "p1", "measurement_type_code": "A_phsA",
            "measurement_name": "Phase A current", "measurement_value": value,
            "observed_at_original": time, "dataset_type": "history",
            "data_origin": "observed",
            "source_ref": {"source_file_id": "f.json", "source_record_index": 0}}


def legacy(value=1.0, asset="a1", time="2026-08-24 00:00:00"):
    return {"source_file_id": "f.json", "source_record_index": 0,
            "psr_id": asset, "measurement_point_id": "p1",
            "measurement_type_code": "A_phsA",
            "measurement_name": "Phase A current", "measurement_value": value,
            "observed_at": time, "dataset_type": "history",
            "data_origin": "observed"}


class TestCompare(unittest.TestCase):
    def test_full_match(self):
        result = compare([canonical()], [legacy()])
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["disagreements"], [])

    def test_value_disagreement(self):
        result = compare([canonical()], [legacy(value=2.0)])
        self.assertEqual(result["matched"], 1)
        self.assertEqual(len(result["disagreements"]), 1)
        self.assertEqual(result["disagreements"][0]["field"], "measurement_value")

    def test_asset_mismatch(self):
        result = compare([canonical()], [legacy(asset="other")])
        self.assertTrue(any(d["field"] == "asset_id_vs_psr_id"
                            for d in result["disagreements"]))

    def test_unmatched_both_sides(self):
        result = compare([canonical()], [legacy(time="2026-08-25 00:00:00")])
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["unmatched_canonical"], ["ev_1"])
        self.assertEqual(len(result["unmatched_serving"]), 1)

    def test_duplicate_serving_key(self):
        result = compare([canonical()], [legacy(), legacy()])
        self.assertEqual(result["matched"], 0)
        self.assertTrue(any(d["field"] == "__duplicate_serving_key__"
                            for d in result["disagreements"]))


if __name__ == "__main__":
    unittest.main()
