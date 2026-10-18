"""Equipment directory across ledger and telemetry (descriptive only).

Merges the sanitized ledger catalog with telemetry assets on exact normalized
IDs (surrounding whitespace stripped; case-sensitive). Case-insensitive
matching was checked against the same data and yields the identical set, so
case handling is moot rather than assumed. No fuzzy matching: unmatched IDs
are reported, never equated.

Transformer-specific logic (readiness routing, phase-spread review) runs only
for equipment type 0408001 through the existing evidence path. Other types get
descriptive readings plus an explicit unsupported list.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

TRANSFORMER_TYPE = "0408001"
# Internal behavior gate for non-transformer telemetry: these analyses never
# run outside the transformer workflow. Presented to users as a single
# concise note, not a repeated warning list.
UNSUPPORTED_NON_TRANSFORMER = (
    "readiness_routing",
    "phase_spread_indicator",
    "fault_state_assessment",
    "failure_probability",
)
NON_TRANSFORMER_NOTE = ("Readings are shown descriptively; no reviewed assessment "
                        "is configured for this equipment type.")
LEDGER_ONLY_NOTE = "No telemetry readings are available for this equipment."
UNIT_TIME_CAVEAT = ("Raw values only. Measurement units/scaling and timestamp "
                    "timezone are unconfirmed; no engineering meaning is inferred.")
_catalog_cache = {"path": None, "catalog": None}


def normalize_id(value):
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def load_catalog(root):
    path = Path(root) / "data/output/equipment_catalog.json"
    cached = _catalog_cache
    if cached["catalog"] is None or cached["path"] != str(path):
        if not path.is_file():
            return None
        cached.update(path=str(path),
                      catalog=json.loads(path.read_text(encoding="utf-8")))
    return cached["catalog"]


def telemetry_assets(rows):
    """Group any telemetry rows by asset across all equipment types."""
    grouped = {}
    for row in rows:
        asset_id = normalize_id(row.get("psr_id"))
        if asset_id is None:
            continue
        entry = grouped.setdefault(asset_id, {
            "asset_id": asset_id, "equipment_type_codes": set(),
            "observation_count": 0, "latest_observation": "",
            "measurement_codes": set(), "source_files": set()})
        entry["equipment_type_codes"].add(row.get("equipment_type_code"))
        entry["observation_count"] += 1
        observed = row.get("observed_at") or ""
        if observed > entry["latest_observation"]:
            entry["latest_observation"] = observed
        entry["measurement_codes"].add(row.get("measurement_type_code"))
        entry["source_files"].add(row.get("source_file_id"))
    for entry in grouped.values():
        entry["equipment_type_codes"] = sorted(entry["equipment_type_codes"])
        entry["measurement_codes"] = sorted(entry["measurement_codes"])
        entry["source_files"] = sorted(entry["source_files"])
    return grouped


def directory(catalog_records, telemetry):
    """Merge on exact normalized IDs. Returns (items, counts)."""
    ledger_index = {}
    for record in catalog_records:
        key = normalize_id(record.get("psr_id"))
        if key is not None:
            ledger_index[key] = record
    tele_keys = set(telemetry)
    ledger_keys = set(ledger_index)
    matched = tele_keys & ledger_keys
    items = []
    for key in sorted(tele_keys | ledger_keys):
        in_ledger = key in ledger_index
        in_telemetry = key in telemetry
        if in_ledger and in_telemetry:
            coverage = "both"
        elif in_ledger:
            coverage = "ledger only"
        else:
            coverage = "telemetry only"
        record = ledger_index.get(key, {})
        tele = telemetry.get(key, {})
        items.append({
            "asset_id": key,
            "coverage": coverage,
            "ast_type": record.get("ast_type"),
            "equipment_type_codes": tele.get("equipment_type_codes", []),
            "observation_count": tele.get("observation_count", 0),
            "latest_observation": tele.get("latest_observation", ""),
        })
    counts = {"ledger_records": len(ledger_keys),
              "telemetry_ids": len(tele_keys),
              "exact_matches": len(matched),
              "unmatched_telemetry_ids": sorted(tele_keys - ledger_keys)}
    return items, counts


def describe_telemetry(rows):
    """Descriptive per-code bundle with chartable series. Raw values only.

    Each code entry carries the full observed series (``points``) plus a
    descriptive summary. The summary is computed over the finite numeric
    values only and uses the same definitions as the frozen M1.6 baseline
    (``scripts/analyze_measurements.py``): ``minimum``/``maximum``/``mean``
    over the finite subset; ``stdev`` is the POPULATION standard deviation
    (``statistics.pstdev``); ``zero_count`` counts finite values exactly
    equal to 0; ``zero_proportion`` is ``zero_count / len(finite)``. Floats
    are rounded to 6 decimals; a statistic is ``null`` when it is undefined
    for the available values (no finite values, or fewer than 2 for stdev).
    No thresholds, units, or engineering meaning are applied.
    """
    by_code = {}
    for row in rows:
        code = row.get("measurement_type_code") or "unknown"
        entry = by_code.setdefault(code, {"observation_count": 0,
                                          "minimum": None,
                                          "maximum": None,
                                          "mean": None,
                                          "stdev": None,
                                          "zero_count": 0,
                                          "zero_proportion": None,
                                          "latest_value": None,
                                          "latest_observation": "",
                                          "measurement_name": row.get("measurement_name"),
                                          "points": []})
        entry["observation_count"] += 1
        observed = row.get("observed_at") or ""
        value = row.get("measurement_value")
        entry["points"].append([observed, value])
        if observed >= entry["latest_observation"]:
            entry["latest_observation"] = observed
            entry["latest_value"] = value
            entry["measurement_name"] = row.get("measurement_name")
    for entry in by_code.values():
        entry["points"].sort(key=lambda point: point[0])
        entry.update(_describe_values([point[1] for point in entry["points"]]))
    return {code: by_code[code] for code in sorted(by_code)}


def _r6(value):
    return round(value, 6) if isinstance(value, float) else value


def _describe_values(values):
    """Descriptive statistics over finite numeric values.

    Mirrors the frozen M1.6 baseline exactly (see ``describe_telemetry``).
    """
    finite = [v for v in values
              if isinstance(v, (int, float)) and not isinstance(v, bool)
              and math.isfinite(v)]
    zeros = sum(1 for v in finite if v == 0)
    return {
        "minimum": _r6(min(finite)) if finite else None,
        "maximum": _r6(max(finite)) if finite else None,
        "mean": _r6(statistics.fmean(finite)) if finite else None,
        "stdev": _r6(statistics.pstdev(finite)) if len(finite) >= 2 else None,
        "zero_count": zeros,
        "zero_proportion": _r6(zeros / len(finite)) if finite else None,
    }
