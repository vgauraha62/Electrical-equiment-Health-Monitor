"""Read-only asset evidence profile (stdlib only, deterministic, no model).

This module is a thin, deterministic *projection* of the already-validated agent
context bundle (`app/agent_context.py::build_context_bundle`) into the shape the
existing equipment review experience can display: the ledger fields that are
recorded context, the single exact-ID link to the asset's telemetry, telemetry
observations, and the material that is honestly unavailable.

It adds no new source, no inference, and no conclusion. Every value it presents
is copied from the validated bundle (which itself copies from the supplied
records), carries its source/locator/hash, and remains gated by the recorded
ledger translation review. An exact ``psr_id`` string match is a data link only:
it never proves physical identity. Unsupported material (defect history, tests,
inspections, work orders) is reported as unavailable, and the five dependent
conclusions stay unresolved.

There is no model call, no cloud call, and nothing is written.
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from . import agent_contract as contract
    from . import agent_context as context
except ImportError:  # Supports `python app/main.py` during local preview.
    import agent_contract as contract
    import agent_context as context

PROFILE_VERSION = "asset_evidence.v1"
ROOT = Path(__file__).resolve().parents[1]

DEFECT_RECORDS_PATH = "data/en/defect_records_translated.json"
LINEAGE_PATH = "data/output/lineage_inventory.json"

LINK_CAVEAT = ("An exact ID-string match is a data link only; it does not prove "
               "physical identity.")
SOURCE_FAMILY_SCOPE_ID = "supplied_source_families"
DEFECT_SCOPE_ID = "defect_record_inventory"
LINK_STATUSES = ("linked", "blocked")
LINK_BLOCK_REASONS = ("unmatched_ledger_id", "incomplete_provenance")
_UNSUPPORTED_FAMILIES = (
    ("test_record", "test records"),
    ("inspection_record", "inspection records"),
    ("maintenance_work_order", "maintenance/work-order records"),
)

# --- allowed-key sets (this module owns the projection shape; it is not an
# evidence contract and is not mirrored by a JSON schema) --------------------

ASSET_EVIDENCE_KEYS = {
    "profile_version", "contract_version", "asset_id", "data_origin",
    "bundle_digest_sha256", "context", "link", "telemetry",
    "unsupported_evidence", "unresolved_conclusions", "caveats", "prohibitions",
}
CONTEXT_KEYS = {
    "source_id", "review_recorded", "translation_status", "review_status",
    "context_usable", "blocked_reason_code", "status_label",
    "translation_review", "record", "fields", "recorded_fields", "absent_fields",
    "recorded_count", "absent_count",
}
CONTEXT_RECORD_KEYS = {
    "evidence_id", "source_file_id", "locator", "source_sha256", "provenance_status",
}
CONTEXT_FIELD_KEYS = {
    "field", "column", "value", "translated", "recorded", "usable_for_context",
    "blocked_reason_code", "locator", "source_sha256", "provenance_status",
    "translation_status", "review_status",
}
LINK_KEYS = {
    "status", "matching_method", "key", "verification_status", "caveat",
    "blocked_reason_code", "ledger_endpoint", "telemetry_endpoint",
}
LEDGER_ENDPOINT_KEYS = {
    "evidence_id", "source_file_id", "locator", "source_sha256", "provenance_status",
}
TELEMETRY_ENDPOINT_KEYS = {
    "evidence_id", "record_count", "first_observation", "last_observation",
    "source_files", "provenance_status",
}
TELEMETRY_KEYS = {
    "observation_count", "measurement_point_count", "measurement_code_count",
    "first_observation", "last_observation", "dataset_types", "source_files",
    "descriptive_metrics", "metrics_scope",
}
METRICS_SCOPE_KEYS = {
    "evidence_type", "evidence_count", "inventory_digest_sha256", "source_files",
}
UNSUPPORTED_KEYS = {"evidence_type", "status", "statement", "scope", "candidate_links"}
UNSUPPORTED_REQUIRED_KEYS = UNSUPPORTED_KEYS - {"candidate_links"}
CANDIDATE_LINK_KEYS = {
    "source_file_id", "locator", "matching_method", "verification_status",
}
UNSUPPORTED_SCOPE_KEYS = {
    "scope_id", "description", "status", "record_count",
    "inventory_digest_sha256", "inventory_items",
}
CONCLUSION_KEYS = {"dimension", "status", "open_register_ids"}

CAVEATS = (
    "Ledger fields are recorded context. A reviewed translation makes the field "
    "readable; it does not assert physical identity, engineering-rule "
    "applicability, measurement units, time semantics, or outcome labels.",
    "Measurement units and scaling are not confirmed; values are raw recorded numbers.",
    "Timestamp timezone is not confirmed; no clock-time ordering is asserted.",
    "No approved engineering guidance or verified outcome labels are recorded; "
    "nothing here is an equipment assessment.",
)

# Narrow, conclusion-specific guard for profile prose. Unlike the agent-prose
# scan it must not fire on the honest availability wording (for example
# "maintenance/work-order records" or "inspection records").
PROHIBITED_CONCLUSION_PHRASES = (
    "failure probability", "probability of failure", "risk of failure",
    "health index", "remaining useful life", "remaining life", "imminent failure",
    "likely to fail", "should be replaced", "should be repaired", "must be repaired",
    "recommend replac", "recommend repair", "diagnos", "fault classification",
    "fault state",
)

# Sentinel distinguishing "load from disk" from an explicit ``None`` (absent).
_AUTO = object()


def _fail(code, message, path="$"):
    raise contract.AgentContractError(code, message, path)


def _strict(instance, allowed, required, path="$"):
    """Local copy of the contract's strict-object check (no private import)."""
    if not isinstance(instance, dict):
        _fail("bad_type", "expected object", path)
    unknown = sorted(set(instance) - set(allowed))
    if unknown:
        _fail("unknown_key", f"unexpected keys {unknown}", path)
    missing = sorted(set(required) - set(instance))
    if missing:
        _fail("missing_key", f"missing keys {missing}", path)


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _scan_conclusions(text, path):
    lowered = str(text).lower()
    for phrase in PROHIBITED_CONCLUSION_PHRASES:
        if phrase in lowered:
            _fail("prohibited_text", f"prohibited conclusion phrase {phrase!r}", path)


