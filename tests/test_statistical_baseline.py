"""Tests for the statistical baseline (stdlib unittest, synthetic data only)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.statistical_baseline import (TARGET_ASSET, analyze, pearson,  # noqa: E402
                                          quantiles, tukey_candidates)


def record(code="A_phsA", value=1.0, time="2026-08-24 00:00:00", evidence="ev_1",
           **overrides):
    entry = {"asset_id": TARGET_ASSET,
            "measurement_point_id": "p1", "measurement_type_code": code,
            "measurement_value": value, "observed_at_original": time,
            "evidence_id": evidence, "dataset_type": "history",
            "data_origin": "observed",
            "source_ref": {"source_file_id": "f.json", "source_record_index": 0}}
    entry.update(overrides)
    return entry


class TestStats(unittest.TestCase):
    def test_quantiles(self):
        values = [1.0, 2.0, 3.0, 4.0]
        result = quantiles(values)
        self.assertEqual(result["p50"], 2.5)
        self.assertEqual(result["p25"], 1.75)
        self.assertEqual(quantiles([])["p50"], None)

    def test_tukey_flags_extreme_only(self):
        values = [10.0] * 10 + [1000.0]
        candidates = tukey_candidates(values)
        self.assertEqual(candidates["high"], [1000.0])
        self.assertEqual(candidates["low"], [])
        self.assertEqual(tukey_candidates([1.0])["note"], "n<4: fences not computed")

    def test_pearson(self):
        self.assertAlmostEqual(pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0])["r"], 1.0)
        self.assertIsNone(pearson([1.0], [1.0])["r"])
        self.assertIsNone(pearson([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])["r"])

    def test_missing_nonfinite_duplicates(self):
        records = [record(value=1.0, evidence="ev_1"),
                   record(value=None, time="2026-08-24 01:00:00", evidence="ev_2"),
                   record(value=float("nan"), time="2026-08-24 02:00:00",
                          evidence="ev_3"),
                   record(value=1.0, time="2026-08-24 00:00:00",
                          evidence="ev_dup", dataset_type="snapshot")]
        report = analyze(records)
        dist = report["distributions"]["A_phsA"]
        # Consistent snapshot/history overlap deduplicated to one observation.
        self.assertEqual(dist["count"], 2)
        self.assertEqual(dist["missing_or_non_numeric"], 1)
        self.assertEqual(report["population"]["excluded_non_finite_values"], ["ev_3"])
        self.assertEqual(len(report["population"]["overlap_consistent_deduped"]), 1)

    def test_non_target_scope_excluded(self):
        other = record()
        other["asset_id"] = "other-asset"
        report = analyze([other])
        self.assertEqual(report["population"]["eligible_records"], 0)
        self.assertEqual(report["population"]["excluded_non_target_scope"], 1)
        self.assertEqual(report["distributions"], {})

    def test_determinism(self):
        records = [record(value=float(i), time=f"2026-08-24 0{i}:00:00",
                          evidence=f"ev_{i}") for i in range(5)]
        first = analyze(records)
        second = analyze(records)
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
