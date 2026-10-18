"""Build evidence-resolution requests (stdlib only, deterministic).

Converts the ten domain questions into traceable requests linked to register
IDs. Generates docs/evidence_resolution_requests.json. Never touches statuses:
all requests start drafted; resolution never verifies engineering claims.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "docs/evidence_resolution_requests.json"

RESOLUTION_TRANSITIONS = {
    "drafted": ("awaiting_owner",),
    "awaiting_owner": ("answered", "drafted"),
    "answered": ("under_review", "awaiting_owner"),
    "under_review": ("closed",),
    "closed": (),
}


def validate_resolution_transition(old, new):
    if new not in RESOLUTION_TRANSITIONS.get(old, ()):
        raise ValueError(f"resolution transition {old!r} -> {new!r} not permitted")
    return True


REQUESTS = (
    {"request_id": "REQ-01", "priority": 1, "owner_role": "data owner",
     "title": "Identifier namespaces and cross-system mapping",
     "affected": ["telemetry asset IDs (UUID, 28DKX-*, SBID*)",
                  "ledger psr_id/ast_id", "defect container_id"],
     "decision": "Whether non-matching IDs denote the same equipment.",
     "required_evidence": "Documented key map or export-system statement.",
     "register_ids": ["EV-ASSET-IDENTITY", "EV-META-astId", "EV-META-astType"],
     "acceptance": "telemetry_to_ledger_mapping",
     "question_ref": "domain question 1"},
    {"request_id": "REQ-02", "priority": 2, "owner_role": "asset owner",
     "title": "Missing ledger rows for metered assets",
     "affected": ["telemetry assets absent from ledger (11 IDs)",
                  "defect container 28DKX-3681 ledger absence"],
     "decision": "Whether absent assets are retired, out of scope, or missing data.",
     "required_evidence": "Owner confirmation per asset.",
     "register_ids": ["EV-ASSET-IDENTITY", "EV-OUTCOMES-LABELED"],
     "acceptance": "telemetry_to_ledger_mapping",
     "question_ref": "domain question 2"},
    {"request_id": "REQ-03", "priority": 1, "owner_role": "metering/transformer domain reviewer",
     "title": "Low-side voltage/current units and fill-rule applicability",
     "affected": ["A_phsA/B/C", "PhV_phsA/B/C", "high-side fill factor"],
     "decision": "Whether documented V/A and the x25 fill apply to these exports.",
     "required_evidence": "Applicability ruling with clause reference.",
     "register_ids": ["EV-CODE-A_phsA", "EV-CODE-A_phsB", "EV-CODE-A_phsC",
                      "EV-CODE-PhV_phsA", "EV-CODE-PhV_phsB", "EV-CODE-PhV_phsC"],
     "acceptance": "units_scaling_sign",
     "question_ref": "domain question 3"},
    {"request_id": "REQ-04", "priority": 1, "owner_role": "metering/transformer domain reviewer",
     "title": "Active/reactive power units and reactive sign convention",
     "affected": ["PhW_phsA/B/C", "PhVAr_phsA/B/C", "TotW", "TotVar"],
     "decision": "Units, aggregation, and sign reading of power values.",
     "required_evidence": "Metering documentation or reviewer ruling.",
     "register_ids": ["EV-CODE-PhW_phsA", "EV-CODE-PhW_phsB", "EV-CODE-PhW_phsC",
                      "EV-CODE-PhVAr_phsA", "EV-CODE-PhVAr_phsB", "EV-CODE-PhVAr_phsC",
                      "EV-CODE-TotW", "EV-CODE-TotVar", "EV-REACTIVE-SIGN"],
     "acceptance": "units_scaling_sign",
     "question_ref": "domain question 4"},
    {"request_id": "REQ-05", "priority": 2, "owner_role": "data owner",
     "title": "Timestamp timezone, sync, and interval encoding",
     "affected": ["dataTime strings", "timeInterval/timeUnit", "885-minute gap"],
     "decision": "How to order, convert, and resample timestamps.",
     "required_evidence": "Dated owner statement of timezone, sync, and encodings.",
     "register_ids": ["EV-TIME-TIMEZONE", "EV-TIME-INTERVAL"],
     "acceptance": "timestamp_cadence",
     "question_ref": "domain question 5"},
    {"request_id": "REQ-06", "priority": 3, "owner_role": "metering/transformer domain reviewer",
     "title": "Rule and threshold applicability to these telemetry streams",
     "affected": ["calc-sheet state rules", "DL/T Table A.8 clauses"],
     "decision": "Which clauses, if any, cover these streams.",
     "required_evidence": "Approved rule list with edition, clause, applicability.",
     "register_ids": ["EV-RULES-APPROVED"],
     "acceptance": "class_applicability",
     "question_ref": "domain question 6"},
    {"request_id": "REQ-07", "priority": 3, "owner_role": "asset owner",
     "title": "Defect outcome and severity validity",
     "affected": ["28DKX-3681 defect row", "12 class rows", "severity/elimination fields"],
     "decision": "Whether any defect row is a usable outcome label.",
     "required_evidence": "Owner-validated outcome set with linkage and state semantics.",
     "register_ids": ["EV-OUTCOMES-LABELED"],
     "acceptance": "outcome_severity_validity",
     "question_ref": "domain question 7"},
    {"request_id": "REQ-08", "priority": 4, "owner_role": "metering/transformer domain reviewer",
     "title": "Zero-reading and operating-state interpretation",
     "affected": ["2,223 breaker zeros", "transformer near-zero values"],
     "decision": "De-energized readings vs missing-data convention.",
     "required_evidence": "Metering documentation or reviewer ruling.",
     "register_ids": ["EV-ZERO-READINGS"],
     "acceptance": "zero_operating_state",
     "question_ref": "domain question 8"},
    {"request_id": "REQ-09", "priority": 4, "owner_role": "data owner",
     "title": "Container and typeless code dictionaries",
     "affected": ["container_type codes", "216 typeless defect rows",
                  "consNo/consType/pt/ct/tFactor keys"],
     "decision": "Code meanings and key roles before any use in joins.",
     "required_evidence": "Source-system dictionaries.",
     "register_ids": ["EV-META-consNo", "EV-META-consType", "EV-META-pt",
                      "EV-META-ct", "EV-META-tFactor"],
     "acceptance": "telemetry_to_ledger_mapping",
     "question_ref": "domain question 9"},
    {"request_id": "REQ-10", "priority": 5, "owner_role": "reliability engineer",
     "title": "Evaluation coverage inventory",
     "affected": ["2 ledger-linked assets", "1 candidate outcome link"],
     "decision": "What additional labeled assets/history are obtainable.",
     "required_evidence": "Coverage inventory with assets and history depths.",
     "register_ids": ["EV-COVERAGE-EVAL"],
     "acceptance": "telemetry_to_ledger_mapping",
     "question_ref": "domain question 10"},
)

ACCEPTANCE = {
    "telemetry_to_ledger_mapping": "Named IDs match exactly on a documented key; namespaces, normalizations, and exceptions recorded; owner confirms scope.",
    "defect_container_namespaces": "Container codebook located; linkage key to ledger/telemetry proven on samples; typeless rows classified, not assumed.",
    "units_scaling_sign": "Unit, scaling factor, and sign stated per code from metering documentation or reviewer ruling; CT/PT effects ruled in or out explicitly.",
    "timestamp_cadence": "Timezone/offset and interval encoding stated by the data owner; gaps attributable; equal-spacing claims still require cadence proof.",
    "class_applicability": "Approved rule list names edition, clause, covered equipment classes and streams; reviewer signs applicability to this telemetry.",
    "zero_operating_state": "Metering documentation or ruling distinguishes de-energized readings from missing data per code; no global zero rule assumed.",
    "outcome_severity_validity": "Owner-validated outcome set with asset linkage, timestamps, and state semantics; single-row candidates never promoted alone.",
}


def main():
    register_ids = {i["id"] for i in json.loads(
        (ROOT / "docs/evidence_register.json").read_text(encoding="utf-8"))["items"]}
    requests = []
    for spec in REQUESTS:
        unknown = [rid for rid in spec["register_ids"] if rid not in register_ids]
        if unknown:
            raise ValueError(f"{spec['request_id']} links unknown IDs: {unknown}")
        requests.append({
            "request_id": spec["request_id"], "priority": spec["priority"],
            "owner_role": spec["owner_role"], "title": spec["title"],
            "affected_fields_or_identifiers": spec["affected"],
            "unresolved_decision": spec["decision"],
            "required_evidence": spec["required_evidence"],
            "linked_register_ids": spec["register_ids"],
            "acceptance_criterion": spec["acceptance"],
            "acceptance_text": ACCEPTANCE[spec["acceptance"]],
            "resolution_status": "drafted",
            "reviewer": None, "decision_rationale": None,
            "contradictions": [],
            "source": spec["question_ref"],
        })
    package = {"package_version": "1.0.0",
               "status_rule": "Requests start drafted. Receiving or translating "
                              "evidence never changes engineering status; only the "
                              "register transition policy with recorded approval does.",
               "acceptance_definitions": ACCEPTANCE,
               "review_store_limitation": "Local review cases carry self-asserted "
                                          "reviewer labels without authentication; they "
                                          "support workflow notes, never professional "
                                          "credential or approval.",
               "requests": requests}
    OUT_PATH = ROOT / "docs/evidence_resolution_requests.json"
    OUT_PATH.write_text(json.dumps(package, ensure_ascii=True, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"requests={len(requests)}")


if __name__ == "__main__":
    main()