def _sha_ok(value):
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value)


# --- projections of the validated bundle -------------------------------------

def _ledger_review_metadata(root):
    """The validated ledger review metadata (review block + exclusions), or None.

    Sourced from the runtime review record via the same loader the equipment
    context gate uses, so local and deployed review provenance cannot diverge.
    """
    review = context.load_ledger_review(root)
    if review is None:
        return None
    return {**review["review"], "excludes": list(review["excludes"])}


def _endpoint_locator(item):
    return {
        "evidence_id": item["evidence_id"],
        "source_file_id": item["source_file_id"],
        "locator": item["locator"],
        "source_sha256": item["source_sha256"],
        "provenance_status": item["provenance_status"],
    }


def _context_block(bundle, translation_review):
    ec = bundle["equipment_context"]
    by_id = {item["evidence_id"]: item for item in bundle["evidence_items"]}
    record = None
    if ec["matched"]:
        record = _endpoint_locator(by_id[ec["evidence_id"]])
    fields = []
    for entry in ec["fields"]:
        fields.append({
            "field": entry["field"],
            "column": entry["column"],
            "value": entry["value"],
            "translated": entry["translated"],
            "recorded": entry["value"] not in (None, ""),
            "usable_for_context": entry["usable_for_context"],
            "blocked_reason_code": entry["blocked_reason_code"],
            "locator": entry["locator"],
            "source_sha256": entry["source_sha256"],
            "provenance_status": ("complete" if _sha_ok(entry["source_sha256"])
                                  else "incomplete"),
            "translation_status": entry["translation_status"],
            "review_status": entry["review_status"],
        })
    recorded = [entry["field"] for entry in fields if entry["recorded"]]
    absent = [entry["field"] for entry in fields if not entry["recorded"]]
    return {
        "source_id": ec["source_id"],
        "review_recorded": ec["review_recorded"],
        "translation_status": ec["translation_status"],
        "review_status": ec["review_status"],
        "context_usable": ec["context_usable"],
        "blocked_reason_code": ec["blocked_reason_code"],
        "status_label": ec["status_label"],
        "translation_review": translation_review,
        "record": record,
        "fields": fields,
        "recorded_fields": recorded,
        "absent_fields": absent,
        "recorded_count": len(recorded),
        "absent_count": len(absent),
    }


