"""Tests for metric definitions (stdlib unittest, hand-calculated cases)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.evaluate_synthetic import (evaluate_scenario, group_events,  # noqa: E402
                                        rank_precision,
                                        rank_precision_target_only)


class MetricTestCase(unittest.TestCase):
    def test_recall_counts_overlapped_truth_events(self):
        series = {"A": [(str(i), 10.0) for i in range(30)]}
        truth = {"events": [{"code": "A", "index": 20},
                            {"code": "A", "index": 21}],
                 "control_code": "CTRL"}
        spec = {"scenario_type": "sustained_shift", "window": [20, 22]}
        result = evaluate_scenario("S", spec, series, truth)
        self.assertEqual(result["event_recall"], 0.0)
        self.assertEqual(result["control_false_alarms"], 0)

    def test_precision_pooling_explains_spike_case(self):
        # Hand calculation: one truth flag scored 6.0 on target; a control
        # flag scores 33.0. Pooled top-1 (K=1 truth event) picks the control.
        flags = [(6.0, "A_phsA", 44), (33.0, "A_phsB", 24)]
        self.assertEqual(rank_precision(flags, {44}, "A_phsB"), 0.0)
        # Recall-style check on the same data still finds the truth flag.
        self.assertEqual(rank_precision_target_only(flags, {44}, "A_phsB"), 1.0)

    def test_precision_empty_and_control_only(self):
        self.assertIsNone(rank_precision([], {5}, "C"))
        self.assertIsNone(rank_precision_target_only([], {5}, "C"))
        flags = [(9.0, "C", 5)]
        self.assertEqual(rank_precision(flags, {5}, "C"), 0.0)

    def test_delay_measures_from_window_start(self):
        series = {"A": [(str(i), 10.0) for i in range(15)]
                  + [(str(i), 50.0) for i in range(15, 25)]}
        truth = {"events": [{"code": "A", "index": i} for i in range(15, 25)],
                 "control_code": "CTRL"}
        spec = {"scenario_type": "sustained_shift", "window": [15, 25]}
        result = evaluate_scenario("S", spec, series, truth)
        self.assertIsNotNone(result["detection_delay_steps"])
        self.assertGreaterEqual(result["detection_delay_steps"], 0)

    def test_missing_abstains(self):
        series = {"A": [(str(i), 10.0) for i in range(10)]
                  + [(str(i), None) for i in range(10, 13)]
                  + [(str(i), 10.0) for i in range(13, 20)]}
        truth = {"events": [{"code": "A", "index": i} for i in range(10, 13)],
                 "control_code": "CTRL"}
        spec = {"scenario_type": "missing_data", "window": [0, 0]}
        result = evaluate_scenario("S", spec, series, truth)
        self.assertEqual(result["abstentions"], 3)

    def test_event_grouping(self):
        self.assertEqual(group_events([False, True, True, False, True]),
                         [(1, 2), (4, 4)])
        self.assertEqual(group_events([False, False]), [])

    def test_historical_fields_preserved(self):
        import json
        evaluation = json.loads(
            (ROOT / "data/output/synthetic/evaluation.json").read_text(encoding="utf-8"))
        spike = evaluation["results"]["SYN-SPIKE-001"]
        self.assertEqual(spike["event_recall"], 1.0)
        self.assertEqual(spike["precision_at_capacity"], 0.0)
        self.assertIn("precision_at_capacity_target_only", spike)


if __name__ == "__main__":
    unittest.main()
