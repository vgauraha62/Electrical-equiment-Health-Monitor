"""Generate the small runtime ledger review record (stdlib only, deterministic).

The full lineage inventory is a development artifact and is never shipped, so the
deployed runtime carries a small purpose-built record instead: the single source
of the recorded ledger translation review. This script derives that record from
the *recorded* ledger review metadata in the lineage inventory plus the exact
translated catalog hash, and refuses to write anything unless that recorded
metadata is an allowed approval for the ledger translation only.

It never invents or broadens an approval, never resolves an evidence-register
item, and re-validates the produced record with the same runtime validator the
deployed app uses, so a generated artifact always passes at runtime.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import agent_contract as contract  # noqa: E402

LINEAGE_PATH = ROOT / "data/output/lineage_inventory.json"
CATALOG_PATH = ROOT / "data/output/equipment_catalog.json"
OUT_PATH = ROOT / "data/output/ledger_review_record.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recorded_ledger_review(lineage_payload):
    """The recorded ledger review entry from a lineage payload, or ``None``."""
    for source in lineage_payload.get("sources", []):
        if isinstance(source, dict) and "equipment ledger" in str(
                source.get("family") or "").lower():
            return source
    return None


def build_record(lineage_payload, catalog_sha256):
    """The runtime record for a recorded lineage review, or ``SystemExit``.

    Refuses to build unless the recorded metadata is exactly an allowed approval
    for the ledger translation, so the generator can never invent or broaden an
    approval. The produced record is re-validated with the runtime validator.
    """
    entry = recorded_ledger_review(lineage_payload)
    if entry is None:
        raise SystemExit("no recorded equipment-ledger review entry in the lineage inventory")
    review = entry.get("translation_review")
    if (entry.get("translation_status") != "reviewed"
            or entry.get("review_status") != "approved"
            or not isinstance(review, dict)
            or review.get("decision") != contract.LEDGER_REVIEW_DECISION
            or review.get("scope") != contract.LEDGER_REVIEW_SCOPE
            or list(review.get("excludes") or []) != list(contract.LEDGER_REVIEW_EXCLUDES)):
        raise SystemExit("recorded ledger review is not an allowed approval; refusing to write")
    record = {
        "record_version": contract.LEDGER_REVIEW_RECORD_VERSION,
        "source_id": contract.LEDGER_SOURCE_ID,
        "catalog_sha256": catalog_sha256,
        "translation_status": "reviewed",
        "review_status": "approved",
        "review": {
            "decision": review["decision"],
            "reviewed_at": review["reviewed_at"],
            "reviewed_by": review["reviewed_by"],
            "reviewer_role": review["reviewer_role"],
            "scope": review["scope"],
        },
        "excludes": list(contract.LEDGER_REVIEW_EXCLUDES),
    }
    if contract.ledger_review_record(record, catalog_sha256) is None:
        raise SystemExit("generated record failed runtime validation; refusing to write")
    return record


def main():
    lineage = json.loads(LINEAGE_PATH.read_text(encoding="utf-8"))
    catalog_sha256 = sha256_file(CATALOG_PATH)
    record = build_record(lineage, catalog_sha256)
    OUT_PATH.write_text(
        json.dumps(record, ensure_ascii=True, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    print(f"catalog_sha256={catalog_sha256}")


if __name__ == "__main__":
    main()
