"""Deterministic descriptive baseline for the target transformer (M1.6, local-only).

Source (declared): data/output/canonical_measurements.ndjson — the canonical
store, NOT the application's legacy-shaped serving path (BigQuery rows /
live-flattened source JSON used by app/main.py).

Scope: asset_id == TARGET_ASSET, cross-checked so every scoped record maps to
equipment_type_code '0408001' in the legacy BigQuery-shape rows. Unrelated
equipment is never pooled.

Aggregation/dedup rules (documented):
- Series key: (asset_id, measurement_type_code); points ordered by parsed
  observed_at_original (wall-clock strings; no timezone assumed).
- Snapshot/history overlap: rows sharing (point, code, timestamp) with
  identical values are counted ONCE (first occurrence) and reported under
  overlap_consistent; rows sharing the key with DIFFERING values are excluded
  from statistics and reported under overlap_conflicts.
- No imputation, no unit conversion (units stay unknown), no thresholds.
- OLS slope is over integer sequence index and reported as
  sequence_trend_slope_per_step: a descriptive sequence trend, not a physical
  rate of change.

Output data/output/descriptive_analysis.json is byte-deterministic: no
wall-clock timestamps in the report body (see generation metadata note).
"""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.features import trend_slope_per_step  # noqa: E402
from scripts.audit_measurements import parse_ts, series_audit  # noqa: E402

SCRIPT_VERSION = "1.0.0"
TARGET_ASSET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
TARGET_EQUIPMENT = "0408001"
CANONICAL_PATH = ROOT / "data/output/canonical_measurements.ndjson"
LEGACY_PATH = ROOT / "data/output/telemetry_observations.ndjson"
OUT_PATH = ROOT / "data/output/descriptive_analysis.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def r6(value):
    return round(value, 6) if isinstance(value, float) else value


def deduplicate(records):
    """Split records into unique observations, consistent overlaps, conflicts."""
    groups = defaultdict(list)
    for record in records:
        groups[(record.get("measurement_point_id"),
                record.get("measurement_type_code"),
                record.get("observed_at_original"))].append(record)
    unique, consistent, conflicts = [], [], []
    for members in groups.values():
        if len(members) == 1:
            unique.append(members[0])
        elif len({m.get("measurement_value") for m in members}) == 1:
            unique.append(sorted(members, key=lambda m: m.get("dataset_type") or "")[0])
            consistent.append(members[0].get("evidence_id"))
        else:
            conflicts.append([m.get("evidence_id") for m in members])
    return unique, consistent, conflicts


def summarize_series(records):
    """Descriptive summary for one time-ordered series. Records must be unique."""
    ordered = sorted(
        (r for r in records if parse_ts(r.get("observed_at_original")) is not None),
        key=lambda r: r.get("observed_at_original"))
    malformed = [r.get("evidence_id") for r in records
                 if parse_ts(r.get("observed_at_original")) is None]
    values = [r.get("measurement_value") for r in ordered]
    numerics = [v for v in values
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
    non_finite = [v for v in numerics
                  if isinstance(v, float)
                  and (v != v or v in (float("inf"), float("-inf")))]
    finite = [v for v in numerics if v not in non_finite]
    zeros = sum(1 for v in finite if v == 0)
    sampling = series_audit([parse_ts(r.get("observed_at_original")) for r in ordered])
    return {
        "observation_count": len(ordered),
        "unique_timestamps": len({r.get("observed_at_original") for r in ordered}),
        "numeric_count": len(finite),
        "missing_or_non_numeric": len(ordered) - len(numerics),
        "non_finite_count": len(non_finite),
        "malformed_timestamp_evidence_ids": malformed,
        "minimum": r6(min(finite)) if finite else None,
        "maximum": r6(max(finite)) if finite else None,
        "mean": r6(statistics.fmean(finite)) if finite else None,
        "median": r6(statistics.median(finite)) if finite else None,
        "stdev_population": r6(statistics.pstdev(finite)) if len(finite) >= 2 else None,
        "zero_count": zeros,
        "zero_proportion": r6(zeros / len(finite)) if finite else None,
        "sequence_trend_slope_per_step": r6(trend_slope_per_step(
            [r.get("measurement_value") for r in ordered])),
        "sampling": sampling,
        "unit_status": "unconfirmed",
        "timezone_status": "unconfirmed",
    }


def analyze(canonical_records, legacy_rows):
    legacy_index = defaultdict(set)
    for row in legacy_rows:
        legacy_index[(row.get("source_file_id"), row.get("source_record_index"),
                      row.get("measurement_point_id"),
                      row.get("measurement_type_code"))].add(row.get("equipment_type_code"))
    scoped = [r for r in canonical_records if r.get("asset_id") == TARGET_ASSET]

    def mapped_equipment(record):
        ref = record.get("source_ref", {})
        key = (ref.get("source_file_id"), ref.get("source_record_index"),
               record.get("measurement_point_id"),
               record.get("measurement_type_code"))
        return sorted(legacy_index.get(key, set()))

    foreign = sorted({json.dumps(mapped_equipment(record), ensure_ascii=True)
                      for record in scoped
                      if mapped_equipment(record) != [TARGET_EQUIPMENT]})
    foreign = [json.loads(item) for item in foreign]
    unique, consistent, conflicts = deduplicate(scoped)
    by_code = defaultdict(list)
    for record in unique:
        by_code[record.get("measurement_type_code")].append(record)
    return {
        "analysis_version": SCRIPT_VERSION,
        "source": {"path": "data/output/canonical_measurements.ndjson",
                   "sha256": sha256_file(CANONICAL_PATH),
                   "record_count": len(canonical_records)},
        "scope": {"equipment_type_code": TARGET_EQUIPMENT, "asset_id": TARGET_ASSET,
                  "scoped_records": len(scoped),
                  "non_target_equipment_mapped": foreign},
        "series": {code: summarize_series(by_code[code]) for code in sorted(by_code)},
        "overlap_consistent_evidence_ids": sorted(consistent),
        "overlap_conflicts": conflicts,
        "verification_status": {"units": "unconfirmed", "timezone": "unconfirmed",
                                "sensor_semantics": "unverified",
                                "construction_oil_immersed": "unconfirmed"},
        "limitations": [
            "Single asset, ~1.6-day span; descriptive numbers only.",
            "Units, timezone, and sensor semantics unverified; slopes are sequence trends, not physical rates.",
            "Snapshot/history overlaps deduplicated; conflicts (none observed) would be excluded from statistics.",
        ],
        "prohibited_interpretations": [
            "Trends are not degradation; unusual values are not faults; no score is a failure probability.",
        ],
    }


def main():
    canonical = [json.loads(line) for line in CANONICAL_PATH.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    legacy = [json.loads(line) for line in LEGACY_PATH.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    report = analyze(canonical, legacy)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"series={len(report['series'])} scoped={report['scope']['scoped_records']} "
          f"overlaps_consistent={len(report['overlap_consistent_evidence_ids'])} "
          f"conflicts={len(report['overlap_conflicts'])} foreign={report['scope']['non_target_equipment_mapped']}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
