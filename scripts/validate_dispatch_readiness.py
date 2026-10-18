"""Dispatch-readiness validation and approval gate (stdlib only).

Two separate concerns, never conflated:
1. Package validation: structural checks over the ten resolution requests
   (stable unique IDs, register linkage, evidence descriptions, acceptance
   criteria, owner roles, dependencies).
2. Dispatch authorization: an explicit approval record. Absent or incomplete
   approval means NO DISPATCH, regardless of package validity, test results,
   self-asserted labels, or milestone completion.

Default state of this repository: no dispatch approved, nothing sent.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUESTS_PATH = ROOT / "docs/evidence_resolution_requests.json"
REGISTER_PATH = ROOT / "docs/evidence_register.json"
APPROVAL_PATH = ROOT / "docs/dispatch_approval.json"

APPROVAL_ROLES = ("dispatch_authority",)


class ReadinessError(Exception):
    pass


def validate_package(package, register_ids):
    """Return (valid_requests, blocked) with reasons. Pure structure, no auth."""
    if not isinstance(package, dict) or not package.get("requests"):
        raise ReadinessError("package has no requests")
    required = ("request_id", "priority", "owner_role", "title",
                "affected_fields_or_identifiers", "unresolved_decision",
                "required_evidence", "linked_register_ids",
                "acceptance_criterion", "acceptance_text", "resolution_status")
    seen, valid, blocked = set(), [], []
    for request in package["requests"]:
        problems = []
        rid = request.get("request_id")
        if not rid or rid in seen:
            problems.append("unstable or duplicate request ID")
        seen.add(rid)
        for field in required:
            if not request.get(field):
                problems.append(f"missing {field}")
        for linked in request.get("linked_register_ids", []):
            if linked not in register_ids:
                problems.append(f"unknown register link {linked}")
        if request.get("resolution_status") != "drafted":
            problems.append("unexpected pre-dispatch status")
        (valid if not problems else blocked).append(
            {"request_id": rid, "problems": problems})
    return valid, blocked


def validate_approval(approval):
    """Return (authorized, reasons). Self-labels and test outcomes never authorize."""
    if not isinstance(approval, dict):
        return False, ["no approval record present"]
    reasons = []
    if approval.get("decision") != "approved":
        reasons.append("decision is not an explicit approval")
    if approval.get("approver_role") not in APPROVAL_ROLES:
        reasons.append("approver_role is not a dispatch authority")
    if not approval.get("approver_id") or not isinstance(approval.get("approver_id"), str):
        reasons.append("identified approver missing (labels alone do not authorize)")
    if not approval.get("date"):
        reasons.append("approval date missing")
    if not approval.get("scope"):
        reasons.append("dispatch scope missing")
    return (not reasons), reasons


def readiness_report():
    package = json.loads(REQUESTS_PATH.read_text(encoding="utf-8"))
    register = json.loads(REGISTER_PATH.read_text(encoding="utf-8"))
    register_ids = {i["id"] for i in register["items"]}
    try:
        approval = json.loads(APPROVAL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        approval = None
    valid, blocked = validate_package(package, register_ids)
    authorized, auth_reasons = validate_approval(approval)
    return {
        "structurally_valid": [v["request_id"] for v in valid],
        "blocked_incomplete": blocked,
        "ready_for_human_review": [v["request_id"] for v in valid],
        "authorized_for_dispatch": [v["request_id"] for v in valid] if authorized else [],
        "approval": {"present": approval is not None, "authorized": authorized,
                     "reasons": auth_reasons},
        "dispatch_default": "NO DISPATCH without explicit authorization",
    }


def main():
    report = readiness_report()
    print(f"valid={len(report['structurally_valid'])} "
          f"blocked={len(report['blocked_incomplete'])} "
          f"authorized={len(report['authorized_for_dispatch'])}")
    print(f"approval: {report['approval']}")
    if report["blocked_incomplete"]:
        raise SystemExit("incomplete requests blocked")


if __name__ == "__main__":
    main()