def _link_block(bundle):
    ec = bundle["equipment_context"]
    base = {
        "matching_method": "exact_id_string",
        "key": "psr_id",
        "verification_status": "id_string_only",
        "caveat": LINK_CAVEAT,
    }
    if not ec["matched"]:
        return dict(base, status="blocked", blocked_reason_code="unmatched_ledger_id",
                    ledger_endpoint=None, telemetry_endpoint=None)
    by_id = {item["evidence_id"]: item for item in bundle["evidence_items"]}
    ledger = by_id[ec["evidence_id"]]
    telemetry = sorted((item for item in bundle["evidence_items"]
                        if item["evidence_type"] == "telemetry"),
                       key=lambda item: item["evidence_id"])
    complete_telemetry = [item for item in telemetry
                          if item["provenance_status"] == "complete"]
    if ledger["provenance_status"] != "complete" or not complete_telemetry:
        return dict(base, status="blocked", blocked_reason_code="incomplete_provenance",
                    ledger_endpoint=None, telemetry_endpoint=None)
    representative = complete_telemetry[0]
    return dict(
        base, status="linked", blocked_reason_code=None,
        ledger_endpoint=_endpoint_locator(ledger),
        telemetry_endpoint={
            "evidence_id": representative["evidence_id"],
            "record_count": len(telemetry),
            "first_observation": bundle["asset_scope"]["first_observation"],
            "last_observation": bundle["asset_scope"]["last_observation"],
            "source_files": sorted({item["source_file_id"] for item in telemetry}),
            "provenance_status": "complete",
        })


def _telemetry_block(bundle):
    scope = bundle["asset_scope"]
    telemetry = [item for item in bundle["evidence_items"]
                 if item["evidence_type"] == "telemetry"]
    return {
        "observation_count": scope["observation_count"],
        "measurement_point_count": scope["measurement_point_count"],
        "measurement_code_count": scope["measurement_code_count"],
        "first_observation": scope["first_observation"],
        "last_observation": scope["last_observation"],
        "dataset_types": list(scope["dataset_types"]),
        "source_files": list(scope["source_files"]),
        "descriptive_metrics": [dict(metric) for metric in bundle["descriptive_metrics"]],
        "metrics_scope": {
            "evidence_type": "telemetry",
            "evidence_count": len(telemetry),
            "inventory_digest_sha256": contract.content_digest(
                sorted(item["evidence_id"] for item in telemetry)),
            "source_files": sorted({item["source_file_id"] for item in telemetry}),
        },
    }


def _conclusions(bundle):
    return [{"dimension": entry["dimension"],
             "status": entry["status"],
             "open_register_ids": list(entry["open_register_ids"])}
            for entry in bundle["equipment_context"]["dependent_unknowns"]]


# --- unsupported material (defect history, tests, inspections, work orders) ---

def _wrapped_value(field):
    """The recorded string value of a translated defect field, or ``None``."""
    if isinstance(field, dict):
        if field.get("missing"):
            return None
        value = field.get("value")
    else:
        value = field
    return value if isinstance(value, str) and value else None


def _load_defect_records(path):
    payload = _read_json(path)
    return payload if isinstance(payload, list) else None


def _defect_identity_digest(records):
    rows = [[index, _wrapped_value(record.get("container_id")),
             _wrapped_value(record.get("obj_id"))]
            for index, record in enumerate(records) if isinstance(record, dict)]
    return contract.content_digest(rows)


