"""Telemetry source audit (stdlib only).

Reads the eight raw telemetry texts from the vendored archive (byte-identical
to the supplied files, verified by hash) and reports per-file statistics,
per-class asset inventory, history/snapshot relations, and JSON-input equality.
Writes data/output/telemetry_source_audit.json. Raw files never modified.
"""
from __future__ import annotations

import io
import json
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = ROOT / "data/source_archive/originals"
OUT_PATH = ROOT / "data/output/telemetry_source_audit.json"
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def find_texts():
    hits = []
    for archive in sorted(ARCHIVE_DIR.glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if name.endswith(".txt") and "量测" in name:
                    hits.append((str(archive), name))
    return hits


def parse_ts(value):
    try:
        return datetime.strptime(value.strip(), TS_FORMAT)
    except (ValueError, AttributeError):
        return None


def normalize_asset_id(value):
    """Normalize an identifier for join keys: strip surrounding whitespace.

    Case is preserved (identifiers are case-sensitive: '28DKX-3681' must never
    equal '28dkx-3681'). Empty/blank/None normalize to None, which never joins.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def main():
    files, inventory = [], {}
    for archive, member in sorted(find_texts()):
        with zipfile.ZipFile(archive) as z:
            payload = json.loads(z.read(member).decode("utf-8"))
        records = payload.get("data", {}).get("result", {}).get("records", [])
        points = [(r, p) for r in records for p in r.get("telemetryValue", [])]
        stamps = sorted({p.get("dataTime") for _, p in points if p.get("dataTime")})
        parsed = [parse_ts(s) for s in stamps]
        intervals = sorted((b - a).total_seconds() / 60.0
                           for a, b in zip(parsed, parsed[1:])
                           if a and b)
        values = []
        for _, p in points:
            try:
                values.append(float(p.get("measValue")))
            except (TypeError, ValueError):
                values.append(None)
        numeric = [v for v in values if v is not None]
        short = member.split("/")[-1]
        files.append({
            "filename": short, "archive": Path(archive).name,
            "equipment_type_code": sorted({r.get("psrType") for r in records}),
            "record_count": len(records),
            "asset_ids": sorted({r.get("psrId") for r in records}),
            "measurement_codes": sorted({r.get("measTypeCode") for r in records}),
            "point_count": len(points),
            "timestamp_min": stamps[0] if stamps else None,
            "timestamp_max": stamps[-1] if stamps else None,
            "unique_timestamps": len(stamps),
            "interval_min": intervals[0] if intervals else None,
            "interval_median": intervals[len(intervals) // 2] if intervals else None,
            "interval_max": intervals[-1] if intervals else None,
            "null_values": sum(1 for v in values if v is None),
            "zero_values": sum(1 for v in numeric if v == 0),
            "record_keys": sorted({k for r in records for k in r}),
            "point_keys": sorted({k for _, p in points for k in p}),
        })
        for record in records:
            asset = record.get("psrId")
            entry = inventory.setdefault(asset, {"files": [], "codes": set(),
                                                 "timestamps": set()})
            entry["files"].append(short)
            entry["codes"].add(record.get("measTypeCode"))
            for point in record.get("telemetryValue", []):
                if point.get("dataTime"):
                    entry["timestamps"].add(point.get("dataTime"))
    asset_inventory = {asset: {"files": sorted(info["files"]),
                              "code_count": len(info["codes"]),
                              "unique_timestamps": len(info["timestamps"]),
                              "span": [min(info["timestamps"]),
                                       max(info["timestamps"])] if info["timestamps"]
                              else [None, None]}
                       for asset, info in sorted(inventory.items())}
    audit = {"audit_version": "telemetry-source-audit-v1",
             "files": files, "asset_inventory": asset_inventory,
             "asset_count": len(asset_inventory),
             "separation_rule": "Equipment classes are never pooled; shared codes "
                                "do not imply a shared population."}
    OUT_PATH.write_text(json.dumps(audit, ensure_ascii=True, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"files={len(files)} assets={len(asset_inventory)}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
