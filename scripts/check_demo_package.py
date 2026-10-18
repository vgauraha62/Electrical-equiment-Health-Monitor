#!/usr/bin/env python3
"""Inspect the Cloud Run source package before a demo build (read-only, no gcloud).

This is the local packaging gate the demo deployment procedure runs first. It
implements the subset of ``.gcloudignore`` semantics the file actually uses
(directory prefixes, basename wildcards, path globs, last-match-wins with ``!``
negation), computes the effective file list for a source deployment from the
backend directory, and fails (nonzero) if a runtime-required file would be
excluded or a secret, credential, archive, development file, or unexpected data
file would be included.

It runs the asset-mapping snapshot freshness check when asked. It never runs
gcloud, never uploads, and never writes to the repository.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Files the runtime needs; the deploy must fail if any of these is excluded.
REQUIRED = (
    "app/main.py",
    "app/gemini_review.py",
    "app/agent_context.py",
    "app/agent_contract.py",
    "app/agent_draft.py",
    "app/asset_evidence.py",
    "app/asset_mapping.py",
    "app/evidence_review.py",
    "static/app.js",
    "static/index.html",
    "requirements.txt",
    "Procfile",
    "data/input/reference/defect_reference_sample.csv",
    "data/input/telemetry/distribution_network_circuit_breaker_snapshot.json",
    "data/input/telemetry/distribution_transformer_history.json",
    "data/input/telemetry/distribution_transformer_snapshot.json",
    "data/input/telemetry/feeder_snapshot.json",
    "data/input/telemetry/pole_mounted_circuit_breaker_history.json",
    "data/input/telemetry/pole_mounted_circuit_breaker_snapshot.json",
    "data/input/telemetry/pole_mounted_transformer_history.json",
    "data/input/telemetry/pole_mounted_transformer_snapshot.json",
    "data/output/statistical_baseline.json",
    "data/output/equipment_catalog.json",
    "data/output/ledger_review_record.json",
    "data/output/manifest.json",
    "data/output/asset_mapping_snapshot.json",
    "docs/evidence_register.json",
)

OUTPUT_ALLOWLIST = (
    "data/output/statistical_baseline.json",
    "data/output/equipment_catalog.json",
    "data/output/ledger_review_record.json",
    "data/output/manifest.json",
    "data/output/asset_mapping_snapshot.json",
)

FORBIDDEN_TOP_DIRS = (
    "tests", "scripts", "sql", "schemas", ".venv", "venv", "env", ".git",
    ".opencode", "secrets",
)
FORBIDDEN_BASENAMES = ("id_rsa", "id_ed25519", ".env")
FORBIDDEN_BASENAME_GLOBS = (
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", ".env.*",
    "*service-account*.json", "*service_account*.json",
    "*credentials*.json", "*credential*.json",
)
FORBIDDEN_GLOBS = ("*.zip", "*.tar", "*.tar.gz", "*.tgz", "*.gz", "*.7z", "*.rar")
FORBIDDEN_STATUSES = ("credential", "secret", "archive", "development",
                      "unexpected_data", "development_data")


def load_patterns(path=None):
    patterns = []
    for line in Path(path or (ROOT / ".gcloudignore")).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_ignored(rel, patterns):
    ignored = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        body = pattern[1:] if negated else pattern
        if body.endswith("/"):
            match = rel == body[:-1] or rel.startswith(body)
        elif "/" in body:
            match = fnmatch.fnmatchcase(rel, body)
        else:
            match = fnmatch.fnmatchcase(rel.split("/")[-1], body)
        if match:
            ignored = not negated
    return ignored


def all_files(root):
    files = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel == ".gcloudignore":
            continue
        files.append(rel)
    return files


def forbidden_reason(rel):
    parts = rel.split("/")
    top = parts[0]
    if top in FORBIDDEN_TOP_DIRS:
        return "development" if top not in ("secrets",) else "secret"
    basename = parts[-1]
    if basename in FORBIDDEN_BASENAMES:
        return "secret"
    for glob in FORBIDDEN_BASENAME_GLOBS:
        if fnmatch.fnmatchcase(basename, glob):
            return "credential"
    for glob in FORBIDDEN_GLOBS:
        if fnmatch.fnmatchcase(basename, glob):
            return "archive"
    if rel.startswith("data/output/"):
        if rel in OUTPUT_ALLOWLIST:
            return None
        return "unexpected_data"
    if rel.startswith("docs/") and rel != "docs/evidence_register.json":
        return "development"
    if rel.startswith("data/en/") or rel.startswith("data/source_archive/"):
        return "development_data"
    return None


def build_report(root=ROOT, check_snapshot=False):
    root = Path(root)
    patterns = load_patterns(root / ".gcloudignore")
    files = all_files(root)
    shipped = [rel for rel in files if not is_ignored(rel, patterns)]
    missing = [rel for rel in REQUIRED if rel not in shipped]
    forbidden = [{"path": rel, "reason": reason}
                 for rel in shipped
                 for reason in [forbidden_reason(rel)] if reason]
    total_bytes = sum((root / rel).stat().st_size for rel in shipped)
    snapshot = {"checked": False}
    if check_snapshot:
        proc = subprocess.run(
            [sys.executable, str(root / "scripts" / "export_asset_mapping_snapshot.py"),
             "--check"], cwd=root, capture_output=True, text=True)
        snapshot = {"checked": True, "returncode": proc.returncode,
                    "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    ok = not missing and not forbidden and (not check_snapshot or snapshot["returncode"] == 0)
    return {
        "ok": ok,
        "file_count": len(shipped),
        "total_bytes": total_bytes,
        "missing_required": missing,
        "forbidden_included": forbidden,
        "snapshot": snapshot,
        "files": shipped,
    }


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--check-snapshot", action="store_true",
                        help="also run the asset-mapping snapshot freshness check")
    args = parser.parse_args()
    report = build_report(check_snapshot=args.check_snapshot)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Effective package: {report['file_count']} file(s), "
              f"{report['total_bytes']} bytes")
        print(f"Required files present: {'yes' if not report['missing_required'] else 'NO'}")
        if report["missing_required"]:
            for rel in report["missing_required"]:
                print(f"  MISSING: {rel}")
        print(f"Forbidden files included: "
              f"{'none' if not report['forbidden_included'] else 'YES'}")
        for entry in report["forbidden_included"]:
            print(f"  FORBIDDEN ({entry['reason']}): {entry['path']}")
        if report["snapshot"]["checked"]:
            print(f"Asset-mapping snapshot check: "
                  f"{'fresh' if report['snapshot']['returncode'] == 0 else 'STALE'}")
            if report["snapshot"]["stdout"]:
                print(report["snapshot"]["stdout"])
            if report["snapshot"]["stderr"]:
                print(report["snapshot"]["stderr"], file=sys.stderr)
        print("PACKAGE OK" if report["ok"] else "PACKAGE REJECTED")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
