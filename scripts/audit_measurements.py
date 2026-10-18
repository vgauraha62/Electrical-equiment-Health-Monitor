"""Measurement data qualification audit (M1.4, local-only, stdlib only).

Reads data/output/canonical_measurements.ndjson, writes
data/output/measurement_audit.json, and prints a short summary
(counts only, never the full dataset).

No engineering thresholds, no unit inference, no imputation.
Interval/gap figures are descriptive (percentiles, maxima) — a gap noted
as a multiple of the series median is a descriptive marker, not an
engineering limit.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_ts(value):
    """Parse a canonical observed_at_original string; None if unparseable.

    The format carries no timezone designator; nothing is assumed about
    timezone — the caller must treat results as wall-clock strings.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value.strip(), TS_FORMAT)
    except ValueError:
        return None
    if not 1990 <= parsed.year <= 2100:
        return None
    return parsed


def intervals_minutes(sorted_datetimes):
    """Successive differences in minutes for a time-ordered list."""
    return [(b - a).total_seconds() / 60.0
            for a, b in zip(sorted_datetimes, sorted_datetimes[1:])]


def percentile(sorted_values, pct):
    if not sorted_values:
        return None
    rank = (len(sorted_values) - 1) * pct / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[int(rank)]
    frac = rank - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def series_audit(timestamps):
    """Descriptive sampling audit for one asset+code timestamp list."""
    ordered = sorted(timestamps)
    gaps = intervals_minutes(ordered)
    sorted_gaps = sorted(gaps)
    median = percentile(sorted_gaps, 50) if sorted_gaps else None
    return {
        "observation_count": len(ordered),
        "interval_count": len(gaps),
        "interval_min": min(gaps) if gaps else None,
        "interval_p50": median,
        "interval_p90": percentile(sorted_gaps, 90) if sorted_gaps else None,
        "interval_max": max(gaps) if gaps else None,
        "intervals_over_60min": sum(1 for g in gaps if g > 60),
        "intervals_over_3x_median": (
            sum(1 for g in gaps if median and g > 3 * median) if median else 0),
    }


def missing_by_field(records):
    counts = Counter()
    for record in records:
        for key, value in record.items():
            if value is None:
                counts[key] += 1
    return dict(sorted(counts.items()))


def distinct_counts(records, fields):
    result = {}
    for field in fields:
        values = {json.dumps(record.get(field), sort_keys=True, ensure_ascii=True)
                  for record in records}
        result[field] = len(values)
    return result


def audit(records):
    summary = {"record_count": len(records)}
    summary["unique_assets"] = len({r.get("asset_id") for r in records})
    summary["records_per_asset"] = dict(sorted(
        Counter(r.get("asset_id") for r in records).items()))
    parsed = [(r, parse_ts(r.get("observed_at_original"))) for r in records]
    summary["unparseable_timestamps"] = sum(1 for _, ts in parsed if ts is None)
    good = sorted(ts for _, ts in parsed if ts is not None)
    summary["unique_timestamps"] = len(set(good))
    summary["min_timestamp"] = good[0].strftime(TS_FORMAT) if good else None
    summary["max_timestamp"] = good[-1].strftime(TS_FORMAT) if good else None
    summary["span_days"] = round((good[-1] - good[0]).total_seconds() / 86400, 2) if good else None

    evidence_ids = [r.get("evidence_id") for r in records]
    summary["duplicate_evidence_ids"] = len(evidence_ids) - len(set(evidence_ids))
    combos = [(r.get("asset_id"), r.get("measurement_point_id"),
               r.get("measurement_type_code"), r.get("observed_at_original"))
              for r in records]
    summary["duplicate_asset_point_code_time"] = len(combos) - len(set(combos))

    summary["missing_by_field"] = missing_by_field(records)
    summary["distinct_counts"] = distinct_counts(records, [
        "data_origin", "dataset_type", "unit_status", "timezone_status",
        "quality_status", "measurement_value_status", "schema_version",
        "quality_flags", "original_unit", "normalized_unit",
        "measurement_type_code", "measurement_name"])
    summary["quality_flags_freq"] = dict(sorted(
        Counter(r.get("quality_flags") for r in records).items()))

    values = [r.get("measurement_value") for r in records]
    numerics = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    summary["non_numeric_or_missing_values"] = len(records) - len(numerics)
    summary["non_finite_values"] = sum(
        1 for v in numerics if isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    per_code = {}
    by_code = {}
    for r in records:
        by_code.setdefault(r.get("measurement_type_code"), []).append(r)
    for code in sorted(by_code):
        series = [r.get("measurement_value") for r in by_code[code]
                  if isinstance(r.get("measurement_value"), (int, float))
                  and not isinstance(r.get("measurement_value"), bool)]
        per_code[code] = {
            "count": len(by_code[code]),
            "numeric_count": len(series),
            "minimum": min(series) if series else None,
            "maximum": max(series) if series else None,
            "assets": sorted({r.get("asset_id") for r in by_code[code]}),
            "dataset_types": sorted({r.get("dataset_type") for r in by_code[code]}),
        }
    summary["per_code"] = per_code

    per_series = {}
    grouped = {}
    for r in records:
        ts = parse_ts(r.get("observed_at_original"))
        if ts is None:
            continue
        grouped.setdefault((r.get("asset_id"), r.get("measurement_type_code")), []).append(ts)
    for (asset, code), stamps in sorted(grouped.items()):
        per_series[f"{asset}||{code}"] = series_audit(stamps)
    summary["per_series_sampling"] = per_series

    label_keys = [k for r in records for k in r
                  if any(term in k.lower() for term in ("fault", "failure", "label", "defect"))]
    summary["label_like_keys_present"] = sorted(set(label_keys))
    summary["units_verified_anywhere"] = any(
        r.get("unit_status") == "verified" for r in records)
    summary["timezone_verified_anywhere"] = any(
        r.get("timezone_status") == "verified" for r in records)
    return summary


def main():
    in_path = ROOT / "data/output/canonical_measurements.ndjson"
    out_path = ROOT / "data/output/measurement_audit.json"
    records = [json.loads(line) for line in in_path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    summary = audit(records)
    out_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True)
                        + "\n", encoding="utf-8")
    series = summary["per_series_sampling"]
    multi = {k: v for k, v in series.items() if v["observation_count"] > 1}
    medians = sorted(v["interval_p50"] for v in multi.values() if v["interval_p50"] is not None)
    print(f"records={summary['record_count']} assets={summary['unique_assets']} "
          f"timestamps={summary['unique_timestamps']} span_days={summary['span_days']}")
    print(f"series={len(series)} multi_point_series={len(multi)} "
          f"median_interval_p50_range={(min(medians), max(medians)) if medians else None}")
    print(f"duplicates_evidence={summary['duplicate_evidence_ids']} "
          f"duplicates_combo={summary['duplicate_asset_point_code_time']} "
          f"unparseable_ts={summary['unparseable_timestamps']} "
          f"nonfinite={summary['non_finite_values']}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
