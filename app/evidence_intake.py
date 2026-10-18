"""Evidence-intake store (stdlib only, file-based).

Receives external evidence responses without promoting anything: intake status
tracks handling, reviewer status tracks review, and neither changes the
evidence register (register changes only through the existing acceptance
criteria plus recorded human approval, applied manually).

Submitted file bytes are immutable once stored: placement verifies the declared
sha256, rewrites are rejected, and translations (if any) live beside the
original, marked draft, with source locators. No personal data is solicited;
submitter strings are stored verbatim and never rendered as verified identity.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("received", "intake_validated", "needs_clarification",
            "ready_for_review", "accepted", "rejected")
ALLOWED_TRANSITIONS = {
    "received": ("intake_validated", "rejected"),
    "intake_validated": ("ready_for_review", "needs_clarification", "rejected"),
    "needs_clarification": ("intake_validated", "rejected"),
    "ready_for_review": ("accepted", "rejected", "needs_clarification"),
    "accepted": (),
    "rejected": (),
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE_BYTES = 5 * 1024 * 1024


class IntakeError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def store_dir(root):
    directory = Path(os.getenv("EVIDENCE_INTAKE_DIR", root / "data/output/evidence_intake"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _path(directory, submission_id):
    if not submission_id or "/" in submission_id or submission_id.startswith("."):
        raise IntakeError("Invalid submission ID.", status=404)
    return directory / f"{submission_id}.json"


def _write_atomic(path, document):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True)
                   + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read(directory, submission_id):
    try:
        return json.loads(_path(directory, submission_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise IntakeError("Submission not found.", status=404)
    except ValueError:
        raise IntakeError("Submission record is corrupt.", status=500)


def register_submission(directory, evidence_request_id, files=None, idempotency_key=None,
                        submission_source=None, source_date=None, asset_context=None,
                        measurement_context=None, stated_interpretation=None,
                        stated_uncertainty=None):
    """Register submission metadata. Files are descriptors; bytes arrive separately."""
    if not isinstance(evidence_request_id, str) or not evidence_request_id.strip():
        raise IntakeError("evidence_request_id is required as a non-empty string.")
    file_entries = []
    for entry in files or []:
        if not isinstance(entry, dict):
            raise IntakeError("Each file entry must be an object.")
        name = entry.get("original_filename")
        digest = entry.get("sha256")
        if not isinstance(name, str) or not name.strip() or "/" in name \
                or name.startswith("."):
            raise IntakeError("Each file needs a safe original_filename.")
        if not isinstance(digest, str) or not SHA_RE.fullmatch(digest):
            raise IntakeError("Each file needs a sha256 hex digest.")
        file_entries.append({"original_filename": name.strip(), "sha256": digest,
                             "source_locator": entry.get("source_locator"),
                             "received": False})
    if idempotency_key:
        for path in sorted(directory.glob("*.json")):
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if existing.get("idempotency_key") == idempotency_key:
                return existing, False
    submission_id = str(uuid.uuid4())
    stamp = now_iso()
    submission = {
        "submission_id": submission_id,
        "evidence_request_id": evidence_request_id.strip(),
        "submission_source": submission_source,
        "source_date": source_date,
        "received_at": stamp,
        "received_timezone": "UTC",
        "files": file_entries,
        "asset_context": asset_context,
        "measurement_context": measurement_context,
        "stated_interpretation": stated_interpretation,
        "stated_uncertainty": stated_uncertainty,
        "intake_status": "received",
        "reviewer_status": "not_reviewed",
        "idempotency_key": idempotency_key,
        "created_at": stamp,
        "updated_at": stamp,
        "history": [{"seq": 1, "at": stamp, "event": "received",
                     "from_status": None, "to_status": "received"}],
        "schema_version": "1.0.0",
    }
    _write_atomic(_path(directory, submission_id), submission)
    return submission, True


def attach_file(directory, submission_id, filename, content_base64):
    """Store file bytes immutably after verifying the declared checksum."""
    submission = _read(directory, submission_id)
    if submission["intake_status"] not in ("received", "needs_clarification"):
        raise IntakeError("Files can only be attached while intake is open.", status=422)
    target = next((f for f in submission["files"]
                   if f["original_filename"] == filename), None)
    if target is None:
        raise IntakeError("Filename was not declared at registration.", status=422)
    if target.get("received"):
        raise IntakeError("File already stored; stored bytes are immutable.", status=409)
    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception:
        raise IntakeError("content_base64 is not valid base64.")
    if len(content) > MAX_FILE_BYTES:
        raise IntakeError("File exceeds the 5 MB intake limit.", status=413)
    digest = hashlib.sha256(content).hexdigest()
    if digest != target["sha256"]:
        raise IntakeError("Checksum mismatch: bytes do not match the declared sha256.",
                          status=422)
    files_dir = directory / submission_id
    files_dir.mkdir(parents=True, exist_ok=True)
    blob_path = files_dir / filename
    if blob_path.exists():
        raise IntakeError("File already stored; stored bytes are immutable.", status=409)
    blob_path.write_bytes(content)
    target["received"] = True
    stamp = now_iso()
    submission["updated_at"] = stamp
    submission["history"].append({"seq": len(submission["history"]) + 1, "at": stamp,
                                  "event": "file_attached", "from_status": None,
                                  "to_status": submission["intake_status"],
                                  "filename": filename, "sha256": digest})
    _write_atomic(_path(directory, submission_id), submission)
    return submission


def transition(directory, submission_id, to_status, note=None):
    submission = _read(directory, submission_id)
    current = submission["intake_status"]
    if to_status not in STATUSES:
        raise IntakeError(f"Unknown intake status {to_status!r}.")
    if to_status not in ALLOWED_TRANSITIONS.get(current, ()):
        raise IntakeError(f"Transition {current!r} -> {to_status!r} not allowed; "
                          f"accepted/rejected are terminal.", status=422)
    if to_status == "accepted":
        missing = [f["original_filename"] for f in submission["files"]
                   if not f.get("received")]
        if missing:
            raise IntakeError(f"Cannot accept: {len(missing)} declared file(s) missing.",
                              status=422)
    stamp = now_iso()
    submission["intake_status"] = to_status
    submission["updated_at"] = stamp
    submission["history"].append({"seq": len(submission["history"]) + 1, "at": stamp,
                                  "event": "transition", "from_status": current,
                                  "to_status": to_status, "note": note})
    _write_atomic(_path(directory, submission_id), submission)
    return submission


def get_submission(directory, submission_id):
    return _read(directory, submission_id)


def list_submissions(directory, request_id=None, intake_status=None):
    found = []
    for path in sorted(directory.glob("*.json")):
        try:
            submission = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if request_id is not None and submission.get("evidence_request_id") != request_id:
            continue
        if intake_status is not None and submission.get("intake_status") != intake_status:
            continue
        found.append({k: submission.get(k) for k in
                      ("submission_id", "evidence_request_id", "intake_status",
                       "reviewer_status", "created_at", "updated_at")})
    return found
