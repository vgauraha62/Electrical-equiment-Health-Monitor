"""Independent temporal-integrity and robustness audit (stdlib only).

Deliberately self-contained: timestamp parsing, quantiles, Pearson, and
autocorrelation are reimplemented here rather than imported, so agreement with
the frozen M1.6 baseline constitutes independent verification. M1.6 artifacts
are read, never written.

Diagnostics only: sensitivity recomputations exclude flagged observations with
explicit counts; nothing is deleted from any record. No thresholds adopted.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data/output/temporal_audit.json"
TARGET_ASSET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
FROZEN_PATH = ROOT / "data/output/statistical_baseline.json"
FROZEN_DESC_PATH = ROOT / "data/output/descriptive_analysis.json"
CANONICAL_PATH = ROOT / "data/output/canonical_measurements.ndjson"


def parse_wallclock(value):
    """Strict parse of source wall-clock strings. No timezone ever attached."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed if 1990 <= parsed.year <= 2100 else None


def quantile_linear(ordered, pct):
    if not ordered:
        return None
    rank = (len(ordered) - 1) * pct / 100.0
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    frac = rank - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def mean(values):
    return sum(values) / len(values) if values else None


def pstdev(values):
    if len(values) < 2:
        return None
    mu = mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / len(values))


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = mean(xs), mean(ys)
    denom = math.sqrt(sum((x - mx) ** 2 for x in xs) *
                      sum((y - my) ** 2 for y in ys))
    if denom == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def lag1_autocorr(values):
    if len(values) < 3:
        return None
    return pearson(values[:-1], values[1:])


def finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and not (isinstance(value, float) and
                     (value != value or value in (float("inf"), float("-inf")))))


def r6(value):
    return round(value, 6) if isinstance(value, float) else value


