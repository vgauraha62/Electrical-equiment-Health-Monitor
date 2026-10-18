"""Deterministic, versioned agent context bundle (stdlib only, read-only).

Builds the evidence bundle the agent draft is produced and validated against.
Nothing here writes files, calls a model, or touches cloud services. Every
evidence item keeps an exact source id, source hash, locator, and uncertainty
status; missing provenance is preserved and marked, never hidden or fabricated.

The bundle is a pure function of its inputs: identical inputs yield an identical
``bundle_digest_sha256`` (the digest covers everything except itself).
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

try:
    from . import agent_contract as contract
except ImportError:  # Supports `python app/main.py` during local preview.
    import agent_contract as contract

try:
    from . import equipment as equipment_directory
except ImportError:
    import equipment as equipment_directory

ROOT = Path(__file__).resolve().parents[1]

TARGET_EQUIPMENT_TYPE = equipment_directory.TRANSFORMER_TYPE
REQUIRED_TRANSFORMER_CODES = {
    "A_phsA", "A_phsB", "A_phsC",
    "PhV_phsA", "PhV_phsB", "PhV_phsC",
    "PhW_phsA", "PhW_phsB", "PhW_phsC",
    "PhVAr_phsA", "PhVAr_phsB", "PhVAr_phsC",
    "TotW", "TotVar",
}
REACTIVE_CODES = {"PhVAr_phsA", "PhVAr_phsB", "PhVAr_phsC", "TotVar"}
LEDGER_PROFILE_FIELDS = (
    "ast_type", "ast_type_meaning", "voltage_level_en", "psr_state_en",
    "manufacturer", "model", "insulation_medium", "insulation_medium_en",
    "operate_date_iso", "manufacture_date_iso",
)
LEDGER_WORKBOOK_FALLBACK = "equipment_ledger.xlsx"
# Shipped telemetry exports. The runtime manifest is only trusted when each
# recorded sha256 matches the actual file here.
TELEMETRY_DIR = Path("data/input/telemetry")
# Source-metadata register items that map to a ledger column or a raw export key.
_META_KEY_BY_REGISTER = {
    "EV-META-astId": ("ast_id", "ledger"),
    "EV-META-astType": ("ast_type", "ledger"),
    "EV-META-consNo": ("cons_no", "rows"),
    "EV-META-consType": ("cons_type", "rows"),
    "EV-META-pt": ("pt", "rows"),
    "EV-META-ct": ("ct", "rows"),
    "EV-META-tFactor": ("t_factor", "rows"),
}
_REPRESENTATIVE_LIMIT = 8
# Grouping metadata for the repeated per-code unit/scaling questions. Each code
# keeps its own register id, question, and status; the shared group id lets a
# consumer recognise the 14 items as one unit/scaling topic without collapsing
# them.
GROUP_UNITS_SCALING_ID = "grp_units_scaling"
GROUP_UNITS_SCALING_LABEL = "Measurement unit and scaling (per measurement code)"
# Ledger field translations are recorded in a small runtime review record (the
# full lineage inventory is a development artifact and is never shipped). When
# that record is absent, malformed, stale, or off-scope the status fails closed
# to unreviewed, and translated field meanings never support a claim or an
# equipment-context conclusion.
LEDGER_TRANSLATION_FALLBACK = {
    "translation_status": "unreviewed",
    "review_status": "unreviewed",
    "recorded": False,
}
LEDGER_SOURCE_ID = contract.LEDGER_SOURCE_ID
LEDGER_REVIEW_RECORD_PATH = "data/output/ledger_review_record.json"
LEDGER_CATALOG_PATH = "data/output/equipment_catalog.json"
# The ledger fields carried as equipment context, as (context field, raw column,
# translated/derived column). The raw column is the recorded code/value; the
# translated column (when present) is the upstream English or ISO rendering. No
# field may support an equipment-context conclusion until the recorded ledger
# translation review is reviewed+approved (see contract.ledger_translation_reviewed).
EQUIPMENT_CONTEXT_FIELDS = (
    ("equipment_type", "ast_type", "ast_type_meaning"),
    ("voltage_level", "voltage_level", "voltage_level_en"),
    ("equipment_state", "psr_state", "psr_state_en"),
    ("insulation_medium", "insulation_medium", "insulation_medium_en"),
    ("manufacturer", "manufacturer", None),
    ("model", "model", None),
    ("station", "station", None),
    ("line", "line", None),
    ("pole", "pole", None),
    ("operate_date", "operate_date", "operate_date_iso"),
    ("manufacture_date", "manufacture_date", "manufacture_date_iso"),
)


def _main_module():
    try:
        from . import main as main_module
    except ImportError:
        import main as main_module
    return main_module


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_manifest(root):
    """Verified telemetry source manifest: ``{source_file_id: sha256}``.

    Each recorded sha256 is trusted only when it matches the actual shipped
    telemetry file under ``data/input/telemetry``. An absent or malformed manifest
    yields no entries, and an entry whose file is missing, unreadable, or of a
    different hash is dropped, so unverified provenance fails closed to
    ``incomplete`` instead of being trusted from the record alone.
    """
    payload = _read_json(Path(root) / "data/output/manifest.json")
    if not isinstance(payload, dict):
        payload = {}
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return {}
    manifest = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        file_id = source.get("file")
        sha = source.get("sha256")
        if not isinstance(file_id, str) or not file_id or not _sha_ok(sha):
            continue
        if _sha256_file(Path(root) / TELEMETRY_DIR / file_id) == sha:
            manifest[file_id] = sha
    return manifest


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def load_ledger_review(root):
    """The recorded+validated ledger translation review, or ``None``.

    Reads the small runtime review record and validates it against the exact
    translated catalog it must cover. A missing, malformed, stale, or off-scope
    record returns ``None`` so the equipment-ledger context can never be used
    unreviewed. The full lineage inventory is a development artifact and is not
    read here; local and deployed runtime behaviour use this same record.
    """
    record = _read_json(Path(root) / LEDGER_REVIEW_RECORD_PATH)
    catalog_sha256 = _sha256_file(Path(root) / LEDGER_CATALOG_PATH)
    return contract.ledger_review_record(record, catalog_sha256)


def load_ledger_translation(root):
    """Recorded translation/review status for the equipment ledger translation.

    If no valid record is found the status fails closed to unreviewed so an
    unreviewed translated field can never support a claim or equipment-context
    conclusion.
    """
    review = load_ledger_review(root)
    if review is None:
        return dict(LEDGER_TRANSLATION_FALLBACK)
    return {
        "translation_status": review["translation_status"],
        "review_status": review["review_status"],
        "recorded": True,
    }


def load_chunks(path):
    """Load an approved-chunk store. Absent/corrupt store is a valid empty state."""
    payload = _read_json(path)
    if payload is None:
        return []
    if isinstance(payload, dict):
        chunks = payload.get("chunks", [])
    else:
        chunks = payload
    return [chunk for chunk in chunks if isinstance(chunk, dict)]


def retrieve_approved(chunks, equipment_type_code=TARGET_EQUIPMENT_TYPE):
    """Same predicate as app.main._knowledge: approved + english + reviewed."""
    return [chunk for chunk in chunks
            if chunk.get("status") == "approved"
            and chunk.get("language") == "en"
            and chunk.get("translation_status") == "reviewed"
            and chunk.get("equipment_type_code") in (equipment_type_code, "all")]


def _sha_ok(value):
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value)


def _telemetry_locator(row):
    return {
        "kind": "telemetry",
        "source_file_id": row.get("source_file_id"),
        "record_index": row.get("source_record_index"),
        "measurement_point_id": row.get("measurement_point_id"),
        "measurement_type_code": row.get("measurement_type_code"),
        "observed_at": row.get("observed_at"),
        "dataset_type": row.get("dataset_type"),
    }


def _ledger_locator(catalog_source, key):
    workbook = catalog_source.get("path") or LEDGER_WORKBOOK_FALLBACK
    return {
        "kind": "ledger",
        "workbook": Path(str(workbook)).name,
        "sheet": catalog_source.get("sheet") or "unknown",
        "key": f"psr_id={key}",
    }


def _knowledge_locator(chunk):
    return {
        "kind": "knowledge",
        "source_id": chunk.get("source_id"),
        "source_locator": chunk.get("source_locator"),
        "source_version": chunk.get("source_version"),
    }


def _representative_ids(items, limit=_REPRESENTATIVE_LIMIT):
    ordered = sorted(items, key=lambda item: (item.get("observed_at") or "",
                                              item.get("evidence_id") or ""))
    return [item["evidence_id"] for item in ordered[:limit]]


def _evidence_item(row, manifest):
    source_file_id = row.get("source_file_id")
    sha = manifest.get(source_file_id)
    complete = _sha_ok(sha)
    return {
        "evidence_id": row.get("evidence_id"),
        "evidence_type": "telemetry",
        "source_file_id": source_file_id,
        "source_record_index": row.get("source_record_index"),
        "measurement_point_id": row.get("measurement_point_id"),
        "measurement_type_code": row.get("measurement_type_code"),
        "dataset_type": row.get("dataset_type"),
        "observed_at": row.get("observed_at") or "",
        "value": row.get("measurement_value"),
        "data_origin": "observed",
        "source_sha256": sha if complete else None,
        "provenance_status": "complete" if complete else "incomplete",
        "provenance_gap": not complete,
        "locator": _telemetry_locator(row),
        "unit_status": "unconfirmed",
        "timestamp_timezone_status": "unconfirmed",
        "translation_status": "not_applicable",
        "review_status": "not_applicable",
    }


def _ledger_item(ledger, catalog_source, key, translation=None):
    translation = translation or LEDGER_TRANSLATION_FALLBACK
    sha = catalog_source.get("sha256")
    complete = _sha_ok(sha)
    locator = _ledger_locator(catalog_source, key)
    evidence_id = "ev_ledger_" + hashlib.sha256(
        ("ledger|" + key).encode("utf-8")).hexdigest()[:20]
    return {
        "evidence_id": evidence_id,
        "evidence_type": "ledger",
        "source_file_id": locator["workbook"],
        "source_record_index": None,
        "measurement_point_id": None,
        "measurement_type_code": None,
        "dataset_type": "ledger",
        "observed_at": "",
        "value": None,
        "data_origin": "observed",
        "source_sha256": sha if complete else None,
        "provenance_status": "complete" if complete else "incomplete",
        "provenance_gap": not complete,
        "locator": locator,
        "unit_status": "not_applicable",
        "timestamp_timezone_status": "not_applicable",
        "translation_status": translation["translation_status"],
        "review_status": translation["review_status"],
    }


def _context_value(ledger, column):
    if ledger is None or column is None:
        return None
    value = ledger.get(column)
    if value is None or isinstance(value, bool):
        return None
    return str(value)


def _equipment_context(ledger, ledger_item, translation, register):
    """Ledger-derived equipment context, gated on the recorded translation review.

    Every field keeps its ledger locator, source hash, and the recorded
    translation/review status. ``usable_for_context`` is true only when the
    recorded review is reviewed+approved *and* the field carries a value; until
    then every field is carried but marked unusable, and the block reports why
    (``unmatched_ledger_id``, ``unreviewed_translation``, or
    ``no_usable_context_fields``). The validator re-derives all of this, so a
    forged ``usable_for_context`` cannot survive validation.
    """
    recorded = translation or LEDGER_TRANSLATION_FALLBACK
    translation_status = recorded["translation_status"]
    review_status = recorded["review_status"]
    matched = ledger_item is not None
    locator = dict(ledger_item["locator"]) if ledger_item else None
    sha = ledger_item["source_sha256"] if ledger_item else None
    reviewed = matched and contract.ledger_translation_reviewed(
        translation_status, review_status)
    fields = []
    if matched:
        for field, raw_column, translated_column in EQUIPMENT_CONTEXT_FIELDS:
            value = _context_value(ledger, raw_column)
            usable = reviewed and value not in (None, "")
            fields.append({
                "field": field,
                "column": raw_column,
                "value": value,
                "translated": _context_value(ledger, translated_column),
                "locator": dict(locator),
                "source_sha256": sha,
                "translation_status": translation_status,
                "review_status": review_status,
                "usable_for_context": bool(usable),
                "blocked_reason_code": (None if usable else
                                        ("unreviewed_translation" if not reviewed
                                         else "missing_value")),
            })
    usable_any = any(entry["usable_for_context"] for entry in fields)
    if not matched:
        blocked = "unmatched_ledger_id"
    elif not reviewed:
        blocked = "unreviewed_translation"
    elif not usable_any:
        blocked = "no_usable_context_fields"
    else:
        blocked = None
    return {
        "source_id": LEDGER_SOURCE_ID,
        "matched": matched,
        "evidence_id": ledger_item["evidence_id"] if matched else None,
        "locator": locator,
        "source_sha256": sha,
        "translation_status": translation_status,
        "review_status": review_status,
        "review_recorded": bool(recorded.get("recorded")),
        "context_usable": bool(matched and reviewed and usable_any),
        "blocked_reason_code": blocked,
        "status_label": contract.equipment_context_label(matched, reviewed, usable_any),
        "register_binding": contract.equipment_register_binding(register),
        "dependent_unknowns": contract.derive_context_dimensions(register),
        "fields": fields,
    }


def _provenance_entry(item):
    return {
        "evidence_type": item["evidence_type"],
        "source_file_id": item["source_file_id"],
        "source_sha256": item["source_sha256"],
        "provenance_status": item["provenance_status"],
        "locator": item["locator"],
        "value": item["value"],
        "measurement_type_code": item["measurement_type_code"],
        "observed_at": item["observed_at"],
        "dataset_type": item["dataset_type"],
    }


def _gap(gap_id, kind, statement, evidence_ids=None, register_id=None, detail=None,
         search_scope=None, documentation_scope=None):
    return {
        "gap_id": gap_id,
        "kind": kind,
        "statement": statement,
        "evidence_ids": sorted(evidence_ids or []),
        "register_id": register_id,
        "detail": detail,
        "search_scope": search_scope,
        "documentation_scope": documentation_scope,
    }


def _record_set_scope(telemetry_items, scope_id, noun):
    """Name the complete telemetry record set a gap searched.

    An absence claim must identify the complete inventory/record set it examined.
    When the set is empty the information is reported as unavailable rather than
    inferred from a sample.
    """
    ids = sorted(item["evidence_id"] for item in telemetry_items)
    if not ids:
        return {
            "scope_id": scope_id,
            "description": f"the complete set of {noun} is unavailable",
            "status": "unavailable",
            "record_count": None,
            "inventory_digest_sha256": None,
            "source_files": [],
        }
    return {
        "scope_id": scope_id,
        "description": (f"all {len(ids)} supplied telemetry record(s) for this asset "
                        f"({noun})"),
        "status": "enumerated",
        "record_count": len(ids),
        "inventory_digest_sha256": contract.content_digest(ids),
        "source_files": sorted({item["source_file_id"] for item in telemetry_items}),
    }


def _unavailable_scope(scope_id, description):
    """A named inventory the bundle does not retain: reported, never enumerated."""
    return {
        "scope_id": scope_id,
        "description": description,
        "status": "unavailable",
        "record_count": None,
        "inventory_digest_sha256": None,
        "source_files": [],
    }


def _manifest_scope(manifest):
    """Name the complete manifest source list retained in the bundle.

    When the bundle retains no manifest entries the inventory is reported as
    unavailable rather than claimed absent.
    """
    entries = [{"source_file_id": file_id, "sha256": manifest[file_id]}
               for file_id in sorted(manifest)]
    if not entries:
        return _unavailable_scope(
            "manifest_sources",
            "the complete manifest source list is not retained in this bundle")
    return {
        "scope_id": "manifest_sources",
        "description": f"the complete manifest source list ({len(entries)} entry/entries)",
        "status": "enumerated",
        "record_count": len(entries),
        "inventory_digest_sha256": contract.content_digest(entries),
        "source_files": [entry["source_file_id"] for entry in entries],
    }


def _documentation_scope(subject, description):
    """Report documentation status without claiming documentation absence.

    No authoritative field dictionary or specification is retained in the bundle,
    so documentation status is reported ``unavailable``: the interpretation
    question stays unresolved and no documentation-absence claim is made.
    """
    return {
        "subject": subject,
        "status": "unavailable",
        "description": description,
        "inventory_source": None,
    }


def _questions(register, telemetry_items, ledger_item, ledger, rows, translation):
    """Build the register-backed unresolved-question list with machine conditions.

    Each question carries:
    - ``applies``: the register question is relevant to the supplied context;
    - ``evidence_specific``: the *cited* evidence specifically supports why it
      applies, as opposed to a dataset/class-level absence that no supplied
      record can demonstrate. Only evidence-specific items may become
      suggestions (see ``agent_draft._suggestions``); class-level items stay here
      with their register id and verbatim question;
    - ``group_id``/``group_label``: shared-topic metadata for the repeated
      per-code unit/scaling questions. Every code keeps its own id, question, and
      status; nothing is collapsed.

    The acquisition package is intentionally NOT read at runtime: its only field
    the runtime would use is ``requests[].evidence_id``, which equals the register
    item id for every item, so ``acquisition_evidence_id`` is the register id.
    """
    by_code = defaultdict(list)
    for item in telemetry_items:
        by_code[item["measurement_type_code"]].append(item)
    present_codes = set(by_code)
    asset_rep = _representative_ids(telemetry_items)
    ledger_rep = [ledger_item["evidence_id"]] if ledger_item else asset_rep
    translation = translation or LEDGER_TRANSLATION_FALLBACK

    def meta_reason(key, scope):
        if scope == "ledger":
            value = (ledger or {}).get(key)
            if value in (None, ""):
                return None
            return [f"ledger record supplies {key}={value}",
                    "the meaning of this source key is not documented"]
        rows_with = [row for row in rows if row.get(key) not in (None, "")]
        if not rows_with:
            return None
        return [f"supplied telemetry records carry the optional key {key}",
                "the meaning of this source key is not documented"]

    questions = []
    for item in register.get("items", []):
        if not isinstance(item, dict) or item.get("status") == "verified":
            continue
        register_id = item.get("id")
        topic = item.get("topic")
        code = item.get("measurement_code")
        applies = False
        evidence_specific = False
        reason_supported = False
        because = []
        related = []
        group_id = None
        group_label = None
        if topic == "measurement_code":
            if code in present_codes:
                applies = True
                evidence_specific = True
                group_id = GROUP_UNITS_SCALING_ID
                group_label = GROUP_UNITS_SCALING_LABEL
                related = [entry["evidence_id"] for entry in by_code[code]]
                because = [f"measurement code {code} is present with "
                           f"{len(by_code[code])} supplied observation(s)",
                           "measurement_unit is not supplied in the raw export"]
        elif register_id == "EV-TIME-TIMEZONE":
            # Dataset-level property: no supplied record can demonstrate the
            # absence of an offset, so this is not evidence-specific.
            if telemetry_items:
                applies = True
                related = asset_rep
                because = ["timestamps are supplied as wall-clock strings with no offset",
                           "timestamp_timezone_status is unconfirmed"]
        elif register_id == "EV-TIME-INTERVAL":
            has_meta = any("time_interval" in row or "time_unit" in row for row in rows)
            applies = has_meta
            evidence_specific = has_meta
            related = asset_rep
            because = (["supplied records carry timeInterval/timeUnit metadata",
                        "the sampling cadence is not committed"] if has_meta else [])
        elif register_id == "EV-ASSET-IDENTITY":
            applies = bool(telemetry_items)
            evidence_specific = True
            reason_supported = True
            related = ledger_rep if ledger_item else asset_rep
            because = ["telemetry rows carry this identifier",
                       "ledger match status: " + ("exact ID string match on psr_id"
                                                  if ledger_item else "no exact match")]
        elif register_id == "EV-ASSET-OIL":
            # The ledger construction/insulation field is a translation whose
            # review status is recorded. A reviewed translation makes the field
            # usable context, but the construction question itself stays open
            # until its register item is verified.
            if ledger_item is not None:
                applies = True
                evidence_specific = True
                reason_supported = True
                related = [ledger_item["evidence_id"]]
                reviewed = contract.ledger_translation_reviewed(
                    translation["translation_status"], translation["review_status"])
                because = [
                    "ledger record carries a construction/insulation field "
                    "translated upstream",
                    "recorded translation/review status: "
                    f"translation_status={translation['translation_status']}, "
                    f"review_status={translation['review_status']}",
                    ("the translated meaning is recorded as reviewed, but this "
                     "register item is not verified, so the construction question "
                     "stays open" if reviewed else
                     "an unreviewed translated meaning cannot support an "
                     "equipment-context conclusion"),
                ]
        elif register_id in _META_KEY_BY_REGISTER:
            key, scope = _META_KEY_BY_REGISTER[register_id]
            reason = meta_reason(key, scope)
            if reason:
                applies = True
                evidence_specific = True
                related = ([ledger_item["evidence_id"]] if scope == "ledger"
                           and ledger_item else asset_rep)
                because = reason
        elif register_id == "EV-ZERO-READINGS":
            zero_items = [entry for entry in telemetry_items
                          if isinstance(entry["value"], (int, float))
                          and not isinstance(entry["value"], bool)
                          and entry["value"] == 0]
            if zero_items:
                applies = True
                evidence_specific = True
                reason_supported = True
                related = _representative_ids(zero_items)
                because = [f"{len(zero_items)} supplied finite value(s) equal zero",
                           "zero semantics are not confirmed"]
        elif register_id == "EV-REACTIVE-SIGN":
            negative = [entry for entry in telemetry_items
                        if entry["measurement_type_code"] in REACTIVE_CODES
                        and isinstance(entry["value"], (int, float))
                        and not isinstance(entry["value"], bool)
                        and entry["value"] < 0]
            if negative:
                applies = True
                evidence_specific = True
                reason_supported = True
                related = _representative_ids(negative)
                because = [f"{len(negative)} supplied reactive-power value(s) are negative",
                           "the sign convention is not confirmed"]
        elif register_id == "EV-RULES-APPROVED":
            # Class-level absence of approved rules: no supplied record can
            # demonstrate it, so this is not evidence-specific.
            applies = bool(telemetry_items)
            related = asset_rep
            because = ["no approved engineering rules are recorded for this equipment class"]
        elif register_id == "EV-OUTCOMES-LABELED":
            # Class-level absence of verified outcomes: not evidence-specific.
            applies = bool(telemetry_items)
            related = asset_rep
            because = ["no verified outcome labels are recorded for this asset"]
        elif register_id == "EV-COVERAGE-EVAL":
            # Programme-level coverage question: not evidence-specific.
            applies = bool(telemetry_items)
            related = asset_rep
            because = ["coverage and evaluation configuration is not established"]
        question = item.get("question")
        if not isinstance(question, str) or not question:
            continue
        questions.append({
            "question_id": register_id,
            "register_id": register_id,
            "question": question,
            "domain": topic,
            "status": item.get("status"),
            "owner_role": item.get("reviewer_required"),
            "applicability": item.get("applicability"),
            "blocks": list(item.get("blocks", [])),
            "measurement_code": code,
            "acquisition_evidence_id": register_id,
            "applies": bool(applies),
            "evidence_specific": bool(evidence_specific),
            "reason_supported": bool(reason_supported),
            "applies_because": list(because),
            "related_evidence_ids": sorted(set(related)),
            "group_id": group_id,
            "group_label": group_label,
            "verification_action": contract.topic_verification_action(topic)
            or "confirm_from_cited_source",
        })
    return sorted(questions, key=lambda question: question["question_id"])


def _knowledge_parts(chunks, equipment_type_code):
    """Return (knowledge_index, approved_items, gaps) for the chunk store."""
    index = {}
    items = []
    gaps = []
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            continue
        in_scope = chunk.get("equipment_type_code") in (equipment_type_code, "all")
        approved = (chunk.get("status") == "approved" and chunk.get("language") == "en"
                    and chunk.get("translation_status") == "reviewed")
        if approved and in_scope and _sha_ok(chunk.get("source_sha256")):
            locator = _knowledge_locator(chunk)
            index[chunk_id] = {
                "review_status": "approved",
                "translation_status": "reviewed",
                "source_id": chunk.get("source_id"),
                "source_uri": chunk.get("source_uri"),
                "source_sha256": chunk.get("source_sha256"),
                "source_version": chunk.get("source_version"),
                "locator": locator,
            }
            items.append({
                "chunk_id": chunk_id,
                "title": chunk.get("title"),
                "source_id": chunk.get("source_id"),
                "source_uri": chunk.get("source_uri"),
                "source_locator": chunk.get("source_locator"),
                "source_version": chunk.get("source_version"),
                "translation_status": "reviewed",
                "review_status": "approved",
                "source_sha256": chunk.get("source_sha256"),
                "provenance_status": "complete",
            })
            continue
        if not in_scope:
            continue
        if approved:
            gaps.append(_gap(
                f"gap_kn_{chunk_id}", "missing_knowledge_provenance",
                f"approved chunk {chunk_id} has no verifiable source hash; the complete "
                "chunk-store inventory is not retained in this bundle, so its search "
                "scope is unavailable and the chunk cannot support a claim.",
                register_id=None, detail={"chunk_id": chunk_id},
                search_scope=_unavailable_scope(
                    "approved_chunk_store",
                    "the complete approved-chunk store is not retained in this bundle")))
        elif chunk.get("status") != "approved":
            gaps.append(_gap(f"gap_kn_{chunk_id}", "unapproved_knowledge",
                             f"chunk {chunk_id} is not approved and cannot support a claim.",
                             register_id=None, detail={"chunk_id": chunk_id}))
        elif chunk.get("translation_status") != "reviewed":
            gaps.append(_gap(f"gap_kn_{chunk_id}", "unreviewed_translation",
                             f"chunk {chunk_id} translation is not reviewed.",
                             register_id=None, detail={"chunk_id": chunk_id}))
    return index, items, gaps


def build_context_bundle(asset_id, *, root=None, rows=None, catalog=None,
                         manifest=None, chunks=None, approved_chunks_path=None,
                         equipment_type_code=TARGET_EQUIPMENT_TYPE):
    """Build the deterministic context bundle, or ``None`` when there is no evidence."""
    root = Path(root or ROOT)
    key = equipment_directory.normalize_id(asset_id)
    if key is None:
        return None
    if rows is None:
        rows = _main_module()._target_rows()
    scope = [row for row in rows
             if equipment_directory.normalize_id(row.get("psr_id")) == key]
    if not scope:
        return None
    if catalog is None:
        catalog = equipment_directory.load_catalog(root) or {"records": []}
    if manifest is None:
        manifest = load_manifest(root)
    if chunks is None:
        path = approved_chunks_path or os.getenv("APPROVED_CHUNKS_PATH") \
            or root / "data/output/approved_chunks.json"
        chunks = load_chunks(path)

    evidence_items = [_evidence_item(row, manifest) for row in scope]

    ledger = next((record for record in catalog.get("records", [])
                   if equipment_directory.normalize_id(record.get("psr_id")) == key), None)
    catalog_source = catalog.get("source") or {}
    ledger_translation = load_ledger_translation(root)
    register = _read_json(root / "docs/evidence_register.json", {}) or {}
    ledger_item = None
    if ledger is not None:
        ledger_item = _ledger_item(ledger, catalog_source, key, ledger_translation)
        evidence_items.append(ledger_item)
    evidence_items.sort(key=lambda item: item["evidence_id"])
    equipment_context = _equipment_context(ledger, ledger_item, ledger_translation,
                                           register)

    telemetry_items = [item for item in evidence_items
                       if item["evidence_type"] == "telemetry"]

    index = {item["evidence_id"]: _provenance_entry(item) for item in evidence_items}
    index_knowledge, approved_items, knowledge_gaps = _knowledge_parts(
        chunks, equipment_type_code)

    gaps = list(knowledge_gaps)

    incomplete_by_file = defaultdict(list)
    for item in evidence_items:
        if item["provenance_status"] == "incomplete":
            incomplete_by_file[item["source_file_id"]].append(item["evidence_id"])
    for file_id, ids in sorted(incomplete_by_file.items()):
        gaps.append(_gap(
            f"gap_hash_{file_id}", "missing_source_hash",
            f"{len(ids)} record(s) from {file_id} cannot support a claim: the file "
            "carries no manifest hash and the complete manifest source list does not "
            "contain it; provenance is incomplete.",
            evidence_ids=ids, detail={"source_file_id": file_id},
            search_scope=_manifest_scope(manifest)))

    if ledger_item is None:
        gaps.append(_gap(
            "gap_ledger_unmatched", "unmatched_ledger_id",
            "no exact ledger ID match; ledger identity claims are blocked. IDs are "
            "never fuzzy-matched; the complete ledger catalog inventory is not "
            "retained in this bundle, so its search scope is unavailable.",
            evidence_ids=[], detail={"asset_id": key},
            search_scope=_unavailable_scope(
                "ledger_catalog",
                "the complete equipment ledger catalog is not retained in this bundle")))
    else:
        translation_status = ledger_translation["translation_status"]
        review_status = ledger_translation["review_status"]
        if not contract.ledger_translation_reviewed(translation_status, review_status):
            gaps.append(_gap(
                "gap_ledger_translation", "unreviewed_translation",
                "ledger field translations are recorded as "
                f"translation_status={translation_status}, "
                f"review_status={review_status}; translated field meanings cannot "
                "support claims or equipment-context conclusions.",
                evidence_ids=[ledger_item["evidence_id"]],
                detail={"translation_status": translation_status,
                        "review_status": review_status,
                        "recorded": ledger_translation["recorded"]}))

    present_codes = {item["measurement_type_code"] for item in telemetry_items}
    record_scope = _record_set_scope(telemetry_items, "telemetry_record_set",
                                     "telemetry records")
    missing_codes = sorted(REQUIRED_TRANSFORMER_CODES - present_codes)
    if missing_codes:
        gaps.append(_gap(
            "gap_missing_codes", "missing_measurement_code",
            "expected transformer codes missing from the complete supplied record set "
            f"({record_scope['record_count']} record(s) examined): "
            + ", ".join(missing_codes),
            detail={"missing_measurement_code": missing_codes},
            search_scope=dict(record_scope)))

    if record_scope["status"] == "enumerated":
        unit_statement = (
            "no unit value is present across the complete supplied telemetry record set "
            f"searched ({record_scope['record_count']} record(s); scope "
            "telemetry_record_set), so magnitudes are raw recorded numbers and "
            "engineering interpretation is blocked. Unit/scaling documentation status: "
            "unavailable - no authoritative field dictionary or unit specification is "
            "present in the bundle, so no documentation-absence claim is made.")
        timezone_statement = (
            "no timestamp offset is present across the complete supplied telemetry "
            f"record set searched ({record_scope['record_count']} record(s); scope "
            "telemetry_record_set), so timestamps are wall-clock strings and no "
            "clock-time ordering is asserted. Timestamp timezone documentation status: "
            "unavailable - no authoritative timezone specification is present in the "
            "bundle, so no documentation-absence claim is made.")
    else:
        unit_statement = (
            "unit/scaling information is unavailable: the complete record set could not "
            "be enumerated, so absence is not inferred from sample readings. Unit/scaling "
            "documentation status: unavailable - no authoritative field dictionary or "
            "unit specification is present in the bundle, so no documentation-absence "
            "claim is made.")
        timezone_statement = (
            "timestamp timezone information is unavailable: the complete record set "
            "could not be enumerated, so absence is not inferred from sample readings. "
            "Timestamp timezone documentation status: unavailable - no authoritative "
            "timezone specification is present in the bundle, so no documentation-absence "
            "claim is made.")
    gaps.append(_gap(
        "gap_units", "unknown_units_scaling", unit_statement,
        search_scope=dict(record_scope),
        documentation_scope=_documentation_scope(
            "unit_scaling",
            "authoritative unit/scaling documentation (field dictionary or unit "
            "specification) is not retained in this bundle")))
    gaps.append(_gap(
        "gap_timezone", "unknown_timezone", timezone_statement,
        search_scope=dict(record_scope),
        documentation_scope=_documentation_scope(
            "timestamp_timezone",
            "authoritative timestamp timezone documentation is not retained in this "
            "bundle")))

    seen = {}
    conflict_ids = set()
    for item in telemetry_items:
        if not isinstance(item["value"], (int, float)) or isinstance(item["value"], bool):
            continue
        cell = (item["measurement_point_id"], item["measurement_type_code"],
                item["observed_at"], item["dataset_type"])
        if cell in seen and seen[cell][1] != item["value"]:
            conflict_ids.add(seen[cell][0])
            conflict_ids.add(item["evidence_id"])
        else:
            seen[cell] = (item["evidence_id"], item["value"])
    if conflict_ids:
        gaps.append(_gap(
            "gap_conflict", "conflicting_values",
            "the same point/code/timestamp carries two different recorded values; "
            "claims about this cell are blocked.",
            evidence_ids=sorted(conflict_ids)))

    gaps.sort(key=lambda gap: (gap["kind"], gap["gap_id"]))

    dataset_types = sorted({item["dataset_type"] for item in telemetry_items})
    observed = [item["observed_at"] for item in telemetry_items if item["observed_at"]]
    present_codes = sorted(present_codes)
    equipment_codes = sorted({row.get("equipment_type_code") for row in scope})
    scope_code = equipment_codes[0] if len(equipment_codes) == 1 else "mixed"
    asset_scope = {
        "asset_id": key,
        "equipment_type_code": scope_code,
        "observation_count": len(telemetry_items),
        "measurement_point_count": len({item["measurement_point_id"]
                                        for item in telemetry_items}),
        "measurement_code_count": len(present_codes),
        "first_observation": min(observed) if observed else "",
        "last_observation": max(observed) if observed else "",
        "dataset_types": dataset_types,
        "source_files": sorted({item["source_file_id"] for item in telemetry_items}),
        "ledger_match": {
            "matched": ledger_item is not None,
            "evidence_id": ledger_item["evidence_id"] if ledger_item else None,
            "locator": ledger_item["locator"] if ledger_item else None,
            "profile": ({field: ledger.get(field) for field in LEDGER_PROFILE_FIELDS}
                        if ledger is not None else None),
            "translation_status": ledger_translation["translation_status"]
            if ledger_item else None,
            "review_status": ledger_translation["review_status"]
            if ledger_item else None,
            "reason": ("exact ID string match in the equipment ledger; physical "
                       "identity not verified"
                       if ledger_item else "no exact ledger ID match; never fuzzy-matched"),
        },
    }

    descriptions = equipment_directory.describe_telemetry(scope)
    metrics = [{
        "measurement_type_code": code,
        "observation_count": entry["observation_count"],
        "minimum": entry["minimum"],
        "maximum": entry["maximum"],
        "mean": entry["mean"],
        "stdev_population": entry["stdev"],
        "zero_count": entry["zero_count"],
        "zero_proportion": entry["zero_proportion"],
    } for code, entry in sorted(descriptions.items())]

    questions = _questions(
        register, telemetry_items, ledger_item, ledger, scope, ledger_translation)

    input_hashes = {}
    for item in evidence_items:
        input_hashes[item["source_file_id"]] = item["source_sha256"]
    input_hashes = [{"source_file_id": file_id, "sha256": sha}
                    for file_id, sha in sorted(input_hashes.items())]
    manifest_sources = [{"source_file_id": file_id, "sha256": manifest[file_id]}
                        for file_id in sorted(manifest)]

    body = {
        "bundle_version": contract.CONTEXT_VERSION,
        "contract_version": contract.CONTRACT_VERSION,
        "asset_id": key,
        "data_origin": contract.DATA_ORIGIN,
        "asset_scope": asset_scope,
        "provenance_index": {"evidence": index, "knowledge": index_knowledge},
        "evidence_items": evidence_items,
        "descriptive_metrics": metrics,
        "known_gaps": gaps,
        "unresolved_questions": questions,
        "approved_knowledge": approved_items,
        "prohibitions": [dict(item) for item in contract.PROHIBITIONS],
        "equipment_context": equipment_context,
        "determinism": {
            "input_hashes": input_hashes,
            "manifest_sources": manifest_sources,
            "ordering": "sorted",
            "digest_excludes": [],
        },
    }
    bundle = dict(body)
    bundle["bundle_digest_sha256"] = contract.content_digest(body)
    return bundle
