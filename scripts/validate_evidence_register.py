"""Evidence-register validator (M1.9, local-only, stdlib only).

Validates structure only: required fields, allowed statuses, unique IDs,
evidence+approval required for verified entries, and completeness of the 14
measurement-code entries. Never approves anything; approval is a human act
recorded in the register, not a script outcome.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER_PATH = ROOT / "docs/evidence_register.json"

ALLOWED_STATUSES = ("unverified", "evidence_pending", "under_review", "verified")
ALLOWED_APPLICABILITY = ("target_asset", "class_level")
REQUIRED_FIELDS = ("id", "topic", "question", "status", "evidence_required",
                   "reviewer_required", "blocks", "applicability")
REQUIRED_CODES = ("A_phsA", "A_phsB", "A_phsC", "PhV_phsA", "PhV_phsB",
                  "PhV_phsC", "PhW_phsA", "PhW_phsB", "PhW_phsC",
                  "PhVAr_phsA", "PhVAr_phsB", "PhVAr_phsC", "TotW", "TotVar")


class RegisterError(Exception):
    pass


# Strict status-transition policy. Translation completion never appears here:
# these transitions govern engineering evidence only, and verified additionally
# requires location + recorded human approval (enforced by validate_register).
ALLOWED_EVIDENCE_TRANSITIONS = {
    "unverified": ("evidence_pending",),
    "evidence_pending": ("under_review", "unverified"),
    "under_review": ("verified", "evidence_pending"),
    "verified": (),
}


def validate_transition(old_status, new_status):
    """Raise RegisterError unless old_status -> new_status is permitted."""
    if old_status not in ALLOWED_STATUSES or new_status not in ALLOWED_STATUSES:
        raise RegisterError(f"unknown status in transition {old_status!r} -> {new_status!r}")
    if new_status not in ALLOWED_EVIDENCE_TRANSITIONS.get(old_status, ()):
        raise RegisterError(
            f"transition {old_status!r} -> {new_status!r} not permitted; "
            f"verified is terminal and translation never promotes engineering status")
    return True


def validate_register(register):
    """Return (item_count, status_counts). Raise RegisterError on any defect."""
    errors = []
    if not isinstance(register, dict):
        raise RegisterError("register must be a JSON object")
    items = register.get("items")
    if not isinstance(items, list) or not items:
        raise RegisterError("register.items must be a non-empty list")
    seen_ids = set()
    for index, item in enumerate(items):
        where = f"items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            continue
        for field in REQUIRED_FIELDS:
            if field not in item:
                errors.append(f"{where}: missing required field '{field}'")
        item_id = item.get("id", f"{where}")
        if item_id in seen_ids:
            errors.append(f"{where}: duplicate id '{item_id}'")
        seen_ids.add(item_id)
        status = item.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{item_id}: invalid status {status!r}")
        if item.get("applicability") not in ALLOWED_APPLICABILITY:
            errors.append(f"{item_id}: applicability must be one of "
                          f"{list(ALLOWED_APPLICABILITY)}")
        if status in ("evidence_pending", "under_review") and \
                not item.get("evidence_location"):
            errors.append(f"{item_id}: status {status} requires cited evidence_location")
        blocks = item.get("blocks")
        if blocks is not None and (not isinstance(blocks, list) or not blocks
                                   or not all(isinstance(b, str) and b for b in blocks)):
            errors.append(f"{item_id}: 'blocks' must be a non-empty list of strings")
        if status == "verified":
            if not item.get("evidence_location"):
                errors.append(f"{item_id}: verified status requires evidence_location")
            approval = item.get("approval")
            if (not isinstance(approval, dict) or not approval.get("reviewed_by")
                    or not approval.get("reviewed_at")):
                errors.append(f"{item_id}: verified status requires "
                              "approval{{reviewed_by, reviewed_at}}")
    covered = {item.get("measurement_code") for item in items
               if isinstance(item, dict)}
    for code in REQUIRED_CODES:
        if code not in covered:
            errors.append(f"missing measurement-code entry for '{code}'")
    if errors:
        raise RegisterError("; ".join(errors))
    status_counts = {}
    for item in items:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    return len(items), status_counts


def main():
    register = json.loads(REGISTER_PATH.read_text(encoding="utf-8"))
    count, statuses = validate_register(register)
    print(f"items={count} statuses={json.dumps(statuses, sort_keys=True)}")
    verified = statuses.get("verified", 0)
    print(f"verified_items={verified} (must be 0 without authoritative evidence+review)")


if __name__ == "__main__":
    main()
