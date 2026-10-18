"""Tests for the temporal audit (stdlib unittest, synthetic data only)."""
import hashlib
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.temporal_audit import (lag1_autocorr, parse_wallclock, pearson,  # noqa: E402
                                    quantile_linear)


class TestAuditPrimitives(unittest.TestCase):
    def test_strict_wallclock_parsing(self):
        self.assertEqual(parse_wallclock("2026-08-24 00:00:00"),
                         datetime(2026, 8, 24))
        self.assertIsNone(parse_wallclock("2026-08-24T00:00:00Z"))
        self.assertIsNone(parse_wallclock("2026-08-24 00:00:00+08:00"))
        self.assertIsNone(parse_wallclock("not-a-time"))
        self.assertIsNone(parse_wallclock(None))

    def test_quantile_convention(self):
        self.assertEqual(quantile_linear([1.0, 2.0, 3.0, 4.0], 50), 2.5)
        self.assertIsNone(quantile_linear([], 50))

    def test_pearson_and_autocorr(self):
        self.assertAlmostEqual(pearson([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0)
        self.assertIsNone(pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))
        self.assertAlmostEqual(lag1_autocorr([1.0, 2.0, 3.0, 4.0]), 1.0)
        self.assertIsNone(lag1_autocorr([1.0, 2.0]))


class TestAuditDeterminism(unittest.TestCase):
    def test_rerun_identical(self):
        out = ROOT / "data/output/temporal_audit.json"
        first = subprocess.run([sys.executable, "scripts/temporal_audit.py"],
                               capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(first.returncode, 0, first.stderr)
        digest_a = hashlib.sha256(out.read_bytes()).hexdigest()
        second = subprocess.run([sys.executable, "scripts/temporal_audit.py"],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(digest_a, hashlib.sha256(out.read_bytes()).hexdigest())

    def test_frozen_inputs_present(self):
        import json
        report = json.loads((ROOT / "data/output/temporal_audit.json").read_text())
        self.assertEqual(report["timestamps"]["distinct_count"], 98)
        self.assertEqual(report["timestamps"]["timestamps_with_all_14_codes"], 98)
        self.assertEqual(report["recomputation"]["discrepancies"], [])


if __name__ == "__main__":
    unittest.main()
