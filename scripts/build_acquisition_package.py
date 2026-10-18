"""Build the evidence acquisition package (stdlib only, deterministic).

Reads the register, gap matrix, and manifest; emits a machine-readable package
plus a traceability report. Never modifies statuses: the package requests
evidence, it does not review it. All reviewer-facing text is English.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "docs/evidence_acquisition_package.json"
OUT_TRACE = ROOT / "docs/m1_13_traceability.md"

# Priority tiers: lower number = earlier request.
PRIORITY = {"measurement_semantics": 1, "time_semantics": 2,
            "asset_identity_context": 3, "rule_applicability": 4,
            "target_outcomes": 5, "metadata_and_coverage": 6}

GROUP = {
    "EV-CODE-A_phsA": "measurement_semantics", "EV-CODE-A_phsB": "measurement_semantics",
    "EV-CODE-A_phsC": "measurement_semantics", "EV-CODE-PhV_phsA": "measurement_semantics",
    "EV-CODE-PhV_phsB": "measurement_semantics", "EV-CODE-PhV_phsC": "measurement_semantics",
    "EV-CODE-PhW_phsA": "measurement_semantics", "EV-CODE-PhW_phsB": "measurement_semantics",
    "EV-CODE-PhW_phsC": "measurement_semantics", "EV-CODE-PhVAr_phsA": "measurement_semantics",
    "EV-CODE-PhVAr_phsB": "measurement_semantics", "EV-CODE-PhVAr_phsC": "measurement_semantics",
    "EV-CODE-TotW": "measurement_semantics", "EV-CODE-TotVar": "measurement_semantics",
    "EV-TIME-TIMEZONE": "time_semantics", "EV-TIME-INTERVAL": "time_semantics",
    "EV-ASSET-IDENTITY": "asset_identity_context", "EV-ASSET-OIL": "asset_identity_context",
    "EV-RULES-APPROVED": "rule_applicability",
    "EV-OUTCOMES-LABELED": "target_outcomes",
    "EV-META-astId": "metadata_and_coverage", "EV-META-astType": "metadata_and_coverage",
    "EV-META-consNo": "metadata_and_coverage", "EV-META-consType": "metadata_and_coverage",
    "EV-META-pt": "metadata_and_coverage", "EV-META-ct": "metadata_and_coverage",
    "EV-META-tFactor": "metadata_and_coverage",
    "EV-ZERO-READINGS": "metadata_and_coverage", "EV-REACTIVE-SIGN": "metadata_and_coverage",
    "EV-COVERAGE-EVAL": "metadata_and_coverage",
}

WHY = {
    "measurement_semantics": (
        "Without confirmed units and scaling, every reading is a bare number: no "
        "loading, balance, or limit calculation can be interpreted. "
        "Nontechnical: we need to know what unit each meter reports in before "
        "any reading can be understood."),
    "time_semantics": (
        "Without timezone and sampling semantics, timestamps cannot be ordered "
        "against real events, converted, or resampled. "
        "Nontechnical: we need to know what clock the timestamps follow and how "
        "often meters report before trends mean anything."),
    "asset_identity_context": (
        "Assessment must attach to a confirmed asset: class codes do not prove "
        "which equipment or construction is being reviewed. "
        "Nontechnical: we need written confirmation of which transformer this is "
        "and what it is built from."),
    "rule_applicability": (
        "Candidate rules with thresholds exist, but thresholds need an approved "
        "standard clause plus a ruling that the clause covers these telemetry streams. "
        "Nontechnical: we need an engineer to approve which rules apply to this data."),
    "target_outcomes": (
        "Supervised evaluation needs timestamped, verified outcomes linked to "
        "assets; class-level rows without linkage cannot validate anything. "
        "Nontechnical: we need confirmed records of past faults and repairs to "
        "test any future early-warning method."),
    "metadata_and_coverage": (
        "Auxiliary keys, zero-value meaning, sign convention, and fleet coverage "
        "decide whether records can be joined, compared, and generalized. "
        "Nontechnical: we need the data dictionary finished and more labeled "
        "examples before results can be trusted more broadly."),
}


def main():
    register = json.loads((ROOT / "docs/evidence_register.json").read_text(encoding="utf-8"))
    matrix = {i["evidence_id"]: i for i in json.loads(
        (ROOT / "docs/evidence_gap_matrix.json").read_text(encoding="utf-8"))["items"]}
    requests = []
    for item in register["items"]:
        entry = matrix[item["id"]]
        group = GROUP[item["id"]]
        requests.append({
            "evidence_id": item["id"],
            "priority": PRIORITY[group],
            "priority_group": group,
            "status": item["status"],
            "question": item["question"],
            "why_it_matters_technical": WHY[group].split(" Nontechnical:")[0],
            "why_it_matters_plain": WHY[group].split("Nontechnical:")[1].strip(),
            "existing_sources": entry["provenance"],
            "established": ("cited source text as recorded in the gap matrix; "
                            "engineering claim still open"),
            "not_established": "the engineering answer itself (see acceptance criteria)",
            "required_external": item["evidence_required"],
            "applicability": item["applicability"],
            "acceptance_criteria": entry["acceptance_criteria"],
            "dependencies": entry["dependencies"],
            "owner_role": item["reviewer_required"],
            "blocks": item["blocks"],
        })
    requests.sort(key=lambda r: (r["priority"], r["evidence_id"]))
    package = {"package_version": "1.0.0",
               "status_rule": "Requests never change verification status. Only a "
                              "recorded human review meeting acceptance criteria does.",
               "requests": requests}
    OUT_JSON.write_text(json.dumps(package, ensure_ascii=True, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    lines = ["# Traceability: requests to evidence IDs and sources",
             "",
             "| Priority | Evidence ID | Status | Source cited |",
             "|---|---|---|---|"]
    for request in requests:
        lines.append(f"| {request['priority']} | {request['evidence_id']} | "
                     f"{request['status']} | "
                     f"{request['existing_sources'] or 'none located'} |")
    lines += ["",
              "Source archive checksums: docs/english_source_manifest.json. "
              "Register statuses unchanged by this package."]
    OUT_TRACE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"requests={len(requests)}")


if __name__ == "__main__":
    main()
