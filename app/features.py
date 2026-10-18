"""Defensible descriptive features only. No thresholds, no failure scores.

Formulas (documented here and in docs/evaluation_protocol.md):
- phase_current_spread_pct = (max - min) / abs(mean) * 100 over the three
  snapshot phase currents; None when fewer than 3 values or mean == 0.
- series_summary: count, first/last (time-ordered), min, max, arithmetic
  mean over non-null values.
- trend_slope_per_step: ordinary least-squares slope of value vs integer
  sequence index (descriptive only, not a forecast).
"""
from __future__ import annotations


def phase_current_spread_pct(a, b, c):
    values = [v for v in (a, b, c) if v is not None]
    if len(values) != 3:
        return None
    mean = sum(values) / 3
    if mean == 0:
        return None
    return round((max(values) - min(values)) / abs(mean) * 100, 2)


def series_summary(points):
    """points: list of (observed_at, value). Returns descriptive dict."""
    ordered = sorted(points, key=lambda item: item[0])
    values = [v for _, v in ordered if v is not None]
    summary = {
        "observation_count": len(ordered),
        "first_observed_at": ordered[0][0] if ordered else None,
        "last_observed_at": ordered[-1][0] if ordered else None,
        "first_value": ordered[0][1] if ordered else None,
        "last_value": ordered[-1][1] if ordered else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "mean": round(sum(values) / len(values), 6) if values else None,
    }
    return summary


def trend_slope_per_step(values):
    clean = [v for v in values if v is not None]
    n = len(clean)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(clean) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, clean)) / denom