def _defect_evidence(records, asset_id, source_file_id):
    if records is None:
        return {
            "evidence_type": "defect_history",
            "status": "source_unavailable",
            "statement": ("No defect-record source is supplied in this environment, "
                          "so defect-history linkage is unavailable. Nothing is inferred."),
            "scope": {
                "scope_id": DEFECT_SCOPE_ID,
                "description": f"the complete supplied defect-record set ({source_file_id})",
                "status": "unavailable",
                "record_count": None,
                "inventory_digest_sha256": None,
                "inventory_items": [],
            },
        }
    candidates = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        for field_name in ("container_id", "obj_id"):
            value = _wrapped_value(record.get(field_name))
            if value is not None and value == asset_id:
                candidates.append({
                    "source_file_id": source_file_id,
                    "locator": {"kind": "defect_record", "record_index": index,
                                "field": field_name, "value": value},
                    "matching_method": "exact_id_string",
                    "verification_status": "unconfirmed_namespace",
                })
    return {
        "evidence_type": "defect_history",
        "status": "candidate_only" if candidates else "no_confirmed_link",
        "statement": (
            f"No defect-history link is confirmed for this asset: the complete "
            f"supplied defect-record set ({len(records)} record(s)) was searched by "
            "exact ID string (container_id/obj_id), the identifier namespace is not "
            "confirmed, and the defect translation is an unreviewed draft excluded "
            "from retrieval. No relationship is inferred."),
        "candidate_links": candidates[:10],
        "scope": {
            "scope_id": DEFECT_SCOPE_ID,
            "description": (f"the complete supplied defect-record set "
                            f"({len(records)} record(s); {source_file_id})"),
            "status": "enumerated",
            "record_count": len(records),
            "inventory_digest_sha256": _defect_identity_digest(records),
            "inventory_items": [source_file_id],
        },
    }


def _source_family_rows(root):
    payload = _read_json(Path(root) / LINEAGE_PATH, {}) or {}
    families = []
    for source in payload.get("sources", []):
        if isinstance(source, dict) and isinstance(source.get("family"), str):
            families.append(source["family"])
    return sorted(families) or None


def _source_family_scope(families, description):
    if families is None:
        return {
            "scope_id": SOURCE_FAMILY_SCOPE_ID,
            "description": description,
            "status": "unavailable",
            "record_count": None,
            "inventory_digest_sha256": None,
            "inventory_items": [],
        }
    return {
        "scope_id": SOURCE_FAMILY_SCOPE_ID,
        "description": description,
        "status": "enumerated",
        "record_count": len(families),
        "inventory_digest_sha256": contract.content_digest(families),
        "inventory_items": list(families),
    }


def _missing_source_evidence(families, evidence_type, noun):
    if families is None:
        statement = ("The complete source-family inventory is not retained, so "
                     f"{noun} availability is reported as unavailable rather than "
                     "claimed absent. Nothing is inferred.")
    else:
        statement = (f"No supplied source provides {noun}. The complete "
                     f"source-family inventory was examined ({len(families)} "
                     f"families); none supplies {noun}, so that evidence is "
                     "unavailable and nothing is inferred.")
    return {
        "evidence_type": evidence_type,
        "status": "source_unavailable",
        "statement": statement,
        "scope": _source_family_scope(
            families, f"the complete supplied source-family inventory for {noun}"),
    }


def _unsupported_evidence(asset_id, defect_records, source_families):
    defect_source_id = Path(DEFECT_RECORDS_PATH).name
    items = [_defect_evidence(defect_records, asset_id, defect_source_id)]
    for evidence_type, noun in _UNSUPPORTED_FAMILIES:
        items.append(_missing_source_evidence(source_families, evidence_type, noun))
    return items


# --- public builder / validator ---------------------------------------------

