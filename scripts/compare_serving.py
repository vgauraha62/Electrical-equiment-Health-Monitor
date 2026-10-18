"""Canonical-vs-serving consistency check (M1.7, local-only, stdlib only).

Compares canonical measurements (data/output/canonical_measurements.ndjson)
against the legacy BigQuery-shape rows served to app/main.py
(data/output/telemetry_observations.ndjson — the documented BQ-load input).

Join key (stable, present on both sides): source_file_id, source_record_index,
measurement_point_id, measurement_type_code, observed timestamp.
Writes data/output/serving_comparison.json; prints counts only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CANONICAL_PATH = ROOT / "data/output/canonical_measurements.ndjson"
LEGACY_PATH = ROOT / "data/output/telemetry_observations.ndjson"
OUT_PATH = ROOT / "data/output/serving_comparison.json"

FIELD_MAP = {  # canonical field -> legacy field
    "measurement_type_code": "measurement_type_code",
    "measurement_name": "measurement_name",
    "measurement_value": "measurement_value",
    "observed_at_original": "observed_at",
    "dataset_type": "dataset_type",
    "data_origin": "data_origin",
}


def join_key_canonical(record):
    ref = record.get("source_ref", {})
    return (ref.get("source_file_id"), ref.get("source_record_index"),
            record.get("measurement_point_id"),
            record.get("measurement_type_code"),
            record.get("observed_at_original"))


def join_key_legacy(row):
    return (row.get("source_file_id"), row.get("source_record_index"),
            row.get("measurement_point_id"),
            row.get("measurement_type_code"), row.get("observed_at"))


def numbers_equal(first, second):
    if first is None or second is None:
        return first is None and second is None
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return first == second


def compare(canonical_records, legacy_rows):
    legacy_index = {}
    for row in legacy_rows:
        legacy_index.setdefault(join_key_legacy(row), []).append(row)
    matched = 0
    unmatched_canonical = []
    disagreements = []
    for record in canonical_records:
        key = join_key_canonical(record)
        candidates = legacy_index.get(key, [])
        if not candidates:
            unmatched_canonical.append(record.get("evidence_id"))
            continue
        if len(candidates) > 1:
            disagreements.append({"key": list(key), "field": "__duplicate_serving_key__",
                                  "detail": f"{len(candidates)} legacy rows share the join key"})
            continue
        row = candidates[0]
        matched += 1
        for canonical_field, legacy_field in FIELD_MAP.items():
            first, second = record.get(canonical_field), row.get(legacy_field)
            equal = numbers_equal(first, second) if "value" in canonical_field \
                else first == second
            if not equal:
                disagreements.append({"key": list(key), "field": canonical_field,
                                      "canonical": first, "serving": second})
        if record.get("asset_id") != row.get("psr_id"):
            disagreements.append({"key": list(key), "field": "asset_id_vs_psr_id",
                                  "canonical": record.get("asset_id"),
                                  "serving": row.get("psr_id")})
    used = {join_key_canonical(r) for r in canonical_records}
    unmatched_serving = [list(key) for key in legacy_index if key not in used]
    metadata_gaps = []
    for record in canonical_records:
        extra = (record.get("source_ref") or {}).get("source_extra") or {}
        populated = sorted(k for k, v in extra.items() if v is not None)
        if populated:
            # Serving rows carry no source_extra: populated values are dropped there.
            metadata_gaps.append({"evidence_id": record.get("evidence_id"),
                                  "populated_keys": populated})
    return {"matched": matched, "unmatched_canonical": unmatched_canonical,
            "unmatched_serving": unmatched_serving, "disagreements": disagreements,
            "metadata_serving_gaps": metadata_gaps,
            "metadata_serving_gap_count": len(metadata_gaps)}


def main():
    canonical = [json.loads(line) for line in CANONICAL_PATH.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    legacy = [json.loads(line) for line in LEGACY_PATH.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    result = compare(canonical, legacy)
    result["canonical_count"] = len(canonical)
    result["serving_count"] = len(legacy)
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"matched={result['matched']} unmatched_canonical={len(result['unmatched_canonical'])} "
          f"unmatched_serving={len(result['unmatched_serving'])} "
          f"disagreements={len(result['disagreements'])} "
          f"metadata_serving_gaps={result['metadata_serving_gap_count']}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
