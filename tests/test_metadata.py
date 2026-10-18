"""Tests for optional source-metadata preservation (stdlib unittest, synthetic only).

Opaque values below are synthetic fixtures for software behavior, never real
source evidence and never engineering facts.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_measurements import parse_ts  # noqa: E402
from scripts.compare_serving import compare  # noqa: E402
from scripts.ingest import EXTRA_KEYS, build_source_extra, evidence_id  # noqa: E402


class TestSourceExtra(unittest.TestCase):
    def test_absent_keys_yield_null(self):
        self.assertIsNone(build_source_extra({"measTypeCode": "A_phsA"}))
        self.assertIsNone(build_source_extra({}))

    def test_present_null_keys_preserved(self):
        extra = build_source_extra({"pt": None, "ct": None, "measTypeCode": "x"})
        self.assertEqual(extra, {"pt": None, "ct": None})

    def test_populated_values_verbatim_no_coercion(self):
        extra = build_source_extra({"pt": "10", "ct": 5, "tFactor": 1.5,
                                    "astId": "A-1", "unknownKey": "dropped?"})
        self.assertEqual(extra, {"pt": "10", "ct": 5, "tFactor": 1.5, "astId": "A-1"})
        self.assertIsInstance(extra["ct"], int)
        self.assertNotIn("unknownKey", extra)

    def test_all_seven_keys_known(self):
        self.assertEqual(tuple(EXTRA_KEYS),
                         ("astId", "astType", "consNo", "consType", "pt", "ct", "tFactor"))

    def test_evidence_id_stable_with_or_without_extras(self):
        row = {"source_file_id": "f.json", "source_record_index": 0,
               "measurement_point_id": "p", "measurement_type_code": "A_phsA",
               "observed_at": "2026-08-25 14:45:00", "dataset_type": "snapshot"}
        self.assertEqual(evidence_id(row), evidence_id(dict(row)))
        self.assertTrue(parse_ts(row["observed_at"]) is not None)


def sized_canonical(extra, evidence="ev_1"):
    return {"evidence_id": evidence, "asset_id": "a1",
            "measurement_point_id": "p1", "measurement_type_code": "A_phsA",
            "measurement_name": "Phase A current", "measurement_value": 1.0,
            "observed_at_original": "2026-08-24 00:00:00",
            "dataset_type": "history", "data_origin": "observed",
            "source_ref": {"source_file_id": "f.json", "source_record_index": 0,
                           "source_extra": extra}}


def sized_legacy():
    return {"source_file_id": "f.json", "source_record_index": 0,
            "psr_id": "a1", "measurement_point_id": "p1",
            "measurement_type_code": "A_phsA",
            "measurement_name": "Phase A current", "measurement_value": 1.0,
            "observed_at": "2026-08-24 00:00:00", "dataset_type": "history",
            "data_origin": "observed"}


class TestMetadataComparison(unittest.TestCase):
    def test_null_or_absent_extras_no_gap(self):
        for extra in (None, {"pt": None, "ct": None}):
            result = compare([sized_canonical(extra)], [sized_legacy()])
            self.assertEqual(result["metadata_serving_gap_count"], 0)

    def test_populated_extra_reports_serving_gap(self):
        result = compare([sized_canonical({"pt": "10", "ct": None})],
                         [sized_legacy()])
        self.assertEqual(result["metadata_serving_gap_count"], 1)
        self.assertEqual(result["metadata_serving_gaps"][0]["evidence_id"], "ev_1")
        self.assertEqual(result["metadata_serving_gaps"][0]["populated_keys"], ["pt"])
        # Value agreement itself still holds; only metadata carriage differs.
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["disagreements"], [])


if __name__ == "__main__":
    unittest.main()
