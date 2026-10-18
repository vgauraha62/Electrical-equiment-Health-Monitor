"""Reproducible descriptive statistical baseline (stdlib only).

Population: canonical target-asset records (equipment 0408001, target asset),
deduplicated per documented overlap rules. Non-target equipment is excluded by
scope (counted, not pooled); malformed timestamps, conflicting overlaps, and
non-finite values are excluded from statistics with reasons recorded.

New statistics beyond the frozen M1.6 baseline (left untouched): quantiles
(linear interpolation, same percentile() as the audit), Tukey-fence outlier
CANDIDATES (k=1.5, documented parameters, never faults), timestamp-aligned
phase-family comparisons with the explicit spread formula, and Pearson
correlations on aligned series with sample sizes documented.

The source ×25 fill rule and calculation-sheet formulas are NOT applied
(applicability unestablished). No units invented; raw-value statistics only.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.features import phase_current_spread_pct  # noqa: E402
from scripts.analyze_measurements import (TARGET_ASSET, TARGET_EQUIPMENT,  # noqa: E402
                                          deduplicate, r6, sha256_file)
from scripts.audit_measurements import parse_ts, percentile  # noqa: E402

SCRIPT_VERSION = "1.0.0"
TUKEY_K = 1.5
CANONICAL_PATH = ROOT / "data/output/canonical_measurements.ndjson"
LEGACY_PATH = ROOT / "data/output/telemetry_observations.ndjson"
OUT_PATH = ROOT / "data/output/statistical_baseline.json"

PHASE_FAMILIES = {
    "current": ("A_phsA", "A_phsB", "A_phsC"),
    "voltage": ("PhV_phsA", "PhV_phsB", "PhV_phsC"),
    "active_power": ("PhW_phsA", "PhW_phsB", "PhW_phsC"),
    "reactive_power": ("PhVAr_phsA", "PhVAr_phsB", "PhVAr_phsC"),
}
CORRELATION_PAIRS = (
    ("TotW", "TotVar"),
    ("A_phsA", "A_phsB"), ("A_phsB", "A_phsC"), ("A_phsA", "A_phsC"),
    ("PhV_phsA", "PhV_phsB"), ("PhW_phsA", "TotW"),
)


def quantiles(finite):
    ordered = sorted(finite)
    return {f"p{p}": (r6(percentile(ordered, p)) if ordered else None)
            for p in (10, 25, 50, 75, 90)}


def tukey_candidates(finite):
    """Tukey fences with k=TUKEY_K. Candidates for review, not faults."""
    ordered = sorted(finite)
    if len(ordered) < 4:
        return {"low": [], "high": [], "note": "n<4: fences not computed"}
    first, third = percentile(ordered, 25), percentile(ordered, 75)
    spread = third - first
    low, high = first - TUKEY_K * spread, third + TUKEY_K * spread
    return {"low": [v for v in ordered if v < low],
            "high": [v for v in ordered if v > high],
            "fence_low": r6(low), "fence_high": r6(high)}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return {"r": None, "n": n, "note": "n<3: not computed"}
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = math.sqrt(sum((x - mean_x) ** 2 for x in xs) *
                      sum((y - mean_y) ** 2 for y in ys))
    if denom == 0:
        return {"r": None, "n": n, "note": "zero variance: not computed"}
    return {"r": r6(sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom),
            "n": n, "note": "descriptive linear association only; not causal"}


def is_finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and not (isinstance(value, float) and (value != value or value in
                                                   (float("inf"), float("-inf")))))


def analyze(canonical_records):
    scoped = [r for r in canonical_records if r.get("asset_id") == TARGET_ASSET]
    excluded_scope = len(canonical_records) - len(scoped)
    unique, consistent, conflicts = deduplicate(scoped)
    conflict_ids = {eid for group in conflicts for eid in group}
    eligible, malformed, non_finite = [], [], []
    for record in unique:
        if parse_ts(record.get("observed_at_original")) is None:
            malformed.append(record.get("evidence_id"))
            continue
        value = record.get("measurement_value")
        if value is not None and not is_finite_number(value):
            non_finite.append(record.get("evidence_id"))
            continue
        eligible.append(record)
    by_code = {}
    for record in eligible:
        by_code.setdefault(record.get("measurement_type_code"), []).append(record)
    distributions = {}
    for code in sorted(by_code):
        finite = [r["measurement_value"] for r in by_code[code]
                  if is_finite_number(r.get("measurement_value"))]
        missing = sum(1 for r in by_code[code]
                      if not is_finite_number(r.get("measurement_value")))
        distributions[code] = {
            "count": len(by_code[code]),
            "missing_or_non_numeric": missing,
            "quantiles": quantiles(finite),
            "tukey_k": TUKEY_K,
            "outlier_candidates": tukey_candidates(finite),
        }
    by_time_code = {}
    for record in eligible:
        value = record.get("measurement_value")
        if is_finite_number(value):
            by_time_code.setdefault(
                record.get("observed_at_original"), {})[record.get(
                    "measurement_type_code")] = value
    aligned_times = sorted(by_time_code)
    phase_comparison = {}
    for family, codes in PHASE_FAMILIES.items():
        spreads, complete = [], 0
        for stamp in aligned_times:
            point = by_time_code[stamp]
            if all(code in point for code in codes):
                complete += 1
                spread = phase_current_spread_pct(
                    point[codes[0]], point[codes[1]], point[codes[2]])
                if spread is not None:
                    spreads.append(spread)
        phase_comparison[family] = {
            "codes": list(codes),
            "aligned_timestamps": complete,
            "spread_formula": "(max-min)/abs(mean)*100 over the three phase values; "
                              "None unless 3 values and mean != 0",
            "spread_quantiles": quantiles(spreads),
            "interpretation": "descriptive spread only; no balance threshold applied",
        }
    correlations = {}
    for first, second in CORRELATION_PAIRS:
        pairs = [(by_time_code[t][first], by_time_code[t][second])
                 for t in aligned_times
                 if first in by_time_code[t] and second in by_time_code[t]]
        result = pearson([p[0] for p in pairs], [p[1] for p in pairs])
        result["aligned_observations"] = len(pairs)
        correlations[f"{first}~{second}"] = result
    return {
        "analysis_version": SCRIPT_VERSION,
        "params": {"quantile_method": "linear interpolation",
                   "tukey_k": TUKEY_K,
                   "correlation": "Pearson on timestamp-aligned finite values",
                   "fill_rule_x25": "NOT applied (applicability unestablished)",
                   "calc_sheet_formulas": "NOT applied (pending review)"},
        "source": {"path": "data/output/canonical_measurements.ndjson",
                   "sha256": sha256_file(CANONICAL_PATH),
                   "record_count": len(canonical_records)},
        "population": {
            "equipment_type_code": TARGET_EQUIPMENT, "asset_id": TARGET_ASSET,
            "scoped_records": len(scoped),
            "excluded_non_target_scope": excluded_scope,
            "excluded_conflicting_overlaps": sorted(conflict_ids),
            "excluded_malformed_timestamps": malformed,
            "excluded_non_finite_values": non_finite,
            "overlap_consistent_deduped": sorted(consistent),
            "eligible_records": len(eligible),
            "independence_note": "Overlapping exports deduplicated; repeated observations "
                                 "of one asset are not independent samples.",
        },
        "distributions": distributions,
        "phase_comparison": phase_comparison,
        "correlations": correlations,
        "verification_status": {"units": "unconfirmed", "timezone": "unconfirmed",
                                "sensor_semantics": "unverified"},
        "prohibited_interpretations": [
            "Patterns are descriptive, not proof of fault or impending failure.",
            "Outlier candidates are review pointers, not fault classifications.",
            "Correlations do not establish causality.",
        ],
    }


def main():
    canonical = [json.loads(line) for line in CANONICAL_PATH.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    report = analyze(canonical)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"eligible={report['population']['eligible_records']} "
          f"codes={len(report['distributions'])} "
          f"correlations={len(report['correlations'])}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
