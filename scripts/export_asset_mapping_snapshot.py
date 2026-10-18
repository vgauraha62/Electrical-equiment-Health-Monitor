#!/usr/bin/env python3
"""Validate/export locally established manual links for a read-only demo.

This command uses bundled local telemetry even if BQ_TABLE is present in the
shell. It never contacts Google Cloud. Default mode writes the generated runtime
snapshot; --check verifies that the committed snapshot matches local mappings.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.asset_mapping as asset_mapping  # noqa: E402
import app.main as main  # noqa: E402
import app.agent_contract as contract  # noqa: E402


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="validate that the packaged snapshot matches local mappings")
    args = parser.parse_args()

    # Make the local-only boundary explicit. This utility must never fall through
    # to the application's optional BigQuery data source.
    main.BQ_TABLE = ""
    main._cache.update(at=0.0, rows=None)
    registry, equipment_ids = main._mapping_context()
    directory = asset_mapping.store_dir(ROOT)
    output = ROOT / "data/output/asset_mapping_snapshot.json"
    expected = asset_mapping.build_mapping_snapshot(directory, registry, equipment_ids)

    if args.check:
        if not output.is_file():
            print("FAIL: packaged asset mapping snapshot is missing.", file=sys.stderr)
            return 1
        try:
            actual = json.loads(output.read_text(encoding="utf-8"))
            asset_mapping.load_mapping_snapshot(output, registry, equipment_ids)
        except (OSError, ValueError, asset_mapping.MappingError) as exc:
            print(f"FAIL: packaged snapshot is invalid: {exc}", file=sys.stderr)
            return 1
        if contract.canonical_json(actual) != contract.canonical_json(expected):
            print("FAIL: packaged snapshot is stale or differs from local mappings.",
                  file=sys.stderr)
            return 1
        print_report(expected, directory, checked=True)
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(expected, ensure_ascii=True, indent=2,
                                    sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    # Re-open and validate the exact bytes that will be packaged.
    asset_mapping.load_mapping_snapshot(output, registry, equipment_ids)
    print_report(expected, directory, checked=False)
    return 0


def print_report(snapshot, directory, *, checked):
    all_docs = asset_mapping.list_mappings(directory, include_removed=True)
    excluded_unresolved = sum(doc.get("status") == "unresolved" for doc in all_docs)
    excluded_removed = sum(doc.get("status") == "removed" for doc in all_docs)
    action = "Validated" if checked else "Exported"
    print(f"{action} read-only mapping snapshot")
    print(f"Active manual relationships: {snapshot['mapping_count']}")
    print(f"Unresolved entries excluded: {excluded_unresolved}")
    print(f"Removed entries excluded: {excluded_removed}")
    print(f"Source registry digest: {snapshot['registry_digest_sha256']}")
    print(f"Snapshot digest: {snapshot['snapshot_digest_sha256']}")
    print("The deployed snapshot is read-only; local mapping edits are not included "
          "until exported again.")


if __name__ == "__main__":
    raise SystemExit(main_cli())
