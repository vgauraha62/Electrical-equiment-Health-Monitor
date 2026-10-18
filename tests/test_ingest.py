"""Ingestion, evidence-ID, determinism, and DQ tests."""
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ingest import evidence_id, valid_timestamp  # noqa: E402


class TestIngest(unittest.TestCase):
    def test_evidence_id_matches_app_formula(self):
        row = {"source_file_id": "f.json", "source_record_index": 0,
               "measurement_point_id": "p", "measurement_type_code": "A_phsA",
               "observed_at": "2026-08-25 14:45:00", "dataset_type": "snapshot"}
        material = "|".join(str(row.get(k, "")) for k in (
            "source_file_id", "source_record_index", "measurement_point_id",
            "measurement_type_code", "observed_at", "dataset_type"))
        expected = "ev_" + hashlib.sha256(material.encode()).hexdigest()[:20]
        self.assertEqual(evidence_id(row), expected)

    def test_timestamp_validation(self):
        self.assertTrue(valid_timestamp("2026-08-25 14:45:00"))
        self.assertFalse(valid_timestamp("2026-08-25T14:45:00Z"))
        self.assertFalse(valid_timestamp("not-a-time"))
        self.assertFalse(valid_timestamp("1800-01-01 00:00:00"))
        self.assertFalse(valid_timestamp(""))

    def test_ingest_deterministic_and_dq_reconciles(self):
        first = subprocess.run([sys.executable, "scripts/ingest.py"],
                               capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(first.returncode, 0, first.stderr)
        canonical = ROOT / "data/output/canonical_measurements.ndjson"
        digest_a = hashlib.sha256(canonical.read_bytes()).hexdigest()
        second = subprocess.run([sys.executable, "scripts/ingest.py"],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(second.returncode, 0, second.stderr)
        digest_b = hashlib.sha256(canonical.read_bytes()).hexdigest()
        self.assertEqual(digest_a, digest_b)
        dq = json.loads((ROOT / "data/output/dq_report.json").read_text())
        manifest = json.loads((ROOT / "data/output/manifest.json").read_text())
        n_records = sum(1 for _ in canonical.read_text().splitlines() if _.strip())
        self.assertEqual(dq["accepted"], n_records)
        self.assertEqual(manifest["record_counts"]["accepted"], n_records)
        self.assertEqual(dq["rejected"], manifest["record_counts"]["quarantined"])
        # Every canonical record preserves originals and marks unknowns explicitly.
        for line in canonical.read_text().splitlines()[:5]:
            record = json.loads(line)
            self.assertIn("observed_at_original", record)
            self.assertEqual(record["timezone_status"], "unconfirmed")
            self.assertEqual(record["unit_status"], "unconfirmed")
            self.assertEqual(record["data_origin"], "observed")


if __name__ == "__main__":
    unittest.main()
