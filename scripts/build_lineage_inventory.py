"""Build the lineage inventory (stdlib only, deterministic).

Aggregates existing audited artifacts into one machine-readable inventory for
the dashboard and for future retrieval. Counts are reused, never recounted
inconsistently: translated telemetry copies are listed but never added to
observation totals. Nothing here changes source data or evidence statuses.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data/output/lineage_inventory.json"


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main():
    manifest = load("data/output/manifest.json")
    audit = load("data/output/telemetry_source_audit.json")
    catalog = load("data/output/equipment_catalog.json")
    try:
        sources_manifest = load("docs/english_source_manifest.json")
        translated = {s["source_id"]: s for s in sources_manifest.get("sources", [])}
    except (OSError, ValueError):
        translated = {}

    def tr_status(source_id, default="not_started"):
        return translated.get(source_id, {}).get("translation_status", default)

    telemetry_files = [
        {"family": "telemetry inputs (canonical runtime)",
         "files": [f["filename"] for f in audit["files"]],
         "records": sum(f["record_count"] for f in audit["files"]),
         "observations": sum(f["point_count"] for f in audit["files"]),
         "status": "canonical runtime input",
         "provenance": "data/input/telemetry/*.json; checksums in data/output/manifest.json",
         "translation_status": "labels_translated_upstream",
         "review_status": "unreviewed",
         "retrieval": "served"},
        {"family": "translated telemetry equivalents",
         "files": ["data_english/measurement/*.txt (8 files)"],
         "records": 152, "observations": 5970,
         "status": "duplicate equivalent — never counted as additional observations",
         "provenance": "data_english/measurement/; value parity verified, 0 mismatches",
         "translation_status": tr_status("SRC-TELEMETRY-TXT", "translated_draft"),
         "review_status": "unreviewed",
         "retrieval": "excluded"},
        # The ledger translation review is a recorded human fact (project owner,
        # 2026-10-17), not a build result: it is repeated here so regeneration
        # neither invents nor reverts it. Scope is translation accuracy of the
        # ledger fields only; it does not verify identity, rules, units, time
        # semantics, or outcome labels.
        {"family": "equipment ledger + catalog",
         "files": ["equipment_ledger.xlsx", "data/output/equipment_catalog.json"],
         "records": 3811, "observations": None,
         "status": "context for exact-ID matches only",
         "provenance": "ledger workbook sha in catalog source record",
         "translation_status": "reviewed",
         "review_status": "approved",
         "translation_review": {
             "decision": "approved",
             "reviewed_at": "2026-10-17",
             "reviewed_by": "project_owner",
             "reviewer_role": "equipment ledger data owner",
             "scope": ("translation accuracy of the ledger fields only "
                       "(column meanings, code-table and date renderings)"),
             "excludes": ["physical asset identity",
                          "engineering-rule applicability",
                          "measurement units", "outcome labels",
                          "time semantics"]},
         "retrieval": "profiles only"},
        {"family": "defect records and standard library",
         "files": ["defect_records.xlsx", "defect_description_standard_library.xlsx"],
         "records": None, "observations": None,
         "status": "reference only; unfinished/unreviewed — excluded from retrieval",
         "provenance": "data_english headers/dictionaries",
         "translation_status": "partial_draft",
         "review_status": "unreviewed",
         "retrieval": "excluded by default"},
        {"family": "guides and standards",
         "files": ["evaluation guide translation", "scanned operating standard"],
         "records": None, "observations": None,
         "status": "reference only; scanned standard blocked (no OCR approval)",
         "provenance": "data_english guide markdowns",
         "translation_status": "partial_draft",
         "review_status": "unreviewed",
         "retrieval": "excluded by default"},
        {"family": "source archives",
         "files": ["data/source_archive/originals/*"],
         "records": None, "observations": None,
         "status": "immutable evidence, never served",
         "provenance": "sha256 recorded at vendor time",
         "translation_status": "not_applicable",
         "review_status": "not_applicable",
         "retrieval": "never"},
        {"family": "generated analysis outputs",
         "files": ["statistical_baseline.json", "temporal_audit.json",
                    "descriptive_analysis.json", "evaluation.json"],
         "records": None, "observations": None,
         "status": "descriptive only; frozen where marked",
         "provenance": "reproducible scripts in scripts/",
         "translation_status": "not_applicable",
         "review_status": "not_applicable",
         "retrieval": "never"},
    ]
    inventory = {
        "inventory_version": "lineage-inventory-v1",
        "sources": telemetry_files,
        "equipment": {
            "telemetry_ids": 13,
            "telemetry_observations": manifest["record_counts"]["accepted"],
            "ledger_ids": len(catalog["records"]),
            "exact_id_matches": 2,
            "telemetry_matched": 2,
            "telemetry_unmatched": 11,
            "match_note": "An exact ID match is a data link, not proof of physical identity.",
        },
        "unresolved": [
            "regulation page count: notes claim 98 pp, PDF has 49 pages",
            "defect library completeness framing vs task caution (stays unreviewed)",
            "telemetry label-style drift between translations (joins use codes)",
            "model-string pinyin residue and untranslated sheet names",
            "reviewer and OCR approvals outstanding",
            "outcome linkage unproven (0 container-to-ledger joins)",
        ],
        "retrieval_manifest": {
            "note": "Local-only shape for future retrieval; no cloud calls.",
            "source_types": ["telemetry", "equipment_profile", "defect_record",
                             "knowledge_document"],
            "fields": ["source_id", "locator", "citation", "translation_status",
                       "review_status", "relationship_type"],
        },
    }
    OUT_PATH.write_text(json.dumps(inventory, ensure_ascii=True, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"telemetry_ids=13 observations={manifest['record_counts']['accepted']} "
          f"ledger={len(catalog['records'])}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
