"""Tests for descriptive analysis (stdlib unittest, synthetic data only)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analyze_measurements import (TARGET_ASSET, analyze, deduplicate,  # noqa: E402
                                          summarize_series)


def record(code="A_phsA", value=1.0, time="2026-08-24 00:00:00", point="p1",
           dataset="history", evidence="ev_1"):
    return {"asset_id": TARGET_ASSET, "measurement_point_id": point,
            "measurement_type_code": code, "measurement_value": value,
            "observed_at_original": time, "evidence_id": evidence,
            "dataset_type": dataset, "unit_status": "unconfirmed",
            "source_ref": {"source_file_id": "f.json", "source_record_index": 0}}


def legacy_row(point="p1", code="A_phsA", equipment="0408001"):
    return {"source_file_id": "f.json", "source_record_index": 0,
            "measurement_point_id": point, "measurement_type_code": code,
            "equipment_type_code": equipment}


class TestSummarize(unittest.TestCase):
    def test_statistics_correctness(self):
        records = [record(value=v, time=f"2026-08-24 0{i}:00:00", evidence=f"ev_{i}")
                   for i, v in enumerate([1.0, 2.0, 3.0, 4.0])]
        summary = summarize_series(records)
        self.assertEqual(summary["observation_count"], 4)
        self.assertEqual(summary["minimum"], 1.0)
        self.assertEqual(summary["maximum"], 4.0)
        self.assertEqual(summary["mean"], 2.5)
        self.assertEqual(summary["median"], 2.5)
        self.assertAlmostEqual(summary["stdev_population"], 1.118034, places=5)
        self.assertAlmostEqual(summary["sequence_trend_slope_per_step"], 1.0)
        self.assertEqual(summary["unit_status"], "unconfirmed")

    def test_zeros_and_missing(self):
        records = [record(value=0.0, evidence="ev_0"),
                   record(value=2.0, time="2026-08-24 01:00:00", evidence="ev_1"),
                   record(value=None, time="2026-08-24 02:00:00", evidence="ev_2")]
        summary = summarize_series(records)
        self.assertEqual(summary["zero_count"], 1)
        self.assertAlmostEqual(summary["zero_proportion"], 0.5)
        self.assertEqual(summary["missing_or_non_numeric"], 1)
        self.assertEqual(summary["numeric_count"], 2)

    def test_irregular_gaps(self):
        records = [record(time="2026-08-24 00:00:00", evidence="ev_0"),
                   record(time="2026-08-24 00:15:00", evidence="ev_1"),
                   record(time="2026-08-24 05:15:00", evidence="ev_2")]
        summary = summarize_series(records)
        self.assertEqual(summary["sampling"]["interval_max"], 300.0)
        self.assertEqual(summary["sampling"]["intervals_over_60min"], 1)

    def test_empty_input(self):
        summary = summarize_series([])
        self.assertEqual(summary["observation_count"], 0)
        self.assertIsNone(summary["mean"])
        self.assertIsNone(summary["sequence_trend_slope_per_step"])


class TestDedup(unittest.TestCase):
    def test_consistent_overlap_counted_once(self):
        records = [record(dataset="history", evidence="ev_h"),
                   record(dataset="snapshot", evidence="ev_s")]
        unique, consistent, conflicts = deduplicate(records)
        self.assertEqual(len(unique), 1)
        self.assertEqual(len(consistent), 1)
        self.assertEqual(conflicts, [])

    def test_conflicting_overlap_excluded(self):
        records = [record(value=1.0, dataset="history", evidence="ev_h"),
                   record(value=9.0, dataset="snapshot", evidence="ev_s")]
        unique, consistent, conflicts = deduplicate(records)
        self.assertEqual(unique, [])
        self.assertEqual(len(conflicts), 1)


class TestAnalyze(unittest.TestCase):
    def test_scope_determinism_and_foreign(self):
        records = [record(evidence="ev_1"), record(code="PhV_phsA", value=230.0,
                   point="p2", evidence="ev_2")]
        legacy = [legacy_row(), legacy_row(point="p2", code="PhV_phsA")]
        first = analyze(records, legacy)
        second = analyze(records, legacy)
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first["scope"]["scoped_records"], 2)
        self.assertEqual(first["scope"]["non_target_equipment_mapped"], [])
        legacy_bad = [legacy_row(), legacy_row(point="p2", code="PhV_phsA",
                                               equipment="0401004")]
        flagged = analyze(records, legacy_bad)
        self.assertIn(["0401004"], flagged["scope"]["non_target_equipment_mapped"])


if __name__ == "__main__":
    unittest.main()
