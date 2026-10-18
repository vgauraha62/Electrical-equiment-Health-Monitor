"""Persistent human-review case store (stdlib only, file-based).

Rationale (documented limitation): the existing POST /api/reviews persists
single outcome rows to BigQuery when configured — insert-only, cloud-gated, no
listing, transitions, or history. That layer cannot provide lifecycle guarantees
in isolated/local operation, so this module adds the smallest defensible
alternative: one JSON document per case plus an append-only history, stored
under REVIEW_CASES_DIR (default data/output/review_cases, never source data).

Guarantees: atomic writes (tmp file + os.replace), per-case monotonically
increasing version checked on every mutation (optimistic concurrency: stale
from_version yields conflict, never silent overwrite), idempotent creation via
client idempotency keys, immutable history (events appended, never edited),
server-generated UTC timestamps used for audit events only.

Reviewer labels are self-asserted strings recorded with verified:false. No
authentication exists in this application; nothing here establishes identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("open", "in_review", "completed", "evidence_required")
ALLOWED_TRANSITIONS = {
    "open": ("in_review", "evidence_required"),
    "in_review": ("completed", "evidence_required"),
    "evidence_required": ("in_review",),
    "completed": (),
}


class StoreError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def store_dir(root):
    directory = Path(os.getenv("REVIEW_CASES_DIR", root / "data/output/review_cases"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _case_path(directory, case_id):
    if not case_id or "/" in case_id or case_id.startswith("."):
        raise StoreError("Invalid case ID.", status=404)
    return directory / f"{case_id}.json"


def _write_atomic(path, document):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True)
                   + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_case(directory, case_id):
    try:
        return json.loads(_case_path(directory, case_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StoreError("Review case not found.", status=404)
    except ValueError:
        raise StoreError("Review case record is corrupt.", status=500)


def monitoring_digest(monitoring):
    """Content hash of a monitoring review minus volatile request metadata."""
    stable = {k: v for k, v in monitoring.items()
              if k not in ("request_id", "created_at")}
    return hashlib.sha256(json.dumps(stable, ensure_ascii=True, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def create_case(directory, asset_id, monitoring, note=None, reviewer=None,
                idempotency_key=None):
    digest = monitoring_digest(monitoring)
    if idempotency_key:
        for path in sorted(directory.glob("*.json")):
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if existing.get("idempotency_key") == idempotency_key:
                return existing, False
    case_id = str(uuid.uuid4())
    stamp = now_iso()
    case = {
        "case_id": case_id,
        "asset_id": asset_id,
        "status": "open",
        "version": 1,
        "idempotency_key": idempotency_key,
        "monitoring": {
            "workflow_version": (monitoring.get("analysis") or {}).get("version"),
            "dataset_version": "1.0.0",
            "digest_sha256": digest,
            "snapshot": monitoring,
        },
        "reviewer": {"label": reviewer, "verified": False} if reviewer else None,
        "created_at": stamp,
        "updated_at": stamp,
        "history": [{"seq": 1, "at": stamp, "event": "created",
                     "from_status": None, "to_status": "open",
                     "note": note, "actor": reviewer}],
    }
    _write_atomic(_case_path(directory, case_id), case)
    return case, True


def get_case(directory, case_id):
    return _read_case(directory, case_id)


def list_cases(directory, asset_id=None, status=None):
    cases = []
    for path in sorted(directory.glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if asset_id is not None and case.get("asset_id") != asset_id:
            continue
        if status is not None and case.get("status") != status:
            continue
        cases.append({k: case.get(k) for k in
                      ("case_id", "asset_id", "status", "version",
                       "created_at", "updated_at")})
    return cases


def _mutate(directory, case_id, from_version, event, to_status=None, note=None,
            actor=None, extra=None):
    case = _read_case(directory, case_id)
    if from_version != case.get("version"):
        raise StoreError(
            f"Version conflict: case is at version {case.get('version')}, "
            f"request cited {from_version}. Reload and retry.", status=409)
    stamp = now_iso()
    case["version"] = case["version"] + 1
    if to_status is not None:
        case["status"] = to_status
    case["updated_at"] = stamp
    entry = {"seq": len(case["history"]) + 1, "at": stamp, "event": event,
             "from_status": None, "to_status": to_status or case["status"],
             "note": note, "actor": actor}
    if extra:
        entry.update(extra)
    # History entries record the pre-mutation status explicitly below.
    entry["from_status"] = case["history"][-1]["to_status"] if case["history"] else None
    case["history"].append(entry)
    _write_atomic(_case_path(directory, case_id), case)
    return case


def add_note(directory, case_id, from_version, note, reviewer=None):
    if not isinstance(note, str) or not note.strip():
        raise StoreError("note is required as a non-empty string.")
    if reviewer is not None and (not isinstance(reviewer, str) or not reviewer.strip()):
        raise StoreError("reviewer must be a non-empty string when provided.")
    return _mutate(directory, case_id, from_version, "note",
                   note=note.strip(),
                   actor={"label": reviewer, "verified": False} if reviewer else None)


def transition(directory, case_id, from_version, to_status, note=None, reviewer=None):
    if to_status not in STATUSES:
        raise StoreError(f"Unknown status {to_status!r}.")
    case = _read_case(directory, case_id)
    current = case.get("status")
    if to_status not in ALLOWED_TRANSITIONS.get(current, ()):
        raise StoreError(
            f"Transition {current!r} -> {to_status!r} is not allowed. "
            f"Terminal states accept no transitions.", status=422)
    return _mutate(directory, case_id, from_version, "transition",
                   to_status=to_status, note=note,
                   actor={"label": reviewer, "verified": False} if reviewer else None)
