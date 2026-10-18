"""Tests for reusable audit functions (stdlib unittest, synthetic data only)."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_measurements import (audit, intervals_minutes, parse_ts,  # noqa: E402
                                        percentile, series_audit)


def record(**overrides):
    base = {"asset_id": "a1", "measurement_point_id": "p1",
            "measurement_type_code": "A_phsA", "measurement_value": 1.0,
            "observed_at_original": "2026-08-24 00:00:00",
            "evidence_id": "ev_x", "data_origin": "observed",
            "dataset_type": "history", "unit_status": "unconfirmed",
            "timezone_status": "unconfirmed", "quality_status": "valid",
            "measurement_value_status": "present", "schema_version": "1.0.0",
            "quality_flags": "unit not confirmed", "original_unit": None,
            "normalized_unit": None, "measurement_name": "Phase A current"}
    base.update(overrides)
    return base


class TestParseTs(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_ts("2026-08-24 00:00:00"), datetime(2026, 8, 24))

    def test_rejects_timezone_suffix(self):
        self.assertIsNone(parse_ts("2026-08-24T00:00:00Z"))
        self.assertIsNone(parse_ts("2026-08-24 00:00:00+08:00"))

    def test_rejects_garbage_and_range(self):
        self.assertIsNone(parse_ts("not-a-time"))
        self.assertIsNone(parse_ts(""))
        self.assertIsNone(parse_ts(None))
        self.assertIsNone(parse_ts("1800-01-01 00:00:00"))


class TestIntervals(unittest.TestCase):
    def test_intervals_and_percentile(self):
        stamps = [datetime(2026, 8, 24, 0, 0), datetime(2026, 8, 24, 0, 15),
                  datetime(2026, 8, 24, 1, 15)]
        self.assertEqual(intervals_minutes(stamps), [15.0, 60.0])
        self.assertEqual(percentile([15.0, 60.0], 50), 37.5)
        self.assertIsNone(percentile([], 50))

    def test_series_audit(self):
        stamps = [datetime(2026, 8, 24, 0, 0), datetime(2026, 8, 24, 0, 15),
                  datetime(2026, 8, 24, 0, 30)]
        result = series_audit(stamps)
        self.assertEqual(result["observation_count"], 3)
        self.assertEqual(result["interval_p50"], 15.0)
        self.assertEqual(result["intervals_over_60min"], 0)


class TestAudit(unittest.TestCase):
    def test_duplicates_missing_and_labels(self):
        records = [
            record(evidence_id="ev_1", observed_at_original="2026-08-24 00:00:00"),
            record(evidence_id="ev_1", observed_at_original="2026-08-24 00:00:00"),
            record(evidence_id="ev_2", observed_at_original="bad-time",
                   measurement_value=float("nan")),
        ]
        summary = audit(records)
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["duplicate_evidence_ids"], 1)
        self.assertEqual(summary["duplicate_asset_point_code_time"], 1)
        self.assertEqual(summary["unparseable_timestamps"], 1)
        self.assertEqual(summary["non_finite_values"], 1)
        self.assertEqual(summary["label_like_keys_present"], [])
        self.assertFalse(summary["units_verified_anywhere"])
        self.assertFalse(summary["timezone_verified_anywhere"])
        self.assertIn("original_unit", summary["missing_by_field"])


if __name__ == "__main__":
    unittest.main()
