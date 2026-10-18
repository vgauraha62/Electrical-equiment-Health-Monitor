"""Reproducible, idempotent ingestion for transformer telemetry (M1, local-only).

Reads data/input/telemetry/*.json, writes:
- data/output/canonical_measurements.ndjson (canonical v1 measurement records)
- data/output/telemetry_observations.ndjson (legacy BigQuery-compatible rows)
- data/output/manifest.json (source checksums, versions)
- data/output/dq_report.json (accepted/rejected/corrected counts, duplicates)
- data/output/quarantine/invalid_records.json (invalid rows with reasons)

Idempotent: same inputs -> identical record outputs (sorted, no timestamps
in record bodies). Never assigns timezone or units.

Optional source metadata (astId, astType, consNo, consType, pt, ct, tFactor)
is preserved verbatim under source_ref.source_extra: keys present in the
source record are carried with their raw values (null stays null); keys
absent from the source record are omitted, and records from files without
these keys carry source_extra=null. No coercion, no interpretation.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_DIR = ROOT / "data/input/telemetry"
OUT_DIR = ROOT / "data/output"
QUARANTINE_DIR = OUT_DIR / "quarantine"
SCHEMA_VERSION = "1.0.0"

sys.path.insert(0, str(ROOT))
from app.provenance import sha256_file  # noqa: E402
from app.units import normalize  # noqa: E402


def evidence_id(row):
    material = "|".join(str(row.get(k, "")) for k in (
        "source_file_id", "source_record_index", "measurement_point_id",
        "measurement_type_code", "observed_at", "dataset_type",
    ))
    return "ev_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def valid_timestamp(value):
    """Accept 'YYYY-MM-DD HH:MM:SS' with year 1990–2100; no tz assignment."""
    if not value or not isinstance(value, str):
        return False
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return 1990 <= parsed.year <= 2100


# Optional source-record keys carried verbatim (never interpreted).
EXTRA_KEYS = ("astId", "astType", "consNo", "consType", "pt", "ct", "tFactor")


def build_source_extra(source_record):
    """Preserve present optional keys with raw values; absent file-level keys -> None.

    Returns None when the source record carries none of EXTRA_KEYS (key absent),
    otherwise a dict with exactly the present keys (null values stay null).
    Values are never coerced or defaulted.
    """
    present = {key: source_record.get(key) for key in EXTRA_KEYS if key in source_record}
    return present if present else None


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": SCHEMA_VERSION, "sources": []}
    canonical_rows, legacy_rows, quarantine = [], [], []
    seen_ids, duplicate_count = set(), 0
    corrected = 0

    for path in sorted(IN_DIR.glob("*.json")):
        checksum = sha256_file(path)
        manifest["sources"].append({"file": path.name, "sha256": checksum})
        payload = json.loads(path.read_text(encoding="utf-8"))
        dataset_type = "history" if "history" in path.stem else "snapshot"
        records = payload.get("data", {}).get("result", {}).get("records", [])
        for rec_idx, record in enumerate(records):
            for point in record.get("telemetryValue", []):
                raw_value = point.get("measValue")
                observed_at = point.get("dataTime") or ""
                try:
                    numeric = float(raw_value) if raw_value is not None else None
                except (TypeError, ValueError):
                    numeric = None
                base = {
                    "source_file_id": path.name,
                    "source_record_index": rec_idx,
                    "measurement_point_id": record.get("measPointId") or "unknown-point",
                    "measurement_type_code": record.get("measTypeCode") or "unknown",
                    "observed_at": observed_at,
                    "dataset_type": dataset_type,
                }
                eid = evidence_id({**base,
                                   "measurement_point_id": base["measurement_point_id"]})
                # Validation
                reasons = []
                if not valid_timestamp(observed_at):
                    reasons.append(f"invalid timestamp: {observed_at!r}")
                if raw_value is not None and numeric is None:
                    reasons.append(f"non-numeric measValue: {raw_value!r}")
                if eid in seen_ids:
                    duplicate_count += 1
                    reasons.append("duplicate record (same evidence_id)")
                if reasons:
                    quarantine.append({"evidence_id": eid, "reasons": reasons,
                                       "raw": {"source_file_id": path.name,
                                               "source_record_index": rec_idx,
                                               "measValue": raw_value,
                                               "dataTime": observed_at,
                                               "measTypeCode": base["measurement_type_code"]}})
                    continue
                seen_ids.add(eid)
                norm_value, norm_unit, unit_status = normalize(
                    numeric, None, base["measurement_type_code"])
                canonical = {
                    "asset_id": record.get("psrId") or "unknown-asset",
                    "measurement_point_id": base["measurement_point_id"],
                    "measurement_type_code": base["measurement_type_code"],
                    "measurement_name": record.get("measTypeName") or "Unknown measurement",
                    "measurement_value": numeric,
                    "measurement_value_status": "present" if numeric is not None else "missing",
                    "original_unit": None,
                    "normalized_unit": norm_unit,
                    "unit_status": unit_status,
                    "observed_at_original": observed_at,
                    "observed_at_timezone": None,
                    "timezone_status": "unconfirmed",
                    "sampling_method": None,
                    "dataset_type": dataset_type,
                    "evidence_id": eid,
                    "quality_status": "valid" if numeric is not None else "missing",
                    "quality_flags": "unit not confirmed; timezone unconfirmed",
                    "validation_reason": None,
                    "data_origin": "observed",
                    "schema_version": SCHEMA_VERSION,
                    "source_ref": {"source_file_id": path.name,
                                   "source_record_index": rec_idx,
                                   "source_uri": path.name,
                                   "checksum_sha256": checksum,
                                   "extraction_method": "telemetry_json_flatten_v1",
                                   "source_extra": build_source_extra(record)},
                }
                canonical_rows.append(canonical)
                legacy_rows.append({
                    "source_file_id": path.name,
                    "source_record_index": rec_idx,
                    "psr_id": canonical["asset_id"],
                    "measurement_point_id": canonical["measurement_point_id"],
                    "equipment_type_code": record.get("psrType") or "unknown",
                    "measurement_type_code": canonical["measurement_type_code"],
                    "measurement_name": canonical["measurement_name"],
                    "measurement_value": numeric,
                    "measurement_unit": None,
                    "observed_at": observed_at,
                    "dataset_type": dataset_type,
                    "data_origin": "observed",
                    "source_uri": path.name,
                    "quality_flags": "unit not confirmed",
                })

    canonical_rows.sort(key=lambda r: r["evidence_id"])
    legacy_rows.sort(key=lambda r: (r["source_file_id"], r["source_record_index"],
                                    r["measurement_point_id"], r["observed_at"]))
    (OUT_DIR / "canonical_measurements.ndjson").write_text(
        "\n".join(json.dumps(r, ensure_ascii=True, sort_keys=True) for r in canonical_rows) + ("\n" if canonical_rows else ""),
        encoding="utf-8")
    (OUT_DIR / "telemetry_observations.ndjson").write_text(
        "\n".join(json.dumps(r, ensure_ascii=True, sort_keys=True) for r in legacy_rows) + ("\n" if legacy_rows else ""),
        encoding="utf-8")
    (QUARANTINE_DIR / "invalid_records.json").write_text(
        json.dumps(quarantine, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    manifest["record_counts"] = {"accepted": len(canonical_rows),
                                 "quarantined": len(quarantine),
                                 "duplicates": duplicate_count,
                                 "corrected": corrected}
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    dq = {"accepted": len(canonical_rows), "rejected": len(quarantine),
          "corrected": corrected, "duplicate_count": duplicate_count,
          "notes": ["units unconfirmed; timezone unconfirmed; no failure labels present"]}
    (OUT_DIR / "dq_report.json").write_text(
        json.dumps(dq, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    print(f"accepted={len(canonical_rows)} quarantined={len(quarantine)} duplicates={duplicate_count}")


if __name__ == "__main__":
    main()