def build_asset_evidence(asset_id, *, root=None, rows=None, catalog=None,
                         manifest=None, chunks=None, defect_records=_AUTO,
                         source_families=_AUTO, register=None, bundle=None):
    """Build the read-only asset evidence profile, or ``None`` when no evidence.

    ``None`` means the asset has no telemetry evidence in the target class, so the
    caller fails closed (404). Any contract violation raises ``AgentContractError``.

    ``bundle`` is an optional already-built, caller-validated context bundle. When
    supplied it is used as-is (the bundle inputs are then ignored), so a caller
    that already built the bundle for the same asset does not build it twice; the
    profile is still re-derived and validated against it.
    """
    root = Path(root or ROOT)
    if bundle is None:
        bundle = context.build_context_bundle(
            asset_id, root=root, rows=rows, catalog=catalog, manifest=manifest,
            chunks=chunks)
    if bundle is None:
        return None
    contract.validate_context(bundle, register)
    if source_families is _AUTO:
        source_families = _source_family_rows(root)
    if defect_records is _AUTO:
        defect_records = _load_defect_records(Path(root) / DEFECT_RECORDS_PATH)
    translation_review = _ledger_review_metadata(root)
    profile = {
        "profile_version": PROFILE_VERSION,
        "contract_version": contract.CONTRACT_VERSION,
        "asset_id": bundle["asset_id"],
        "data_origin": contract.DATA_ORIGIN,
        "bundle_digest_sha256": bundle["bundle_digest_sha256"],
        "context": _context_block(bundle, translation_review),
        "link": _link_block(bundle),
        "telemetry": _telemetry_block(bundle),
        "unsupported_evidence": _unsupported_evidence(
            bundle["asset_id"], defect_records, source_families),
        "unresolved_conclusions": _conclusions(bundle),
        "caveats": list(CAVEATS),
        "prohibitions": [dict(item) for item in contract.PROHIBITIONS],
    }
    validate_asset_evidence(profile, bundle, root=root, register=register)
    return profile


