"""Tests for the synthetic anomaly harness (stdlib unittest)."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.evaluate_synthetic import (detector_input, group_events,  # noqa: E402
                                        multivariate_spread_check, rolling_mad)
from scripts.synthetic_scenarios import generate  # noqa: E402

CANONICAL = ROOT / "data/output/canonical_measurements.ndjson"
SYNTHETIC = ROOT / "data/output/synthetic"


class HarnessTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent_hash = hashlib.sha256(CANONICAL.read_bytes()).hexdigest()
        cls.parent_ids = {json.loads(line).get("evidence_id")
                          for line in CANONICAL.read_text(encoding="utf-8").splitlines()
                          if line.strip()}

    def test_deterministic_reruns(self):
        first = generate()
        second = generate()
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))
        self.assertEqual(hashlib.sha256(CANONICAL.read_bytes()).hexdigest(),
                         self.parent_hash)

    def test_lineage_and_isolation(self):
        _, derived, _ = generate()
        self.assertGreater(len(derived), 0)
        for row in derived:
            self.assertEqual(row["data_origin"], "synthetic")
            self.assertTrue(row["evidence_id"].startswith("syn_"))
            self.assertIn(row["parent_evidence_id"], self.parent_ids)
            self.assertNotIn(row["evidence_id"], self.parent_ids)
            view = detector_input(row)
            self.assertEqual(sorted(view),
                             ["code", "scenario_id", "timestamp", "value"])

    def test_spike_flagged_control_clean(self):
        base = [10.0] * 30
        spiked = base + [50.0] + [10.0] * 10
        flags = [flag for _, flag, _ in rolling_mad(spiked)]
        self.assertTrue(flags[30])
        self.assertFalse(any(flags[:30]))
        control = [flag for _, flag, _ in rolling_mad(base + [10.0] * 11)]
        self.assertFalse(any(control))

    def test_missing_abstains_never_imputed(self):
        values = [10.0] * 15 + [None, None] + [10.0] * 15
        scored = rolling_mad(values)
        self.assertTrue(all(abst for _, _, abst in (scored[15], scored[16])))
        self.assertTrue(all(s is None for s, _, _ in (scored[15], scored[16])))

    def test_shift_delay_and_grouping(self):
        values = [10.0] * 20 + [30.0] * 10 + [10.0] * 5
        flags = [flag for _, flag, _ in rolling_mad(values)]
        events = group_events(flags)
        self.assertTrue(any(start >= 20 for start, _ in events))

    def test_null_detector_never_flags(self):
        from scripts.evaluate_synthetic import K
        self.assertGreater(K, 0)
        values = [1.0, 100.0, -50.0, None, 3.0]
        abstentions = sum(1 for v in values if v is None)
        self.assertEqual(abstentions, 1)

    def test_manifest_truth_consistent(self):
        manifest = json.loads((SYNTHETIC / "manifest.json").read_text(encoding="utf-8"))
        truth = json.loads((SYNTHETIC / "truth.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["parent_dataset"]["sha256"], self.parent_hash)
        self.assertEqual(manifest["seed"], 18421)
        self.assertEqual({s["scenario_id"] for s in manifest["scenarios"]}, set(truth))
        self.assertIn("never split a scenario", manifest["partition_rule"])

    def test_multivariate_needs_three_series(self):
        self.assertIn("not applicable", multivariate_spread_check(
            {"A": [(str(i), 1.0) for i in range(20)]})["note"])


if __name__ == "__main__":
    unittest.main()
