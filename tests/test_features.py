"""Descriptive feature tests with hand-computed expectations."""
import unittest

from app.features import phase_current_spread_pct, series_summary, trend_slope_per_step


class TestFeatures(unittest.TestCase):
    def test_phase_spread(self):
        # (40-20)/30*100 = 66.67
        self.assertEqual(phase_current_spread_pct(40.0, 30.0, 20.0), 66.67)

    def test_phase_spread_missing(self):
        self.assertIsNone(phase_current_spread_pct(40.0, None, 20.0))

    def test_phase_spread_zero_mean(self):
        self.assertIsNone(phase_current_spread_pct(1.0, -1.0, 0.0))

    def test_series_summary(self):
        summary = series_summary([("2026-08-24 01:00:00", 3.0),
                                  ("2026-08-24 00:00:00", 1.0),
                                  ("2026-08-24 02:00:00", 2.0)])
        self.assertEqual(summary["minimum"], 1.0)
        self.assertEqual(summary["maximum"], 3.0)
        self.assertEqual(summary["mean"], 2.0)
        self.assertEqual(summary["first_observed_at"], "2026-08-24 00:00:00")

    def test_trend_slope(self):
        self.assertAlmostEqual(trend_slope_per_step([1.0, 2.0, 3.0]), 1.0)
        self.assertIsNone(trend_slope_per_step([5.0]))
        self.assertIsNone(trend_slope_per_step([]))


if __name__ == "__main__":
    unittest.main()