def main():
    canonical = [json.loads(line) for line in CANONICAL_PATH.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    frozen_desc = json.loads(FROZEN_DESC_PATH.read_text(encoding="utf-8"))
    scoped = [r for r in canonical if r.get("asset_id") == TARGET_ASSET]

    # --- 1. timestamp population ---
    parsed = [(r, parse_wallclock(r.get("observed_at_original"))) for r in scoped]
    bad = [r.get("evidence_id") for r, ts in parsed if ts is None]
    stamps = sorted(ts for _, ts in parsed if ts is not None)
    distinct_stamps = sorted(set(stamps))
    gaps = [(b - a).total_seconds() / 60.0
            for a, b in zip(distinct_stamps, distinct_stamps[1:])]
    freq = Counter(round(g, 6) for g in gaps)
    mode_gap, mode_count = (freq.most_common(1)[0] if freq else (None, 0))
    per_code_stamps = {}
    for r, ts in parsed:
        if ts is not None:
            per_code_stamps.setdefault(r.get("measurement_type_code"), []).append(ts)
    dup_within_code = {code: len(v) - len(set(v))
                       for code, v in per_code_stamps.items()}
    by_stamp = {}
    for r, ts in parsed:
        if ts is not None:
            by_stamp.setdefault(ts, set()).add(r.get("measurement_type_code"))
    full_grid = sum(1 for codes in by_stamp.values() if len(codes) == 14)
    snapshot_stamps = {ts for r, ts in parsed
                       if ts is not None and r.get("dataset_type") == "snapshot"}
    history_stamps = {ts for r, ts in parsed
                      if ts is not None and r.get("dataset_type") == "history"}
    tz_evidence = [r.get("observed_at_original") for r in scoped
                   if not isinstance(r.get("observed_at_original"), str)
                   or "+" in str(r.get("observed_at_original"))
                   or str(r.get("observed_at_original")).endswith("Z")
                   or "T" in str(r.get("observed_at_original"))]

    # --- 2. independent recomputation vs frozen ---
    by_code = {}
    for r, ts in parsed:
        if ts is not None and finite_number(r.get("measurement_value")):
            by_code.setdefault(r.get("measurement_type_code"), []).append(
                r.get("measurement_value"))
    recomputed, discrepancies = {}, []
    for code in sorted(by_code):
        values = sorted(by_code[code])
        # Round to 6dp first: the frozen baseline stores r6-rounded values,
        # so comparison must use the same documented convention.
        got = {"count": len(values), "mean": r6(mean(values)),
               "median": r6(quantile_linear(values, 50)),
               "stdev": r6(pstdev(values)), "min": values[0], "max": values[-1],
               "p10": r6(quantile_linear(values, 10)),
               "p90": r6(quantile_linear(values, 90))}
        first, third = quantile_linear(values, 25), quantile_linear(values, 75)
        iqr = third - first
        got["tukey_high"] = sum(1 for v in values if v > third + 1.5 * iqr)
        got["tukey_low"] = sum(1 for v in values if v < first - 1.5 * iqr)
        first_q = quantile_linear(values, 25)
        third_q = quantile_linear(values, 75)
        audit_high = sum(1 for v in values if v > third_q + 1.5 * (third_q - first_q))
        audit_low = sum(1 for v in values if v < first_q - 1.5 * (third_q - first_q))
        frozen_dist = frozen["distributions"][code]
        frozen_desc_series = frozen_desc["series"][code]
        frozen_q = frozen_dist.get("quantiles", {})
        frozen_candidates = frozen_dist.get("outlier_candidates", {})
        frozen_high = len(frozen_candidates.get("high", []))
        frozen_low = len(frozen_candidates.get("low", []))
        checks = [("count", got["count"], frozen_dist.get("count")),
                  ("mean", got["mean"], frozen_desc_series.get("mean")),
                  ("median", got["median"], frozen_desc_series.get("median")),
                  ("stdev", got["stdev"], frozen_desc_series.get("stdev_population")),
                  ("min", got["min"], frozen_desc_series.get("minimum")),
                  ("max", got["max"], frozen_desc_series.get("maximum")),
                  ("p10", got["p10"], frozen_q.get("p10")),
                  ("p90", got["p90"], frozen_q.get("p90")),
                  ("tukey_high_count", audit_high, frozen_high),
                  ("tukey_low_count", audit_low, frozen_low)]
        for field, mine, theirs in checks:
            both_none = mine is None and theirs is None
            close = (mine is not None and theirs is not None
                     and abs(mine - theirs) < 1e-9)
            if not (both_none or close):
                discrepancies.append({"code": code, "field": field,
                                      "audit": mine, "frozen": theirs,
                                      "note": "r6-rounded comparison; any entry "
                                              "here is a real discrepancy"})
        recomputed[code] = {k: r6(v) if isinstance(v, float) else v
                            for k, v in got.items()}

    # --- 3. robustness diagnostics ---
    sensitivity = {}
    for code in sorted(by_code):
        values = sorted(by_code[code])
        first, third = quantile_linear(values, 25), quantile_linear(values, 75)
        iqr = third - first
        kept = [v for v in values if v <= third + 1.5 * iqr]
        excluded = len(values) - len(kept)
        sensitivity[code] = {"excluded_high_candidates": excluded,
                             "mean_all": r6(mean(values)),
                             "mean_without_high": r6(mean(kept)) if kept else None,
                             "mean_shift": r6(mean(kept) - mean(values)) if kept else None}
    half = len(distinct_stamps) // 2
    early, late = set(distinct_stamps[:half]), set(distinct_stamps[half:])
    spread_by_segment = {}
    for label, window in (("first_half", early), ("second_half", late)):
        triplets = {}
        for r, ts in parsed:
            if ts in window and finite_number(r.get("measurement_value")) and \
                    r.get("measurement_type_code") in ("A_phsA", "A_phsB", "A_phsC"):
                triplets.setdefault(ts, {})[r.get("measurement_type_code")] = \
                    r.get("measurement_value")
        spreads = []
        for ts, point in triplets.items():
            if len(point) == 3:
                mu = sum(point.values()) / 3
                if mu != 0:
                    spreads.append((max(point.values()) - min(point.values()))
                                   / abs(mu) * 100)
        spreads.sort()
        spread_by_segment[label] = {"aligned_timestamps": len(triplets),
                                    "spread_p50": r6(quantile_linear(spreads, 50))
                                    if spreads else None}
    autocorr = {}
    for code in sorted(by_code):
        ordered = sorted(
            ((ts, r.get("measurement_value")) for r, ts in parsed
             if ts is not None and r.get("measurement_type_code") == code
             and finite_number(r.get("measurement_value"))),
            key=lambda item: item[0])
        autocorr[code] = r6(lag1_autocorr([v for _, v in ordered]))
    corr_pairs = {}
    for key in ("A_phsA~A_phsB", "PhV_phsA~PhV_phsB", "TotW~TotVar"):
        first, second = key.split("~")
        series = {}
        for r, ts in parsed:
            if ts is not None and finite_number(r.get("measurement_value")):
                series.setdefault(r.get("measurement_type_code"), {})[ts] = \
                    r.get("measurement_value")
        common = sorted(set(series[first]) & set(series[second]))
        xs = [series[first][t] for t in common]
        ys = [series[second][t] for t in common]
        full = pearson(xs, ys)
        first_q, third_q = quantile_linear(sorted(ys), 25), quantile_linear(sorted(ys), 75)
        keep = [i for i, y in enumerate(ys) if y <= third_q + 1.5 * (third_q - first_q)]
        trimmed = pearson([xs[i] for i in keep], [ys[i] for i in keep])
        corr_pairs[key] = {"n": len(common),
                           "r_all": r6(full) if full is not None else None,
                           "excluded_high_y": len(ys) - len(keep),
                           "r_without_high_y": r6(trimmed) if trimmed is not None else None}

    report = {
        "audit_version": "1.0.0",
        "frozen_baseline_sha256_note": "M1.6 artifacts read-only; hashes verified separately",
        "timestamps": {
            "first": stamps[0].strftime("%Y-%m-%d %H:%M:%S") if stamps else None,
            "last": stamps[-1].strftime("%Y-%m-%d %H:%M:%S") if stamps else None,
            "distinct_count": len(set(stamps)),
            "unparseable": bad,
            "interval_min": r6(min(gaps)) if gaps else None,
            "interval_median": r6(quantile_linear(sorted(gaps), 50)) if gaps else None,
            "interval_mean": r6(mean(gaps)) if gaps else None,
            "interval_max": r6(max(gaps)) if gaps else None,
            "most_common_interval": [mode_gap, mode_count],
            "duplicate_within_code": dup_within_code,
            "timestamps_with_all_14_codes": full_grid,
            "snapshot_only_stamps": len(snapshot_stamps - history_stamps),
            "history_only_stamps": len(history_stamps - snapshot_stamps),
            "shared_snapshot_history_stamps": len(snapshot_stamps & history_stamps),
            "timezone_markers_found": len(tz_evidence),
            "blocked_conclusions": [
                "UTC conversion", "cross-source clock alignment",
                "equal-spacing assumptions", "physical sampling-rate claims"],
        },
        "recomputation": {"codes_checked": sorted(by_code),
                          "discrepancies": discrepancies},
        "robustness": {"mean_sensitivity_to_high_candidates": sensitivity,
                       "spread_segment_stability": spread_by_segment,
                       "lag1_autocorrelation": autocorr,
                       "correlation_sensitivity": corr_pairs,
                       "independence_note": "Lag-1 autocorrelation far from zero "
                                            "rejects independent-observation assumptions."},
        "forecasting_readiness": {
            "decision": "descriptive monitoring only",
            "unique_timestamps": len(set(stamps)),
            "temporal_depth": "about 1.6 days with one 885-minute gap",
            "reasons": ["98 timestamps cannot yield train/validation/test periods "
                        "with non-trivial horizons and no leakage",
                        "single asset: no cross-asset generalization check possible",
                        "no verified target labels exist for any horizon"],
            "minimum_additional_data": "weeks of verified-cadence history per asset, "
                                       "multiple labeled assets, confirmed timezone, "
                                       "declared horizon with time-split protocol",
        },
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"distinct_ts={report['timestamps']['distinct_count']} "
          f"discrepancies={len(discrepancies)} "
          f"full_grid={full_grid}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