def validate_asset_evidence(profile, bundle=None, root=None, register=None):
    """Fail-closed validation of an asset evidence profile.

    Structural checks always run. When ``bundle`` is supplied the whole profile
    is re-derived from that validated bundle and required to match, so an altered
    endpoint, a drifted field, or a forged context cannot survive. Conclusion
    keys/phrases fail closed.
    """
    _strict(profile, ASSET_EVIDENCE_KEYS, ASSET_EVIDENCE_KEYS)
    if profile["profile_version"] != PROFILE_VERSION:
        _fail("bad_value", "unknown profile_version", "$.profile_version")
    if profile["contract_version"] != contract.CONTRACT_VERSION:
        _fail("bad_value", "unknown contract_version", "$.contract_version")
    if profile["data_origin"] != contract.DATA_ORIGIN:
        _fail("bad_value", "data_origin must be observed", "$.data_origin")

    for index, caveat in enumerate(profile["caveats"]):
        if not isinstance(caveat, str) or not caveat.strip():
            _fail("bad_type", "caveat must be a non-empty string",
                  f"$.caveats[{index}]")
        _scan_conclusions(caveat, f"$.caveats[{index}]")

    context_block = profile["context"]
    _strict(context_block, CONTEXT_KEYS, CONTEXT_KEYS, "$.context")
    if context_block["source_id"] != contract.LEDGER_SOURCE_ID:
        _fail("bad_value", "context.source_id must be the ledger source",
              "$.context.source_id")
    if context_block["record"] is not None:
        _strict(context_block["record"], CONTEXT_RECORD_KEYS,
                CONTEXT_RECORD_KEYS, "$.context.record")
    for index, entry in enumerate(context_block["fields"]):
        _strict(entry, CONTEXT_FIELD_KEYS, CONTEXT_FIELD_KEYS,
                f"$.context.fields[{index}]")
        if isinstance(entry["value"], str):
            _scan_conclusions(entry["value"], f"$.context.fields[{index}].value")
        if isinstance(entry["translated"], str):
            _scan_conclusions(entry["translated"],
                              f"$.context.fields[{index}].translated")

    link = profile["link"]
    _strict(link, LINK_KEYS, LINK_KEYS, "$.link")
    if link["status"] not in LINK_STATUSES:
        _fail("bad_value", "unknown link status", "$.link.status")
    if link["matching_method"] != "exact_id_string":
        _fail("bad_value", "matching_method must be exact_id_string",
              "$.link.matching_method")
    if link["key"] != "psr_id":
        _fail("bad_value", "link key must be psr_id", "$.link.key")
    if link["verification_status"] != "id_string_only":
        _fail("bad_value", "verification_status must be id_string_only",
              "$.link.verification_status")
    if not isinstance(link["caveat"], str) or "physical identity" not in link["caveat"].lower():
        _fail("missing_caveat",
              "the link caveat must state physical identity is not proven",
              "$.link.caveat")
    if link["status"] == "linked":
        if link["blocked_reason_code"] is not None:
            _fail("bad_value", "a linked profile must not carry a block reason",
                  "$.link.blocked_reason_code")
        _strict(link["ledger_endpoint"], LEDGER_ENDPOINT_KEYS,
                LEDGER_ENDPOINT_KEYS, "$.link.ledger_endpoint")
        _strict(link["telemetry_endpoint"], TELEMETRY_ENDPOINT_KEYS,
                TELEMETRY_ENDPOINT_KEYS, "$.link.telemetry_endpoint")
    else:
        if link["blocked_reason_code"] not in LINK_BLOCK_REASONS:
            _fail("bad_value", "unknown link block reason", "$.link.blocked_reason_code")
        for key in ("ledger_endpoint", "telemetry_endpoint"):
            if link[key] is not None:
                _fail("bad_value", f"a blocked link must not carry {key}", f"$.link.{key}")

    telemetry = profile["telemetry"]
    _strict(telemetry, TELEMETRY_KEYS, TELEMETRY_KEYS, "$.telemetry")
    _strict(telemetry["metrics_scope"], METRICS_SCOPE_KEYS,
            METRICS_SCOPE_KEYS, "$.telemetry.metrics_scope")

    for index, item in enumerate(profile["unsupported_evidence"]):
        path = f"$.unsupported_evidence[{index}]"
        _strict(item, UNSUPPORTED_KEYS, UNSUPPORTED_REQUIRED_KEYS, path)
        if not isinstance(item["statement"], str) or not item["statement"].strip():
            _fail("bad_type", "unsupported statement must be a non-empty string",
                  f"{path}.statement")
        _scan_conclusions(item["statement"], f"{path}.statement")
        _strict(item["scope"], UNSUPPORTED_SCOPE_KEYS,
                UNSUPPORTED_SCOPE_KEYS, f"{path}.scope")
        for link_index, candidate in enumerate(item.get("candidate_links", [])):
            _strict(candidate, CANDIDATE_LINK_KEYS, CANDIDATE_LINK_KEYS,
                    f"{path}.candidate_links[{link_index}]")

    for index, entry in enumerate(profile["unresolved_conclusions"]):
        _strict(entry, CONCLUSION_KEYS, CONCLUSION_KEYS,
                f"$.unresolved_conclusions[{index}]")

    for index, prohibition in enumerate(profile["prohibitions"]):
        _strict(prohibition, contract.PROHIBITION_KEYS,
                contract.PROHIBITION_KEYS, f"$.prohibitions[{index}]")

    if bundle is None:
        return True

    if profile["asset_id"] != bundle["asset_id"]:
        _fail("bad_value", "asset_id mismatch", "$.asset_id")
    if profile["bundle_digest_sha256"] != bundle["bundle_digest_sha256"]:
        _fail("bundle_digest_mismatch", "profile is not bound to this bundle",
              "$.bundle_digest_sha256")
    translation_review = None
    if root is not None:
        translation_review = _ledger_review_metadata(root)
    if context_block != _context_block(bundle, translation_review):
        _fail("unsupported_context",
              "context does not re-derive from the validated bundle", "$.context")
    if link != _link_block(bundle):
        _fail("unsupported_context",
              "link does not re-derive from the validated bundle", "$.link")
    if telemetry != _telemetry_block(bundle):
        _fail("unsupported_context",
              "telemetry does not re-derive from the validated bundle", "$.telemetry")
    if profile["unresolved_conclusions"] != _conclusions(bundle):
        _fail("unsupported_conclusion",
              "unresolved conclusions must re-derive from the bundle",
              "$.unresolved_conclusions")
    return True
