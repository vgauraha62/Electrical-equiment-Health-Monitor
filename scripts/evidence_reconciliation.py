"""Evidence reconciliation report (stdlib only, deterministic).

For each acquisition-package request, reports dispatch/receipt truthfully from
isolated intake storage: with no submissions present, every request shows zero
received and awaits dispatch. Never marks requests sent or responses received
without a record. Never touches verification statuses.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_PATH = ROOT / "data/output/evidence_reconciliation.json"
DISPATCH_LOG = ROOT / "data/output/dispatch_log.json"

# Lifecycle, in order. Dispatch-side states require a dispatch-log record;
# receipt-side states derive from intake submissions. Nothing is inferred.
LIFECYCLE = ("drafted", "approved_for_dispatch", "sent", "response_received",
             "intake_validated", "ready_for_review", "accepted_or_rejected",
             "engineering_verified")


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def lifecycle_state(dispatch_events, submissions):
    """Derive the furthest honest lifecycle state from records alone."""
    sent = [e for e in dispatch_events if e.get("event") == "sent"]
    approved = [e for e in dispatch_events if e.get("event") == "approved_for_dispatch"]
    if submissions:
        latest = sorted(submissions, key=lambda s: s.get("created_at", ""))
        intake = latest[-1].get("intake_status")
        mapping = {"received": "response_received",
                   "intake_validated": "intake_validated",
                   "needs_clarification": "intake_validated",
                   "ready_for_review": "ready_for_review",
                   "accepted": "accepted_or_rejected",
                   "rejected": "accepted_or_rejected"}
        return mapping.get(intake, "response_received")
    if sent:
        return "sent"
    if approved:
        return "approved_for_dispatch"
    return "drafted"


def reconcile(package, register, dispatch_log, intake_submissions):
    register = {i["id"]: i for i in register["items"]}
    dispatches = {}
    for event in dispatch_log:
        dispatches.setdefault(event.get("evidence_id"), []).append(event)
    received = {}
    for submission in intake_submissions:
        received.setdefault(submission.get("evidence_request_id"), []).append(submission)
    rows = []
    for request in package["requests"]:
        item = register[request["evidence_id"]]
        state = lifecycle_state(dispatches.get(request["evidence_id"], []),
                                received.get(request["evidence_id"], []))
        rows.append({
            "evidence_id": request["evidence_id"],
            "priority": request["priority"],
            "lifecycle_state": state,
            "request_sent": state not in ("drafted", "approved_for_dispatch"),
            "responses_received": len(received.get(request["evidence_id"], [])),
            "provenance_complete": None,
            "answers_question": None,
            "contradictions": [],
            "missing_fields": ["response itself"] if state == "drafted" else [],
            "reviewer_actions": ["await dispatch decision", item["reviewer_required"]
                                 + " review when evidence arrives"],
            "register_status": item["status"],
        })
    return {"report_version": "1.0.0",
            "summary": {"requests": len(rows),
                        "requests_sent": sum(1 for r in rows if r["request_sent"]),
                        "responses_received": sum(r["responses_received"] for r in rows),
                        "lifecycle_states": sorted({r["lifecycle_state"] for r in rows}),
                        "note": "States derive from dispatch-log and intake records only."},
            "rows": rows}


def main():
    package = load_json(ROOT / "docs/evidence_acquisition_package.json", {})
    register = load_json(ROOT / "docs/evidence_register.json", {})
    dispatch_log = load_json(DISPATCH_LOG, [])
    intake_dir = os.getenv("EVIDENCE_INTAKE_DIR",
                           str(ROOT / "data/output/evidence_intake"))
    submissions = []
    intake_path = Path(intake_dir)
    if intake_path.is_dir():
        for path in sorted(intake_path.glob("*.json")):
            try:
                submissions.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
    report = reconcile(package, register,
                       dispatch_log if isinstance(dispatch_log, list) else [],
                       submissions)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=True, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"requests={report['summary']['requests']} "
          f"sent={report['summary']['requests_sent']} "
          f"received={report['summary']['responses_received']} "
          f"states={report['summary']['lifecycle_states']}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
