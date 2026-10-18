"""Deterministic synthetic scenario generator (stdlib only).

Parent: frozen target-asset canonical observations, read-only. Perturbations
are explicitly artificial raw-value arithmetic (units/scaling unconfirmed);
magnitudes are software parameters, never physical claims.

Layout (all under data/output/synthetic/, never touching observed data):
  manifest.json      generator version/seed, parent hash, scenario catalog
  scenarios.ndjson   derived observations (data_origin=synthetic, syn_ IDs)
  truth.json         per-scenario event windows (withheld from detectors)

Deterministic: all randomness flows from random.Random(seed); reruns from the
same inputs produce byte-identical outputs.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GENERATOR_VERSION = "synthetic-scenarios-v1"
TARGET_ASSET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
CANONICAL_PATH = ROOT / "data/output/canonical_measurements.ndjson"
OUT_DIR = ROOT / "data/output/synthetic"

SCENARIOS = (
    {"scenario_id": "SYN-SPIKE-001", "scenario_type": "spike",
     "target_code": "A_phsA", "control_code": "A_phsB",
     "window": [30, 60], "offset": 120.0,
     "expected": "single-point flag on target only; control clean"},
    {"scenario_id": "SYN-SHIFT-001", "scenario_type": "sustained_shift",
     "target_code": "A_phsC", "control_code": "A_phsB",
     "window": [20, 50], "offset": 30.0,
     "expected": "sustained flags across the interval; control clean"},
    {"scenario_id": "SYN-DRIFT-001", "scenario_type": "gradual_drift",
     "target_code": "PhW_phsA", "control_code": "PhW_phsB",
     "window": [10, 70], "slope_per_step": 0.35, "noise": 0.05,
     "expected": "late-interval flags as ramp exceeds baseline; control clean"},
    {"scenario_id": "SYN-MISSING-001", "scenario_type": "missing_data",
     "target_code": "PhV_phsA", "control_code": "PhV_phsB",
     "block": [40, 48], "points": [10, 55],
     "expected": "abstention on masked points; no imputed values"},
    {"scenario_id": "SYN-XPHASE-001", "scenario_type": "cross_phase",
     "target_code": "A_phsA", "control_code": "A_phsB",
     "extra_codes": ["A_phsC"],
     "window": [25, 55], "scale": 0.55,
     "expected": "multivariate spread flag; companion series retained"},
)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parent_series(records):
    """Ordered (timestamp, value, parent evidence id) per code for the target asset."""
    grouped = {}
    for record in records:
        if record.get("asset_id") != TARGET_ASSET:
            continue
        grouped.setdefault(record.get("measurement_type_code"), []).append(record)
    series = {}
    for code, rows in grouped.items():
        ordered = sorted(rows, key=lambda r: r.get("observed_at_original"))
        series[code] = [(r.get("observed_at_original"), r.get("measurement_value"),
                         r.get("evidence_id")) for r in ordered]
    return series


def derive_row(parent, code, timestamp, value, scenario_id, change):
    return {"asset_id": TARGET_ASSET,
            "measurement_point_id": parent.get("measurement_point_id"),
            "measurement_type_code": code,
            "measurement_name": parent.get("measurement_name"),
            "measurement_value": value,
            "measurement_value_status": "missing" if value is None else "present",
            "original_unit": None, "normalized_unit": None,
            "unit_status": "unconfirmed",
            "observed_at_original": timestamp,
            "observed_at_timezone": None, "timezone_status": "unconfirmed",
            "dataset_type": parent.get("dataset_type"),
            "evidence_id": "syn_" + hashlib.sha256(
                f"{scenario_id}|{code}|{timestamp}".encode()).hexdigest()[:20],
            "parent_evidence_id": parent.get("evidence_id"),
            "scenario_id": scenario_id,
            "change": change,
            "quality_status": "missing" if value is None else "valid",
            "quality_flags": "synthetic perturbation; units unconfirmed",
            "data_origin": "synthetic",
            "schema_version": "1.0.0"}


def generate(seed=18421):
    rng = random.Random(seed)
    canonical = [json.loads(line) for line in CANONICAL_PATH.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    parent_hash = sha256_file(CANONICAL_PATH)
    series = parent_series(canonical)
    by_code_record = {}
    for record in canonical:
        if record.get("asset_id") == TARGET_ASSET:
            by_code_record.setdefault(record.get("measurement_type_code"), []).append(record)
    for rows in by_code_record.values():
        rows.sort(key=lambda r: r.get("observed_at_original"))

    derived, truth = [], {}
    for spec in SCENARIOS:
        sid = spec["scenario_id"]
        stype = spec["scenario_type"]
        target_rows = by_code_record[spec["target_code"]]
        control_rows = by_code_record[spec["control_code"]]
        events = []
        if stype == "spike":
            start, end = spec["window"]
            index = rng.randrange(start, end)
            for i, row in enumerate(target_rows):
                value = row.get("measurement_value")
                if i == index:
                    value = (value or 0) + spec["offset"]
                    events.append({"code": spec["target_code"], "index": index})
                derived.append(derive_row(
                    row, spec["target_code"], row.get("observed_at_original"),
                    value, sid, "spike_offset" if i == index else "copied"))
            for row in control_rows:
                derived.append(derive_row(
                    row, spec["control_code"], row.get("observed_at_original"),
                    row.get("measurement_value"), sid, "control_copied"))
        elif stype == "sustained_shift":
            start, end = spec["window"]
            for i, row in enumerate(target_rows):
                value = row.get("measurement_value")
                changed = start <= i < end
                if changed and value is not None:
                    value = value + spec["offset"]
                if changed:
                    events.append({"code": spec["target_code"], "index": i})
                derived.append(derive_row(
                    row, spec["target_code"], row.get("observed_at_original"),
                    value, sid, "shift_offset" if changed else "copied"))
            for row in control_rows:
                derived.append(derive_row(
                    row, spec["control_code"], row.get("observed_at_original"),
                    row.get("measurement_value"), sid, "control_copied"))
        elif stype == "gradual_drift":
            start, end = spec["window"]
            for i, row in enumerate(target_rows):
                value = row.get("measurement_value")
                changed = start <= i < end
                if changed and value is not None:
                    value = value + spec["slope_per_step"] * (i - start) \
                        + (rng.random() - 0.5) * 2 * spec["noise"]
                    events.append({"code": spec["target_code"], "index": i})
                derived.append(derive_row(
                    row, spec["target_code"], row.get("observed_at_original"),
                    value, sid, "drift_ramp" if changed else "copied"))
            for row in control_rows:
                derived.append(derive_row(
                    row, spec["control_code"], row.get("observed_at_original"),
                    row.get("measurement_value"), sid, "control_copied"))
        elif stype == "missing_data":
            masked = set(range(spec["block"][0], spec["block"][1])) | set(spec["points"])
            for i, row in enumerate(target_rows):
                if i in masked:
                    events.append({"code": spec["target_code"], "index": i})
                derived.append(derive_row(
                    row, spec["target_code"], row.get("observed_at_original"),
                    None if i in masked else row.get("measurement_value"),
                    sid, "masked_missing" if i in masked else "copied"))
            for row in control_rows:
                derived.append(derive_row(
                    row, spec["control_code"], row.get("observed_at_original"),
                    row.get("measurement_value"), sid, "control_copied"))
        elif stype == "cross_phase":
            start, end = spec["window"]
            for i, row in enumerate(target_rows):
                value = row.get("measurement_value")
                changed = start <= i < end
                if changed and value is not None:
                    value = value * spec["scale"]
                    events.append({"code": spec["target_code"], "index": i})
                derived.append(derive_row(
                    row, spec["target_code"], row.get("observed_at_original"),
                    value, sid, "cross_phase_scale" if changed else "copied"))
            for extra in [spec["control_code"]] + list(spec.get("extra_codes", [])):
                for row in by_code_record[extra]:
                    derived.append(derive_row(
                        row, extra, row.get("observed_at_original"),
                        row.get("measurement_value"), sid, "control_copied"))
        truth[sid] = {"events": events, "control_code": spec["control_code"],
                      "expected": spec["expected"]}
    manifest = {"generator": GENERATOR_VERSION, "seed": seed,
                "parent_dataset": {"path": "data/output/canonical_measurements.ndjson",
                                   "sha256": parent_hash,
                                   "asset_id": TARGET_ASSET},
                "scenarios": [{k: v for k, v in spec.items()} for spec in SCENARIOS],
                "derived_rows": len(derived),
                "partition_rule": "All variants of one scenario share a partition; "
                                  "never split a scenario across train/test."}
    return manifest, derived, truth


def main(seed=18421):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest, derived, truth = generate(seed)
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    (OUT_DIR / "scenarios.ndjson").write_text(
        "\n".join(json.dumps(r, ensure_ascii=True, sort_keys=True) for r in derived)
        + "\n", encoding="utf-8")
    (OUT_DIR / "truth.json").write_text(
        json.dumps(truth, ensure_ascii=True, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"scenarios={len(manifest['scenarios'])} derived={len(derived)}")
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
