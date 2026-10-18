"""Strict agent context/draft contract (stdlib only, deterministic, local reads).

M0 scope: define and enforce the versioned data contract shared by the context
bundle and the agent draft BEFORE any model call or HTTP route exists. Runtime
validation lives here in Python (``schemas/`` is excluded from the deployment
package), and ``contract_key_registry`` lets a test prove these Python allowed-key
sets match the declarative JSON schemas, including every ``additionalProperties:
false`` object.

The only I/O here is reading the authoritative local evidence register
(``docs/evidence_register.json``) so the equipment-context dependent unknowns can
be re-derived and checked against the trusted register content instead of the
ids copied into the bundle. No network or model calls.

Evidence policy (fail-closed, enforced in code):
- A factual claim is emitted only as a direct copy of a supplied evidence item
  or as a reproducible calculation over supplied evidence ids.
- Every citation must resolve to the provenance index with a complete source
  hash, an exact locator, and an approved/reviewed status where it is knowledge.
- Nothing is inferred: missing, ambiguous, conflicting, untraceable, unreviewed
  evidence produces a machine-recorded gap and no claim about it.
- Suggestions may only be register-backed, documentary verification questions
  from the approved template set; they never direct work on physical equipment.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from pathlib import Path

CONTRACT_VERSION = "agent.v1"
CONTEXT_VERSION = "agent_context.v1"
DRAFT_VERSION = "agent_draft.v1"

PRODUCER_DETERMINISTIC = "deterministic_reference_v1"
ORIGINS = (PRODUCER_DETERMINISTIC, "ai_draft")
DATA_ORIGIN = "observed"

STATUS_GENERATED = "generated"
STATUS_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
DRAFT_STATUSES = (STATUS_GENERATED, STATUS_INSUFFICIENT_EVIDENCE)

CLAIM_KINDS = ("direct", "computation")
EVIDENCE_TYPES = ("telemetry", "ledger", "knowledge")
PROVENANCE_STATUSES = ("complete", "incomplete")

# Computation scope/version. Every aggregate records the exact selection predicate
# it ran over, the input count, and a digest of the complete input set, so a
# reviewer can reproduce it independently of the displayed (representative) citations.
FORMULA_VERSION = "formula.v1"
SEARCH_SCOPE_STATUSES = ("enumerated", "unavailable")
# Gap kinds that assert information is absent and therefore must name the complete
# inventory/record set searched (see ``search_scope``).
ABSENCE_GAP_KINDS = (
    "missing_source_hash", "missing_measurement_code", "unmatched_ledger_id",
    "unknown_units_scaling", "unknown_timezone", "missing_knowledge_provenance",
)
# Gap kinds whose question is about authoritative documentation (units/scaling,
# timestamp timezone). These must report documentation status explicitly; absence
# of documentation may only be claimed when a documentation inventory was searched.
DOCUMENTATION_GAP_KINDS = ("unknown_units_scaling", "unknown_timezone")
DOCUMENTATION_SUBJECTS = ("unit_scaling", "timestamp_timezone")
# Names for the complete inventories an absence gap may reference. Only the first
# two are retained in the bundle and can therefore be ``enumerated``; the others
# may only be reported ``unavailable`` (do not invent a scope).
GAP_SCOPE_IDS = (
    "telemetry_record_set", "manifest_sources", "ledger_catalog",
    "approved_chunk_store",
)
VERIFIABLE_SCOPE_IDS = ("telemetry_record_set", "manifest_sources")

# Equipment-context gate. The equipment ledger is a translated source. Its fields
# are carried as context with a locator and the recorded translation/review status,
# but a field may only support an equipment-context conclusion once the recorded
# translation review is reviewed+approved. Until then every field stays
# ``usable_for_context: false`` and the block reports why it is blocked.
LEDGER_SOURCE_ID = "SRC-LEDGER"
CONTEXT_BLOCK_REASONS = (
    "unmatched_ledger_id", "unreviewed_translation", "no_usable_context_fields",
)
# Single shared predicate: a translated source is reviewed only when the
# translation status is ``reviewed`` and the review status is accepted. The
# unreviewed-translation gap and the equipment-context gate both use this, so the
# two can never disagree about whether translated fields may be used.
_ACCEPTED_REVIEW_STATUSES = ("approved", "not_applicable")

# Runtime ledger review record. The full lineage inventory is a development
# artifact and is never shipped, so the deployed runtime carries this small,
# purpose-built record instead: the single source of the recorded ledger
# translation review. It binds the ledger source id, the exact translated catalog
# it covers, the recorded statuses, the review metadata, and the explicit
# exclusions. It is generated from the recorded review (never invented or
# broadened) and validated at runtime; anything missing, malformed, stale, or
# off-scope fails closed to unreviewed.
LEDGER_REVIEW_RECORD_VERSION = "ledger-review-v1"
LEDGER_REVIEW_SCOPE = ("translation accuracy of the ledger fields only "
                       "(column meanings, code-table and date renderings)")
LEDGER_REVIEW_EXCLUDES = (
    "physical asset identity", "engineering-rule applicability",
    "measurement units", "outcome labels", "time semantics",
)
LEDGER_REVIEW_RECORD_KEYS = frozenset({
    "record_version", "source_id", "catalog_sha256", "translation_status",
    "review_status", "review", "excludes",
})
LEDGER_REVIEW_KEYS = frozenset({
    "decision", "reviewed_at", "reviewed_by", "reviewer_role", "scope",
})
LEDGER_REVIEW_DECISION = "approved"

# Human-readable equipment-context status labels (a clear display value alongside
# the machine ``blocked_reason_code``). The validator re-derives the label.
CONTEXT_STATUS_LABELS = (
    "ledger not matched", "translation unreviewed", "no usable context fields",
    "translation reviewed",
)
# Conclusions that stay unknown until reviewed evidence (a verified register item)
# supports them. Each maps to one or more evidence-register topics. This mapping is
# the single source of truth for both the bundle builder and the validator.
CONTEXT_UNKNOWN_DIMENSIONS = (
    "criteria_applicability", "physical_asset_identity", "units",
    "time_semantics", "outcome_linkage",
)
CONTEXT_UNKNOWN_STATUSES = ("unknown", "supported", "not_recorded")
CONTEXT_UNKNOWN_TOPICS = (
    ("criteria_applicability", ("engineering_rules",)),
    ("physical_asset_identity", ("asset_identity",)),
    ("units", ("measurement_code",)),
    ("time_semantics", ("temporal_semantics",)),
    ("outcome_linkage", ("outcome_labels",)),
)
CONTEXT_UNKNOWN_TOPICS_MAP = dict(CONTEXT_UNKNOWN_TOPICS)

# Register binding. The dependent unknowns are only as trustworthy as the register
# they were derived from, so the bundle records a content-level binding (path id,
# declared version, digest algorithm, digest, item count) and the validator
# recomputes it against the authoritative local register. The digest is over the
# canonical JSON of the whole parsed register, so it binds content, not byte layout.
CONTEXT_REGISTER_ID = "docs/evidence_register.json"
REGISTER_DIGEST_ALGORITHM = "sha256"
DEFAULT_REGISTER_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "evidence_register.json")

# Closed, tested calculation allowlist. Mirrors the frozen descriptive baseline
# (app/equipment.py::_describe_values, scripts/analyze_measurements.py).
FORMULAS = (
    "count", "min", "max", "mean", "stdev_population", "zero_count", "zero_proportion",
)

# Documentary-only verification actions. There is deliberately no value that
# instructs physical work (inspect / operate / repair / replace / isolate / dispatch).
VERIFICATION_ACTIONS = (
    "confirm_from_cited_source",
    "record_owner_decision",
    "confirm_translation_review",
    "confirm_id_mapping",
)
_TOPIC_ACTION = {
    "measurement_code": "confirm_from_cited_source",
    "temporal_semantics": "confirm_from_cited_source",
    "asset_identity": "confirm_id_mapping",
    "asset_construction": "confirm_id_mapping",
    "source_metadata": "confirm_from_cited_source",
    "value_interpretation": "confirm_from_cited_source",
    "engineering_rules": "record_owner_decision",
    "outcome_labels": "confirm_from_cited_source",
    "evaluation_coverage": "record_owner_decision",
}

GAP_KINDS = (
    "missing_source_hash",
    "missing_measurement_code",
    "conflicting_values",
    "unmatched_ledger_id",
    "unknown_units_scaling",
    "unknown_timezone",
    "unreviewed_translation",
    "unapproved_knowledge",
    "missing_knowledge_provenance",
)

# Secondary defense-in-depth scan over agent-authored prose only. Register-derived
# verbatim text (question/blocks) is governed by the register binding instead, so
# it is never scanned (register questions may legitimately name data availability).
PROHIBITED_TEXT_PHRASES = (
    "failure probability", "probability of failure", "fault state",
    "fault classification", "fault diagnosis", "diagnos",
    "remaining useful life", "remaining life", "health index",
    "imminent failure", "risk of failure", "predictive maintenance",
    "replace the", "repair the", "isolate the", "de-energize", "deenergize",
    "shut down the", "work order", "dispatch", "inspect the", "operate the",
    "lock out", "tag out",
)

PROHIBITIONS = (
    {"prohibition_id": "no_fault_diagnosis",
     "text": "Do not diagnose a fault or an equipment condition."},
    {"prohibition_id": "no_failure_probability",
     "text": "Do not state or estimate failure probability, risk, or remaining life."},
    {"prohibition_id": "no_maintenance_action",
     "text": "Do not recommend maintenance, repair, replacement, isolation, or dispatch."},
    {"prohibition_id": "no_unit_inference",
     "text": "Do not infer or assume units, scaling, or timestamp timezone."},
    {"prohibition_id": "no_invented_facts",
     "text": "Do not invent, infer, or fill in missing facts; cite supplied evidence only."},
    {"prohibition_id": "no_unapproved_knowledge",
     "text": "Do not use unapproved or unreviewed knowledge."},
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# --- allowed-key sets (single source of truth; mirrored by the JSON schemas) ---

CONTEXT_ROOT_KEYS = {
    "bundle_version", "contract_version", "asset_id", "data_origin", "asset_scope",
    "provenance_index", "evidence_items", "descriptive_metrics", "known_gaps",
    "unresolved_questions", "approved_knowledge", "prohibitions", "determinism",
    "equipment_context", "bundle_digest_sha256",
}
ASSET_SCOPE_KEYS = {
    "asset_id", "equipment_type_code", "observation_count",
    "measurement_point_count", "measurement_code_count", "first_observation",
    "last_observation", "dataset_types", "source_files", "ledger_match",
}
LEDGER_MATCH_KEYS = {
    "matched", "evidence_id", "locator", "profile", "reason",
    "translation_status", "review_status",
}
PROVENANCE_INDEX_KEYS = {"evidence", "knowledge"}
PROVENANCE_EVIDENCE_KEYS = {
    "evidence_type", "source_file_id", "source_sha256", "provenance_status",
    "locator", "value", "measurement_type_code", "observed_at", "dataset_type",
}
PROVENANCE_KNOWLEDGE_KEYS = {
    "review_status", "translation_status", "source_id", "source_uri",
    "source_sha256", "source_version", "locator",
}
EVIDENCE_ITEM_KEYS = {
    "evidence_id", "evidence_type", "source_file_id", "source_record_index",
    "measurement_point_id", "measurement_type_code", "dataset_type", "observed_at",
    "value", "data_origin", "source_sha256", "provenance_status", "provenance_gap",
    "locator", "unit_status", "timestamp_timezone_status",
    "translation_status", "review_status",
}
DESCRIPTIVE_METRIC_KEYS = {
    "measurement_type_code", "observation_count", "minimum", "maximum", "mean",
    "stdev_population", "zero_count", "zero_proportion",
}
GAP_KEYS = {
    "gap_id", "kind", "statement", "evidence_ids", "register_id", "detail",
    "search_scope", "documentation_scope",
}
GAP_SEARCH_SCOPE_KEYS = {
    "scope_id", "description", "status", "record_count",
    "inventory_digest_sha256", "source_files",
}
GAP_DOCUMENTATION_SCOPE_KEYS = {
    "subject", "status", "description", "inventory_source",
}
UNRESOLVED_QUESTION_KEYS = {
    "question_id", "register_id", "question", "domain", "status", "owner_role",
    "applicability", "blocks", "measurement_code", "acquisition_evidence_id",
    "applies", "applies_because", "related_evidence_ids", "verification_action",
    "evidence_specific", "reason_supported", "group_id", "group_label",
}
KNOWLEDGE_ITEM_KEYS = {
    "chunk_id", "title", "source_id", "source_uri", "source_locator",
    "source_version", "translation_status", "review_status", "source_sha256",
    "provenance_status",
}
PROHIBITION_KEYS = {"prohibition_id", "text"}
DETERMINISM_KEYS = {"input_hashes", "manifest_sources", "ordering", "digest_excludes"}
EQUIPMENT_CONTEXT_KEYS = {
    "source_id", "matched", "evidence_id", "locator", "source_sha256",
    "translation_status", "review_status", "review_recorded", "context_usable",
    "blocked_reason_code", "status_label", "register_binding",
    "dependent_unknowns", "fields",
}
EQUIPMENT_CONTEXT_FIELD_KEYS = {
    "field", "column", "value", "translated", "locator", "source_sha256",
    "translation_status", "review_status", "usable_for_context",
    "blocked_reason_code",
}
CONTEXT_UNKNOWN_KEYS = {
    "dimension", "topics", "status", "register_ids", "open_register_ids",
}
CONTEXT_REGISTER_BINDING_KEYS = {
    "register_path", "register_version", "digest_algorithm",
    "register_digest_sha256", "item_count", "dimension_count",
}

DRAFT_ROOT_KEYS = {
    "draft_version", "contract_version", "origin", "data_origin", "asset_id",
    "bundle_digest_sha256", "status", "summary", "claims", "caveats",
    "known_gaps", "suggestions", "human_review",
}
CLAIM_KEYS = {"claim_id", "kind", "text", "citations", "computation", "relationship"}
CITATION_KEYS = {
    "type", "evidence_id", "chunk_id", "locator", "source_sha256",
    "provenance_status",
}
COMPUTATION_KEYS = {
    "formula", "formula_version", "scope", "inputs", "input_count",
    "inputs_digest_sha256", "result",
}
COMPUTATION_SCOPE_KEYS = {
    "description", "evidence_type", "measurement_type_code",
    "provenance_status", "excluded_evidence_ids",
}
RELATIONSHIP_KEYS = {
    "relation", "telemetry_evidence_id", "ledger_evidence_id", "caveat",
}
SUGGESTION_KEYS = {
    "suggestion_id", "question_id", "register_id", "domain", "question", "why",
    "verify_action", "citations", "uncertainty_resolved", "blocks",
    "group_id", "group_label",
}
HUMAN_REVIEW_KEYS = {"required", "status"}
HUMAN_REVIEW_STATUSES = ("pending", "not_required")

# Discriminated-union locators, validated per kind (not a single strict object).
LOCATOR_TELEMETRY_KEYS = {
    "kind", "source_file_id", "record_index", "measurement_point_id",
    "measurement_type_code", "observed_at", "dataset_type",
}
LOCATOR_LEDGER_KEYS = {"kind", "workbook", "sheet", "key"}
LOCATOR_KNOWLEDGE_KEYS = {"kind", "source_id", "source_locator", "source_version"}
_LOCATOR_KEYS_BY_KIND = {
    "telemetry": LOCATOR_TELEMETRY_KEYS,
    "ledger": LOCATOR_LEDGER_KEYS,
    "knowledge": LOCATOR_KNOWLEDGE_KEYS,
}


class AgentContractError(Exception):
    """Fail-closed contract violation. ``code`` is stable for tests and routes."""

    def __init__(self, code, message, path="$"):
        super().__init__(f"{path}: {code}: {message}")
        self.code = code
        self.path = path
        self.detail = message


def _fail(code, message, path="$"):
    raise AgentContractError(code, message, path)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def content_digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bundle_digest(bundle):
    body = {key: value for key, value in bundle.items() if key != "bundle_digest_sha256"}
    return content_digest(body)


def ledger_translation_reviewed(translation_status, review_status):
    """Whether a translated ledger source may support claims or context.

    Reviewed+approved is required; anything else leaves translated meanings
    unusable. Shared by the ``unreviewed_translation`` gap and the
    equipment-context gate so the two cannot drift apart.
    """
    return (translation_status == "reviewed"
            and review_status in _ACCEPTED_REVIEW_STATUSES)


def _is_sha256(value):
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def ledger_review_record(record, catalog_sha256):
    """Validate a runtime ledger review record against the exact catalog.

    Returns the normalized recorded review (statuses, review block, exclusions)
    only when ``record`` is a well-formed approval that names the ledger source,
    binds this exact ``catalog_sha256``, keeps the allowed review scope, and
    carries exactly the recorded exclusions. Any other case returns ``None`` so
    callers fail closed to unreviewed. Recording a review never resolves a
    register item; the exclusions are carried verbatim.
    """
    if not isinstance(record, dict) or set(record) != LEDGER_REVIEW_RECORD_KEYS:
        return None
    if record.get("record_version") != LEDGER_REVIEW_RECORD_VERSION:
        return None
    if record.get("source_id") != LEDGER_SOURCE_ID:
        return None
    if not _is_sha256(catalog_sha256):
        return None
    recorded_hash = record.get("catalog_sha256")
    if not _is_sha256(recorded_hash) or recorded_hash != catalog_sha256:
        return None
    if record.get("translation_status") != "reviewed":
        return None
    if record.get("review_status") != "approved":
        return None
    review = record.get("review")
    if not isinstance(review, dict) or set(review) != LEDGER_REVIEW_KEYS:
        return None
    if review.get("decision") != LEDGER_REVIEW_DECISION:
        return None
    for key in ("reviewed_at", "reviewed_by", "reviewer_role", "scope"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            return None
    if review.get("scope") != LEDGER_REVIEW_SCOPE:
        return None
    excludes = record.get("excludes")
    if not isinstance(excludes, list) or tuple(excludes) != LEDGER_REVIEW_EXCLUDES:
        return None
    return {
        "translation_status": "reviewed",
        "review_status": "approved",
        "recorded": True,
        "review": dict(review),
        "excludes": list(LEDGER_REVIEW_EXCLUDES),
    }


def equipment_context_label(matched, reviewed, usable_any):
    """Human-readable equipment-context status label, derived from the gate."""
    if not matched:
        return "ledger not matched"
    if not reviewed:
        return "translation unreviewed"
    if not usable_any:
        return "no usable context fields"
    return "translation reviewed"


def load_authoritative_register(path=None):
    """Parse the authoritative local evidence register, or ``None`` if unreadable.

    The register is the shipped local record at ``docs/evidence_register.json``.
    A missing or malformed register returns ``None`` so callers can fail closed.
    """
    target = Path(path) if path is not None else DEFAULT_REGISTER_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def register_is_wellformed(register):
    """Structural check for the register used in authoritative verification."""
    if not isinstance(register, dict):
        return False
    items = register.get("items")
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        for field in ("id", "topic", "status"):
            if not isinstance(item.get(field), str) or not item[field]:
                return False
    return True


def register_digest(register):
    """Deterministic sha256 over the canonical JSON of the whole register."""
    return content_digest(register)


def register_version(register):
    """The register's declared version, or an empty string when absent."""
    if not isinstance(register, dict):
        return ""
    value = register.get("register_version")
    return value if isinstance(value, str) else ""


def register_item_count(register):
    items = register.get("items") if isinstance(register, dict) else None
    return len(items) if isinstance(items, list) else 0


def derive_context_dimensions(register):
    """Re-derive the five dependent-unknown dimensions from a register.

    Deterministic and shared by the builder and the validator: for each dimension
    it lists the backing register items (matched by topic), which of those are
    still open, and the resulting status. No register id is hard-coded, so a
    changed, extra, missing, or mis-mapped register item is detected by comparing
    the result with the register the bundle recorded.
    """
    items = register.get("items") if isinstance(register, dict) else None
    items = [item for item in items if isinstance(item, dict)] \
        if isinstance(items, list) else []
    dimensions = []
    for dimension, topics in CONTEXT_UNKNOWN_TOPICS:
        backing = [item for item in items if item.get("topic") in topics]
        register_ids = sorted(str(item["id"]) for item in backing if item.get("id"))
        open_ids = sorted(str(item["id"]) for item in backing
                          if item.get("id") and item.get("status") != "verified")
        if not register_ids:
            status = "not_recorded"
        elif open_ids:
            status = "unknown"
        else:
            status = "supported"
        dimensions.append({
            "dimension": dimension,
            "topics": list(topics),
            "status": status,
            "register_ids": register_ids,
            "open_register_ids": open_ids,
        })
    return dimensions


def equipment_register_binding(register):
    """The register content binding recorded in (and verified against) the bundle."""
    return {
        "register_path": CONTEXT_REGISTER_ID,
        "register_version": register_version(register),
        "digest_algorithm": REGISTER_DIGEST_ALGORITHM,
        "register_digest_sha256": register_digest(register),
        "item_count": register_item_count(register),
        "dimension_count": len(CONTEXT_UNKNOWN_DIMENSIONS),
    }


def _r6(value):
    return round(value, 6)


def _strict(instance, allowed, required, path="$"):
    if not isinstance(instance, dict):
        _fail("bad_type", "expected object", path)
    unknown = sorted(set(instance) - set(allowed))
    if unknown:
        _fail("unknown_key", f"unexpected keys {unknown}", path)
    missing = sorted(set(required) - set(instance))
    if missing:
        _fail("missing_key", f"missing keys {missing}", path)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value):
    return _is_number(value) and math.isfinite(value)


def scan_prohibited(text, path="$"):
    """Secondary scan over agent-authored prose. Raises ``prohibited_text``."""
    lowered = str(text).lower()
    for phrase in PROHIBITED_TEXT_PHRASES:
        if phrase in lowered:
            _fail("prohibited_text", f"prohibited phrase {phrase!r}", path)


def _validate_locator(locator, kind, path):
    if not isinstance(locator, dict):
        _fail("bad_type", "locator must be an object", path)
    declared = locator.get("kind")
    if declared != kind:
        _fail("locator_mismatch", f"locator kind {declared!r} != {kind!r}", path)
    _strict(locator, _LOCATOR_KEYS_BY_KIND[kind], _LOCATOR_KEYS_BY_KIND[kind], path)


# --- context validation -----------------------------------------------------

def _manifest_entries(bundle):
    """The complete manifest source list retained in the bundle (or empty).

    Entries are ``{"source_file_id", "sha256"}``. An absent/empty list means the
    manifest inventory is not retained, so a ``manifest_sources`` scope may only be
    reported ``unavailable``.
    """
    determinism = bundle.get("determinism")
    if not isinstance(determinism, dict):
        _fail("bad_type", "determinism must be an object", "$.determinism")
    entries = determinism.get("manifest_sources")
    if not isinstance(entries, list):
        _fail("bad_type", "determinism.manifest_sources must be a list",
              "$.determinism.manifest_sources")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"source_file_id", "sha256"}:
            _fail("bad_type", "manifest entry must map source_file_id and sha256",
                  "$.determinism.manifest_sources")
        if not isinstance(entry["source_file_id"], str) or not entry["source_file_id"]:
            _fail("bad_type", "manifest entry source_file_id must be a non-empty string",
                  "$.determinism.manifest_sources")
        sha = entry["sha256"]
        if sha is not None and not (isinstance(sha, str) and _SHA256_RE.match(sha)):
            _fail("bad_type", "manifest entry sha256 must be sha256 or null",
                  "$.determinism.manifest_sources")
    return entries


def _validate_equipment_context(bundle, by_id, register):
    """Fail-closed gate for ledger-derived equipment context.

    Every field may be ``usable_for_context`` only when the recorded translation
    review is reviewed+approved and the field carries a value. The validator
    re-derives that predicate (and the block-level reason) from the carried
    provenance, so a forged bundle cannot mark an unreviewed field as usable.

    The dependent unknowns are additionally verified against the trusted register
    supplied here: the validator recomputes the register digest/version/item count
    and re-derives the five dimensions, so ids, topics, and statuses copied into
    the bundle cannot drift from the authoritative register.
    """
    context = bundle["equipment_context"]
    path = "$.equipment_context"
    _strict(context, EQUIPMENT_CONTEXT_KEYS, EQUIPMENT_CONTEXT_KEYS, path)
    if context["source_id"] != LEDGER_SOURCE_ID:
        _fail("bad_value", "equipment_context.source_id must be the ledger source",
              f"{path}.source_id")
    for field in ("matched", "review_recorded", "context_usable"):
        if not isinstance(context[field], bool):
            _fail("bad_type", f"equipment_context.{field} must be boolean",
                  f"{path}.{field}")
    for field in ("translation_status", "review_status"):
        if context[field] is not None and not isinstance(context[field], str):
            _fail("bad_type", f"equipment_context.{field} must be a string or null",
                  f"{path}.{field}")
    reason = context["blocked_reason_code"]
    if reason is not None and reason not in CONTEXT_BLOCK_REASONS:
        _fail("bad_value", "unknown equipment_context blocked_reason_code",
              f"{path}.blocked_reason_code")

    matched = context["matched"]
    ledger_item = None
    if matched:
        evidence_id = context["evidence_id"]
        if not isinstance(evidence_id, str) or evidence_id not in by_id:
            _fail("unresolved_citation",
                  "equipment_context.evidence_id must resolve to a supplied item",
                  f"{path}.evidence_id")
        ledger_item = by_id[evidence_id]
        if ledger_item["evidence_type"] != "ledger":
            _fail("bad_value", "equipment_context must reference a ledger item",
                  f"{path}.evidence_id")
        if context["locator"] != ledger_item["locator"]:
            _fail("locator_mismatch", "equipment_context locator drift",
                  f"{path}.locator")
        if context["source_sha256"] != ledger_item["source_sha256"]:
            _fail("source_hash_mismatch", "equipment_context source hash drift",
                  f"{path}.source_sha256")
        if (context["translation_status"] != ledger_item["translation_status"]
                or context["review_status"] != ledger_item["review_status"]):
            _fail("unsupported_context",
                  "equipment_context status must match the ledger item",
                  f"{path}.translation_status")
        match = bundle["asset_scope"]["ledger_match"]
        if match["matched"]:
            if context["evidence_id"] != match["evidence_id"]:
                _fail("unsupported_context",
                      "equipment_context must reference the matched ledger item",
                      f"{path}.evidence_id")
            if (context["translation_status"] != match["translation_status"]
                    or context["review_status"] != match["review_status"]):
                _fail("unsupported_context",
                      "equipment_context status must match asset_scope.ledger_match",
                      f"{path}.translation_status")
    else:
        if bundle["asset_scope"]["ledger_match"]["matched"]:
            _fail("unsupported_context",
                  "an unmatched equipment_context must agree with asset_scope.ledger_match",
                  f"{path}.matched")
        for field in ("evidence_id", "locator", "source_sha256"):
            if context[field] is not None:
                _fail("bad_value",
                      f"an unmatched equipment_context must not carry {field}",
                      f"{path}.{field}")

    reviewed = ledger_item is not None and ledger_translation_reviewed(
        ledger_item["translation_status"], ledger_item["review_status"])

    fields = context["fields"]
    if not isinstance(fields, list):
        _fail("bad_type", "equipment_context.fields must be a list", f"{path}.fields")
    if not matched and fields:
        _fail("bad_value", "an unmatched equipment_context must carry no fields",
              f"{path}.fields")

    seen_fields = set()
    usable_any = False
    for index, entry in enumerate(fields):
        fpath = f"{path}.fields[{index}]"
        _strict(entry, EQUIPMENT_CONTEXT_FIELD_KEYS, EQUIPMENT_CONTEXT_FIELD_KEYS, fpath)
        if not isinstance(entry["field"], str) or not entry["field"]:
            _fail("bad_type", "field name must be a non-empty string", f"{fpath}.field")
        if entry["field"] in seen_fields:
            _fail("bad_value", "duplicate equipment-context field", f"{fpath}.field")
        seen_fields.add(entry["field"])
        if not isinstance(entry["column"], str) or not entry["column"]:
            _fail("bad_type", "field column must be a non-empty string", f"{fpath}.column")
        for value_key in ("value", "translated"):
            if entry[value_key] is not None and not isinstance(entry[value_key], str):
                _fail("bad_type", f"{value_key} must be a string or null",
                      f"{fpath}.{value_key}")
        for status_key in ("translation_status", "review_status"):
            if entry[status_key] is not None and not isinstance(entry[status_key], str):
                _fail("bad_type", f"{status_key} must be a string or null",
                      f"{fpath}.{status_key}")
        if ledger_item is not None:
            if entry["locator"] != ledger_item["locator"]:
                _fail("locator_mismatch", "field locator drift", f"{fpath}.locator")
            if entry["source_sha256"] != ledger_item["source_sha256"]:
                _fail("source_hash_mismatch", "field source hash drift",
                      f"{fpath}.source_sha256")
            if (entry["translation_status"] != ledger_item["translation_status"]
                    or entry["review_status"] != ledger_item["review_status"]):
                _fail("unsupported_context",
                      "field status must match the ledger item", fpath)
        has_value = entry["value"] not in (None, "")
        expected_usable = bool(reviewed and has_value)
        if entry["usable_for_context"] is not expected_usable:
            _fail("unsupported_context",
                  "a field may be usable only when its translation is reviewed "
                  "and it carries a value", f"{fpath}.usable_for_context")
        expected_reason = None
        if not expected_usable:
            expected_reason = ("unreviewed_translation" if not reviewed
                               else "missing_value")
        if entry["blocked_reason_code"] != expected_reason:
            _fail("unsupported_context",
                  "field blocked_reason_code does not match its provenance",
                  f"{fpath}.blocked_reason_code")
        usable_any = usable_any or expected_usable

    expected_usable = bool(matched and reviewed and usable_any)
    if context["context_usable"] is not expected_usable:
        _fail("unsupported_context",
              "context_usable must follow from the recorded review and field values",
              f"{path}.context_usable")
    if not matched:
        expected_block = "unmatched_ledger_id"
    elif not reviewed:
        expected_block = "unreviewed_translation"
    elif not usable_any:
        expected_block = "no_usable_context_fields"
    else:
        expected_block = None
    if context["blocked_reason_code"] != expected_block:
        _fail("unsupported_context",
              "blocked_reason_code does not match the recorded review state",
              f"{path}.blocked_reason_code")

    expected_label = equipment_context_label(matched, reviewed, usable_any)
    if context["status_label"] != expected_label:
        _fail("unsupported_context",
              "status_label must follow from the recorded review and field values",
              f"{path}.status_label")

    unknowns = context["dependent_unknowns"]
    if not isinstance(unknowns, list):
        _fail("bad_type", "equipment_context.dependent_unknowns must be a list",
              f"{path}.dependent_unknowns")
    seen_dimensions = set()
    for u_index, entry in enumerate(unknowns):
        upath = f"{path}.dependent_unknowns[{u_index}]"
        _strict(entry, CONTEXT_UNKNOWN_KEYS, CONTEXT_UNKNOWN_KEYS, upath)
        if entry["dimension"] not in CONTEXT_UNKNOWN_DIMENSIONS:
            _fail("bad_value", "unknown dependent-unknown dimension",
                  f"{upath}.dimension")
        if entry["dimension"] in seen_dimensions:
            _fail("bad_value", "duplicate dependent-unknown dimension",
                  f"{upath}.dimension")
        seen_dimensions.add(entry["dimension"])
        for list_key in ("topics", "register_ids", "open_register_ids"):
            value = entry[list_key]
            if not isinstance(value, list) or not all(
                    isinstance(item, str) and item for item in value):
                _fail("bad_type", f"{list_key} must be a list of non-empty strings",
                      f"{upath}.{list_key}")
            if len(set(value)) != len(value):
                _fail("bad_value", f"{list_key} must not repeat ids", f"{upath}.{list_key}")
        if list(entry["topics"]) != list(CONTEXT_UNKNOWN_TOPICS_MAP[entry["dimension"]]):
            _fail("bad_value",
                  "dependent-unknown topics must match the code mapping",
                  f"{upath}.topics")
        if not set(entry["open_register_ids"]) <= set(entry["register_ids"]):
            _fail("unsupported_context",
                  "open register ids must be a subset of the backing register ids",
                  f"{upath}.open_register_ids")
        expected_unknown = ("unknown" if entry["open_register_ids"]
                            else ("supported" if entry["register_ids"]
                                  else "not_recorded"))
        if entry["status"] != expected_unknown:
            _fail("unsupported_context",
                  "a dependent unknown is 'unknown' only while it has an open "
                  "register item", f"{upath}.status")
    if seen_dimensions != set(CONTEXT_UNKNOWN_DIMENSIONS):
        _fail("unsupported_context",
              "dependent_unknowns must cover every context dimension exactly once",
              f"{path}.dependent_unknowns")

    bpath = f"{path}.register_binding"
    binding = context["register_binding"]
    _strict(binding, CONTEXT_REGISTER_BINDING_KEYS, CONTEXT_REGISTER_BINDING_KEYS, bpath)
    if binding["register_path"] != CONTEXT_REGISTER_ID:
        _fail("bad_value", "unknown register_path", f"{bpath}.register_path")
    if binding["digest_algorithm"] != REGISTER_DIGEST_ALGORITHM:
        _fail("bad_value", "unknown register digest algorithm",
              f"{bpath}.digest_algorithm")
    if not isinstance(binding["register_version"], str):
        _fail("bad_type", "register_version must be a string", f"{bpath}.register_version")
    if not isinstance(binding["register_digest_sha256"], str) or \
            not _SHA256_RE.match(binding["register_digest_sha256"]):
        _fail("bad_value", "register digest must be sha256",
              f"{bpath}.register_digest_sha256")
    for count_field in ("item_count", "dimension_count"):
        value = binding[count_field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _fail("bad_type", f"{count_field} must be a non-negative integer",
                  f"{bpath}.{count_field}")
    if binding["dimension_count"] != len(CONTEXT_UNKNOWN_DIMENSIONS):
        _fail("bad_value", "dimension_count must equal the five context dimensions",
              f"{bpath}.dimension_count")

    # Authoritative verification: re-derive everything from the trusted register.
    # Nothing copied into the bundle is trusted on its own.
    if not register_is_wellformed(register):
        _fail("unverified_register",
              "the authoritative evidence register is missing or malformed", bpath)
    if binding["register_digest_sha256"] != register_digest(register):
        _fail("register_mismatch",
              "the bundle is bound to different register content",
              f"{bpath}.register_digest_sha256")
    if binding["register_version"] != register_version(register):
        _fail("register_mismatch",
              "the bundle is bound to a different register version",
              f"{bpath}.register_version")
    if binding["item_count"] != register_item_count(register):
        _fail("register_mismatch",
              "the bundle's register item count does not match the register",
              f"{bpath}.item_count")
    if unknowns != derive_context_dimensions(register):
        _fail("register_mismatch",
              "dependent_unknowns does not match the authoritative register",
              f"{path}.dependent_unknowns")


def validate_context(bundle, register=None):
    """Validate a context bundle against the trusted evidence register.

    ``register`` is the authoritative parsed register. When it is ``None`` the
    validator loads the local evidence register itself and fails closed
    (``unverified_register``) if it is missing or malformed, so a bundle's
    dependent unknowns are always checked against real register content rather
    than the ids copied into the bundle. Pass a parsed register to override it
    (tests, or a changed-register comparison).
    """
    if register is None:
        register = load_authoritative_register()
        if register is None:
            _fail("unverified_register",
                  "the authoritative evidence register is missing or unreadable",
                  "$.equipment_context.register_binding")
    _strict(bundle, CONTEXT_ROOT_KEYS, CONTEXT_ROOT_KEYS)
    if bundle["bundle_version"] != CONTEXT_VERSION:
        _fail("bad_value", "unknown bundle_version", "$.bundle_version")
    if bundle["contract_version"] != CONTRACT_VERSION:
        _fail("bad_value", "unknown contract_version", "$.contract_version")
    if bundle["data_origin"] != DATA_ORIGIN:
        _fail("bad_value", "data_origin must be observed", "$.data_origin")
    if bundle["bundle_digest_sha256"] != bundle_digest(bundle):
        _fail("bundle_digest_mismatch", "bundle digest does not match content")

    scope = bundle["asset_scope"]
    _strict(scope, ASSET_SCOPE_KEYS, ASSET_SCOPE_KEYS, "$.asset_scope")
    if scope["asset_id"] != bundle["asset_id"]:
        _fail("bad_value", "asset_scope.asset_id != bundle.asset_id", "$.asset_scope")
    _strict(scope["ledger_match"], LEDGER_MATCH_KEYS, LEDGER_MATCH_KEYS,
            "$.asset_scope.ledger_match")
    if not isinstance(scope["ledger_match"]["matched"], bool):
        _fail("bad_type", "ledger_match.matched must be boolean",
              "$.asset_scope.ledger_match")
    for field in ("translation_status", "review_status"):
        value = scope["ledger_match"][field]
        if value is not None and not isinstance(value, str):
            _fail("bad_type", f"ledger_match.{field} must be a string or null",
                  "$.asset_scope.ledger_match")

    items = bundle["evidence_items"]
    if not isinstance(items, list):
        _fail("bad_type", "evidence_items must be a list", "$.evidence_items")
    by_id = {}
    for index, item in enumerate(items):
        path = f"$.evidence_items[{index}]"
        _strict(item, EVIDENCE_ITEM_KEYS, EVIDENCE_ITEM_KEYS, path)
        if item["evidence_type"] not in EVIDENCE_TYPES:
            _fail("bad_value", "unknown evidence_type", f"{path}.evidence_type")
        if item["data_origin"] != DATA_ORIGIN:
            _fail("bad_value", "data_origin must be observed", f"{path}.data_origin")
        if item["provenance_status"] not in PROVENANCE_STATUSES:
            _fail("bad_value", "unknown provenance_status", f"{path}.provenance_status")
        sha = item["source_sha256"]
        if item["provenance_status"] == "complete":
            if not (isinstance(sha, str) and _SHA256_RE.match(sha)):
                _fail("incomplete_provenance", "complete provenance needs a sha256",
                      f"{path}.source_sha256")
            if item["provenance_gap"] is not False:
                _fail("bad_value", "provenance_gap must be False when complete",
                      f"{path}.provenance_gap")
        else:
            if sha is not None:
                _fail("incomplete_provenance", "incomplete provenance must not carry a hash",
                      f"{path}.source_sha256")
            if item["provenance_gap"] is not True:
                _fail("bad_value", "provenance_gap must be True when incomplete",
                      f"{path}.provenance_gap")
        _validate_locator(item["locator"], item["evidence_type"], f"{path}.locator")
        for field in ("translation_status", "review_status"):
            if item[field] is not None and not isinstance(item[field], str):
                _fail("bad_type", f"{field} must be a string or null",
                      f"{path}.{field}")
        if item["evidence_id"] in by_id:
            _fail("bad_value", "duplicate evidence_id", f"{path}.evidence_id")
        by_id[item["evidence_id"]] = item

    index = bundle["provenance_index"]
    _strict(index, PROVENANCE_INDEX_KEYS, PROVENANCE_INDEX_KEYS, "$.provenance_index")
    if not isinstance(index["evidence"], dict) or not isinstance(index["knowledge"], dict):
        _fail("bad_type", "provenance_index maps must be objects", "$.provenance_index")
    if set(index["evidence"]) != set(by_id):
        _fail("evidence_index_mismatch", "evidence index keys != evidence_items ids",
              "$.provenance_index.evidence")
    for eid, entry in index["evidence"].items():
        path = f"$.provenance_index.evidence[{eid}]"
        _strict(entry, PROVENANCE_EVIDENCE_KEYS, PROVENANCE_EVIDENCE_KEYS, path)
        if entry["provenance_status"] != by_id[eid]["provenance_status"]:
            _fail("evidence_index_mismatch", "index provenance_status drift", path)
        if entry["source_sha256"] != by_id[eid]["source_sha256"]:
            _fail("source_hash_mismatch", "index source_sha256 drift", path)
        if entry["locator"] != by_id[eid]["locator"]:
            _fail("locator_mismatch", "index locator drift", path)

    _validate_equipment_context(bundle, by_id, register)

    for metric_index, metric in enumerate(bundle["descriptive_metrics"]):
        _strict(metric, DESCRIPTIVE_METRIC_KEYS, DESCRIPTIVE_METRIC_KEYS,
                f"$.descriptive_metrics[{metric_index}]")

    telemetry_ids = sorted(item["evidence_id"] for item in items
                           if item["evidence_type"] == "telemetry")
    manifest_entries = _manifest_entries(bundle)
    for gap_index, gap in enumerate(bundle["known_gaps"]):
        _validate_gap(gap, by_id, f"$.known_gaps[{gap_index}]", telemetry_ids,
                      manifest_entries)

    question_ids = set()
    for q_index, question in enumerate(bundle["unresolved_questions"]):
        path = f"$.unresolved_questions[{q_index}]"
        _strict(question, UNRESOLVED_QUESTION_KEYS, UNRESOLVED_QUESTION_KEYS, path)
        if question["question_id"] in question_ids:
            _fail("bad_value", "duplicate question_id", f"{path}.question_id")
        question_ids.add(question["question_id"])
        if question["verification_action"] not in VERIFICATION_ACTIONS:
            _fail("bad_value", "unknown verification_action",
                  f"{path}.verification_action")
        if not isinstance(question["applies"], bool):
            _fail("bad_type", "applies must be boolean", f"{path}.applies")
        if not isinstance(question["evidence_specific"], bool):
            _fail("bad_type", "evidence_specific must be boolean",
                  f"{path}.evidence_specific")
        if not isinstance(question["reason_supported"], bool):
            _fail("bad_type", "reason_supported must be boolean",
                  f"{path}.reason_supported")
        for field in ("group_id", "group_label"):
            if question[field] is not None and not isinstance(question[field], str):
                _fail("bad_type", f"{field} must be a string or null", f"{path}.{field}")
        for eid in question["related_evidence_ids"]:
            if eid not in by_id:
                _fail("unresolved_citation", f"related evidence {eid} not supplied", path)

    knowledge_ids = set()
    for k_index, item in enumerate(bundle["approved_knowledge"]):
        path = f"$.approved_knowledge[{k_index}]"
        _strict(item, KNOWLEDGE_ITEM_KEYS, KNOWLEDGE_ITEM_KEYS, path)
        if item["chunk_id"] in knowledge_ids:
            _fail("bad_value", "duplicate chunk_id", f"{path}.chunk_id")
        knowledge_ids.add(item["chunk_id"])
        if item["review_status"] != "approved" or item["translation_status"] != "reviewed":
            _fail("unapproved_knowledge", "approved_knowledge must be approved+reviewed",
                  path)
    if set(index["knowledge"]) != knowledge_ids:
        _fail("evidence_index_mismatch",
              "knowledge index keys != approved_knowledge chunk_ids",
              "$.provenance_index.knowledge")
    for chunk_id, entry in index["knowledge"].items():
        _strict(entry, PROVENANCE_KNOWLEDGE_KEYS, PROVENANCE_KNOWLEDGE_KEYS,
                f"$.provenance_index.knowledge[{chunk_id}]")

    for p_index, prohibition in enumerate(bundle["prohibitions"]):
        _strict(prohibition, PROHIBITION_KEYS, PROHIBITION_KEYS,
                f"$.prohibitions[{p_index}]")
    _strict(bundle["determinism"], DETERMINISM_KEYS, DETERMINISM_KEYS, "$.determinism")
    return True


def _validate_gap(gap, by_id, path, telemetry_ids=None, manifest_entries=None):
    _strict(gap, GAP_KEYS, GAP_KEYS, path)
    if gap["kind"] not in GAP_KINDS:
        _fail("unknown_gap_kind", f"unknown gap kind {gap['kind']!r}", path)
    if not isinstance(gap["evidence_ids"], list):
        _fail("bad_type", "gap evidence_ids must be a list", f"{path}.evidence_ids")
    for eid in gap["evidence_ids"]:
        if eid not in by_id:
            _fail("unresolved_citation", f"gap evidence {eid} not supplied", path)
    _validate_gap_scope(gap, path, telemetry_ids, manifest_entries)
    _validate_documentation_scope(gap, path)
    _validate_absence_specifics(gap, by_id, path, manifest_entries)


def _validate_gap_scope(gap, path, telemetry_ids, manifest_entries):
    """An absence claim must name the complete inventory/record set searched.

    The scope is either ``unavailable`` (the inventory is not retained in the
    bundle, so no absence is asserted) or ``enumerated`` and independently
    re-derived from the bundle. An inventory the bundle does not retain cannot be
    enumerated; inventing such a scope fails closed.
    """
    scope = gap["search_scope"]
    if scope is None:
        if gap["kind"] in ABSENCE_GAP_KINDS:
            _fail("unsupported_gap",
                  "an absence claim must name the complete inventory/record set searched",
                  f"{path}.search_scope")
        return
    _strict(scope, GAP_SEARCH_SCOPE_KEYS, GAP_SEARCH_SCOPE_KEYS, f"{path}.search_scope")
    scope_id = scope["scope_id"]
    if scope_id not in GAP_SCOPE_IDS:
        _fail("bad_value", f"unknown scope_id {scope_id!r}", f"{path}.search_scope.scope_id")
    status = scope["status"]
    if status not in SEARCH_SCOPE_STATUSES:
        _fail("bad_value", "unknown search_scope status", f"{path}.search_scope.status")
    if not isinstance(scope["description"], str) or not scope["description"].strip():
        _fail("bad_type", "search_scope description must be a non-empty string",
              f"{path}.search_scope.description")
    if not isinstance(scope["source_files"], list) or not all(
            isinstance(name, str) for name in scope["source_files"]):
        _fail("bad_type", "search_scope source_files must be a list of strings",
              f"{path}.search_scope.source_files")
    if status == "unavailable":
        if scope["record_count"] is not None or scope["inventory_digest_sha256"] is not None:
            _fail("unsupported_gap",
                  "an unavailable inventory must not report a count or digest",
                  f"{path}.search_scope")
        return
    count = scope["record_count"]
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        _fail("bad_type", "an enumerated scope needs a positive record_count",
              f"{path}.search_scope.record_count")
    digest = scope["inventory_digest_sha256"]
    if not (isinstance(digest, str) and _SHA256_RE.match(digest)):
        _fail("bad_type", "an enumerated scope needs a sha256 inventory digest",
              f"{path}.search_scope.inventory_digest_sha256")
    if scope_id not in VERIFIABLE_SCOPE_IDS:
        _fail("unsupported_gap",
              "this inventory is not retained in the bundle and cannot be enumerated",
              f"{path}.search_scope")
    if scope_id == "telemetry_record_set" and telemetry_ids is not None:
        if count != len(telemetry_ids) or digest != content_digest(sorted(telemetry_ids)):
            _fail("gap_scope_mismatch",
                  "declared inventory does not match the complete telemetry record set",
                  f"{path}.search_scope")
    if scope_id == "manifest_sources" and manifest_entries is not None:
        ordered = sorted(manifest_entries, key=lambda entry: entry["source_file_id"])
        expected_files = [entry["source_file_id"] for entry in ordered]
        if (not ordered or count != len(ordered)
                or digest != content_digest(ordered)
                or scope["source_files"] != expected_files):
            _fail("gap_scope_mismatch",
                  "declared inventory does not match the complete manifest source list",
                  f"{path}.search_scope")


def _validate_documentation_scope(gap, path):
    """Documentation absence is claimable only after a documentation inventory.

    Units/scaling and timestamp timezone depend on authoritative documentation
    (a field dictionary or specification). The bundle retains no such inventory,
    so those gaps must report documentation status explicitly as a non-claim
    (``status: unavailable``); claiming an enumerated documentation inventory or
    attaching a documentation scope to any other gap fails closed.
    """
    scope = gap["documentation_scope"]
    if gap["kind"] in DOCUMENTATION_GAP_KINDS:
        if scope is None:
            _fail("unsupported_gap",
                  "a documentation question must report documentation status",
                  f"{path}.documentation_scope")
    elif scope is not None:
        _fail("bad_value",
              "only documentation questions may carry a documentation_scope",
              f"{path}.documentation_scope")
    if scope is None:
        return
    _strict(scope, GAP_DOCUMENTATION_SCOPE_KEYS, GAP_DOCUMENTATION_SCOPE_KEYS,
            f"{path}.documentation_scope")
    if scope["subject"] not in DOCUMENTATION_SUBJECTS:
        _fail("bad_value", "unknown documentation subject",
              f"{path}.documentation_scope.subject")
    if scope["status"] not in SEARCH_SCOPE_STATUSES:
        _fail("bad_value", "unknown documentation status",
              f"{path}.documentation_scope.status")
    if not isinstance(scope["description"], str) or not scope["description"].strip():
        _fail("bad_type", "documentation_scope description must be a non-empty string",
              f"{path}.documentation_scope.description")
    source = scope["inventory_source"]
    if scope["status"] == "unavailable":
        if source is not None:
            _fail("unsupported_gap",
                  "an unavailable documentation status must not name an inventory source",
                  f"{path}.documentation_scope")
        return
    # No authoritative documentation inventory is retained in the bundle, so a
    # claim that documentation was searched and found absent cannot be supported.
    _fail("unsupported_gap",
          "no authoritative documentation inventory is retained in the bundle",
          f"{path}.documentation_scope")


def _validate_absence_specifics(gap, by_id, path, manifest_entries):
    """Per-kind checks that keep an absence claim inside its enumerated scope."""
    if gap["kind"] != "missing_source_hash":
        return
    detail = gap["detail"]
    file_id = detail.get("source_file_id") if isinstance(detail, dict) else None
    if not isinstance(file_id, str) or not file_id:
        _fail("unsupported_gap", "missing_source_hash needs a source_file_id",
              f"{path}.detail")
    if manifest_entries is not None and any(
            entry["source_file_id"] == file_id for entry in manifest_entries):
        _fail("unsupported_gap",
              "the manifest contains this file, so a missing hash is not supported",
              f"{path}.search_scope")
    for eid in gap["evidence_ids"]:
        if by_id[eid]["provenance_status"] != "incomplete":
            _fail("unsupported_gap",
                  "missing_source_hash evidence must have incomplete provenance", path)


# --- draft validation -------------------------------------------------------

def resolve_citations(citations, bundle):
    if not isinstance(citations, list) or not citations:
        _fail("unresolved_citation", "citations must be a non-empty list")
    evidence_index = bundle["provenance_index"]["evidence"]
    knowledge_index = bundle["provenance_index"]["knowledge"]
    resolved = []
    for index, citation in enumerate(citations):
        path = f"$.citations[{index}]"
        _strict(citation, CITATION_KEYS, CITATION_KEYS, path)
        kind = citation["type"]
        if kind not in EVIDENCE_TYPES:
            _fail("unknown_key", f"unknown citation type {kind!r}", path)
        if kind == "knowledge":
            chunk_id = citation["chunk_id"]
            entry = knowledge_index.get(chunk_id)
            if entry is None:
                _fail("unresolved_citation", f"knowledge {chunk_id} not supplied", path)
            if entry["review_status"] != "approved" or entry["translation_status"] != "reviewed":
                _fail("unapproved_knowledge", "citation uses unapproved knowledge", path)
        else:
            eid = citation["evidence_id"]
            entry = evidence_index.get(eid)
            if entry is None:
                _fail("unresolved_citation", f"evidence {eid} not supplied", path)
        if entry["provenance_status"] != "complete":
            _fail("incomplete_provenance", "citation has incomplete provenance", path)
        if citation["source_sha256"] != entry["source_sha256"]:
            _fail("source_hash_mismatch", "citation sha256 does not match source", path)
        if citation["locator"] != entry["locator"]:
            _fail("locator_mismatch", "citation locator does not match source", path)
        if citation["provenance_status"] != entry["provenance_status"]:
            _fail("incomplete_provenance", "citation provenance_status drift", path)
        resolved.append(citation)
    return resolved


def _compute(formula, values, finite):
    if formula == "count":
        return len(values)
    if not finite:
        return None
    if formula == "min":
        return min(finite)
    if formula == "max":
        return max(finite)
    if formula == "mean":
        return statistics.fmean(finite)
    if formula == "zero_count":
        return sum(1 for value in finite if value == 0)
    if formula == "zero_proportion":
        return sum(1 for value in finite if value == 0) / len(finite)
    if formula == "stdev_population":
        return statistics.pstdev(finite) if len(finite) >= 2 else None
    return None


def computation_inputs_digest(inputs):
    """Stable digest of the complete, sorted input id set (producer and validator)."""
    return content_digest(sorted(set(inputs)))


def verify_computation(computation, bundle):
    """Recompute a claim's formula over supplied evidence; return its input ids.

    Also verifies the recorded scope, input count, and complete-input digest, so a
    reviewer can reproduce the aggregate from the bundle alone.
    """
    _strict(computation, COMPUTATION_KEYS, COMPUTATION_KEYS, "$.computation")
    formula = computation["formula"]
    if formula not in FORMULAS:
        _fail("unsupported_formula", f"formula {formula!r} is not allowed",
              "$.computation.formula")
    if computation["formula_version"] != FORMULA_VERSION:
        _fail("bad_value", "unknown formula_version", "$.computation.formula_version")

    scope = computation["scope"]
    _strict(scope, COMPUTATION_SCOPE_KEYS, COMPUTATION_SCOPE_KEYS, "$.computation.scope")
    if not isinstance(scope["description"], str) or not scope["description"].strip():
        _fail("bad_type", "scope description must be a non-empty string",
              "$.computation.scope.description")
    if scope["evidence_type"] != "telemetry":
        _fail("computation_scope_mismatch", "computation scope must be telemetry evidence",
              "$.computation.scope.evidence_type")
    if scope["provenance_status"] != "complete":
        _fail("computation_scope_mismatch", "computation scope must require complete provenance",
              "$.computation.scope.provenance_status")
    excluded = scope["excluded_evidence_ids"]
    if not isinstance(excluded, list) or not all(isinstance(eid, str) for eid in excluded):
        _fail("bad_type", "scope excluded_evidence_ids must be a list of strings",
              "$.computation.scope.excluded_evidence_ids")

    inputs = computation["inputs"]
    if not isinstance(inputs, list) or not inputs:
        _fail("unsupported_claim", "computation inputs must be a non-empty list",
              "$.computation.inputs")
    if len(set(inputs)) != len(inputs):
        _fail("computation_mismatch", "computation inputs must be unique",
              "$.computation.inputs")
    evidence_index = bundle["provenance_index"]["evidence"]
    scope_code = scope["measurement_type_code"]
    values = []
    for eid in inputs:
        entry = evidence_index.get(eid)
        if entry is None:
            _fail("unresolved_citation", f"computation input {eid} not supplied",
                  "$.computation.inputs")
        if entry["provenance_status"] != "complete":
            _fail("incomplete_provenance", f"computation input {eid} has incomplete provenance",
                  "$.computation.inputs")
        if entry["evidence_type"] != "telemetry":
            _fail("unsupported_claim", "computation inputs must be telemetry evidence",
                  "$.computation.inputs")
        if scope_code is not None and entry["measurement_type_code"] != scope_code:
            _fail("computation_scope_mismatch",
                  f"input {eid} is outside the declared measurement code scope",
                  "$.computation.scope.measurement_type_code")
        if eid in excluded:
            _fail("computation_scope_mismatch",
                  f"input {eid} is declared excluded from the scope",
                  "$.computation.scope.excluded_evidence_ids")
        values.append(entry["value"])

    if computation["input_count"] != len(inputs):
        _fail("computation_mismatch",
              "declared input_count does not match the listed inputs",
              "$.computation.input_count")
    if computation["inputs_digest_sha256"] != computation_inputs_digest(inputs):
        _fail("computation_mismatch",
              "declared input digest does not match the listed inputs",
              "$.computation.inputs_digest_sha256")

    finite = [value for value in values if _finite(value)]
    computed = _compute(formula, values, finite)
    if computed is None or not _is_number(computed):
        _fail("computation_mismatch", "formula is undefined over supplied inputs",
              "$.computation")
    if _r6(float(computed)) != computation["result"]:
        _fail("computation_mismatch",
              f"recomputed {_r6(float(computed))} != declared {computation['result']}",
              "$.computation.result")
    return inputs


def _validate_relationship(relationship, claim, resolved, bundle, path):
    _strict(relationship, RELATIONSHIP_KEYS, RELATIONSHIP_KEYS, path)
    if claim["kind"] != "direct":
        _fail("unsupported_claim", "only direct claims may carry a relationship", path)
    if relationship["relation"] != "same_id_string":
        _fail("bad_value", "unknown relationship relation", f"{path}.relation")
    evidence_index = bundle["provenance_index"]["evidence"]
    telemetry_id = relationship["telemetry_evidence_id"]
    ledger_id = relationship["ledger_evidence_id"]
    telemetry_entry = evidence_index.get(telemetry_id)
    ledger_entry = evidence_index.get(ledger_id)
    if telemetry_entry is None or telemetry_entry["evidence_type"] != "telemetry":
        _fail("unsupported_claim", "relationship telemetry endpoint is not telemetry evidence",
              f"{path}.telemetry_evidence_id")
    if ledger_entry is None or ledger_entry["evidence_type"] != "ledger":
        _fail("unsupported_claim", "relationship ledger endpoint is not ledger evidence",
              f"{path}.ledger_evidence_id")
    cited = {item.get("evidence_id") for item in resolved}
    for endpoint in (telemetry_id, ledger_id):
        if endpoint not in cited:
            _fail("missing_witness",
                  "an exact-ID relationship must cite both the telemetry and ledger records",
                  path)
    caveat = relationship["caveat"]
    if not isinstance(caveat, str) or "physical identity" not in caveat.lower():
        _fail("missing_caveat",
              "the relationship caveat must state physical identity is not proven",
              f"{path}.caveat")


def validate_claim(claim, bundle):
    _strict(claim, CLAIM_KEYS, CLAIM_KEYS, "$.claims[]")
    if claim["kind"] not in CLAIM_KINDS:
        _fail("bad_value", "unknown claim kind", "$.claims[].kind")
    if not isinstance(claim["text"], str) or not claim["text"].strip():
        _fail("bad_type", "claim text must be a non-empty string", "$.claims[].text")
    scan_prohibited(claim["text"], "$.claims[].text")
    resolved = resolve_citations(claim["citations"], bundle)
    if claim["relationship"] is not None:
        _validate_relationship(claim["relationship"], claim, resolved, bundle,
                               "$.claims[].relationship")
    if claim["kind"] == "direct":
        if any(item["type"] == "knowledge" for item in resolved):
            _fail("unsupported_claim",
                  "direct claims must cite observed evidence, not knowledge",
                  "$.claims[].citations")
    else:
        computation = claim["computation"]
        if not isinstance(computation, dict):
            _fail("unsupported_claim", "computation claims require a computation block",
                  "$.claims[].computation")
        input_ids = set(verify_computation(computation, bundle))
        cited = {item["evidence_id"] for item in resolved if item.get("evidence_id")}
        if not cited.issubset(input_ids):
            _fail("unsupported_claim", "citations must be a subset of computation inputs",
                  "$.claims[].citations")
        if computation["formula"] in ("min", "max"):
            evidence_index = bundle["provenance_index"]["evidence"]
            result = computation["result"]
            extremal = {eid for eid in input_ids
                        if _finite(evidence_index[eid]["value"])
                        and _r6(float(evidence_index[eid]["value"])) == result}
            if not (cited & extremal):
                _fail("missing_witness",
                      "min/max claims must cite the record that produced the result",
                      "$.claims[].citations")
    return True


def validate_suggestion(suggestion, bundle, question_index):
    _strict(suggestion, SUGGESTION_KEYS, SUGGESTION_KEYS, "$.suggestions[]")
    question_id = suggestion["question_id"]
    question = question_index.get(question_id)
    if question is None:
        _fail("unresolved_register_question",
              f"suggestion question_id {question_id!r} is not register-backed",
              "$.suggestions[].question_id")
    if suggestion["register_id"] != question["register_id"]:
        _fail("unresolved_register_question", "register_id mismatch",
              "$.suggestions[].register_id")
    if not question.get("evidence_specific"):
        _fail("unsupported_suggestion",
              "question is not evidence-specific and cannot back a suggestion",
              "$.suggestions[].question_id")
    if not question.get("reason_supported"):
        _fail("unsupported_suggestion",
              "cited evidence does not support the specific reason for this suggestion",
              "$.suggestions[].question_id")
    if suggestion["question"] != question["question"]:
        _fail("unresolved_register_question",
              "suggestion question must be the verbatim register question",
              "$.suggestions[].question")
    if suggestion["domain"] != question["domain"]:
        _fail("unresolved_register_question", "suggestion domain must match the register topic",
              "$.suggestions[].domain")
    if suggestion["verify_action"] != question["verification_action"]:
        _fail("unresolved_register_question",
              "verify_action must be the register-derived documentary action",
              "$.suggestions[].verify_action")
    if suggestion["verify_action"] not in VERIFICATION_ACTIONS:
        _fail("bad_value", "unknown verification action", "$.suggestions[].verify_action")
    if suggestion["blocks"] != question["blocks"]:
        _fail("unresolved_register_question", "blocks must be the verbatim register blocks",
              "$.suggestions[].blocks")
    for field in ("group_id", "group_label"):
        if suggestion[field] != question[field]:
            _fail("unresolved_register_question",
                  f"suggestion {field} must match the register-derived question",
                  f"$.suggestions[].{field}")
    for field in ("why", "uncertainty_resolved"):
        if not isinstance(suggestion[field], str) or not suggestion[field].strip():
            _fail("bad_type", f"suggestion {field} must be a non-empty string",
                  f"$.suggestions[].{field}")
    scan_prohibited(suggestion["why"], "$.suggestions[].why")
    resolve_citations(suggestion["citations"], bundle)
    return True


def validate_draft(draft, bundle, register=None):
    validate_context(bundle, register)
    _strict(draft, DRAFT_ROOT_KEYS, DRAFT_ROOT_KEYS)
    if draft["draft_version"] != DRAFT_VERSION:
        _fail("bad_value", "unknown draft_version", "$.draft_version")
    if draft["contract_version"] != CONTRACT_VERSION:
        _fail("bad_value", "unknown contract_version", "$.contract_version")
    if draft["origin"] not in ORIGINS:
        _fail("bad_value", "unknown origin", "$.origin")
    if draft["data_origin"] != DATA_ORIGIN:
        _fail("bad_value", "data_origin must be observed", "$.data_origin")
    if draft["asset_id"] != bundle["asset_id"]:
        _fail("bad_value", "asset_id mismatch", "$.asset_id")
    if draft["bundle_digest_sha256"] != bundle["bundle_digest_sha256"]:
        _fail("bundle_digest_mismatch", "draft is not bound to this bundle",
              "$.bundle_digest_sha256")
    if draft["status"] not in DRAFT_STATUSES:
        _fail("bad_value", "unknown draft status", "$.status")

    if not isinstance(draft["summary"], str) or not draft["summary"].strip():
        _fail("bad_type", "summary must be a non-empty string", "$.summary")
    scan_prohibited(draft["summary"], "$.summary")

    claims = draft["claims"]
    if not isinstance(claims, list):
        _fail("bad_type", "claims must be a list", "$.claims")
    for claim in claims:
        validate_claim(claim, bundle)
    if draft["status"] == STATUS_INSUFFICIENT_EVIDENCE and claims:
        _fail("unsupported_claim",
              "insufficient_evidence result must not carry claims", "$.claims")
    if draft["status"] == STATUS_GENERATED and not claims:
        _fail("unsupported_claim", "generated result must carry at least one claim",
              "$.claims")

    if not isinstance(draft["caveats"], list) or not all(
            isinstance(item, str) for item in draft["caveats"]):
        _fail("bad_type", "caveats must be a list of strings", "$.caveats")
    for caveat in draft["caveats"]:
        scan_prohibited(caveat, "$.caveats[]")

    evidence_index = {item["evidence_id"]: item for item in bundle["evidence_items"]}
    telemetry_ids = sorted(item["evidence_id"] for item in bundle["evidence_items"]
                           if item["evidence_type"] == "telemetry")
    manifest_entries = _manifest_entries(bundle)
    for gap in draft["known_gaps"]:
        _validate_gap(gap, evidence_index, "$.known_gaps[]", telemetry_ids,
                      manifest_entries)

    question_index = {q["question_id"]: q for q in bundle["unresolved_questions"]}
    if not isinstance(draft["suggestions"], list):
        _fail("bad_type", "suggestions must be a list", "$.suggestions")
    for suggestion in draft["suggestions"]:
        validate_suggestion(suggestion, bundle, question_index)

    human_review = draft["human_review"]
    _strict(human_review, HUMAN_REVIEW_KEYS, HUMAN_REVIEW_KEYS, "$.human_review")
    if not isinstance(human_review["required"], bool):
        _fail("bad_type", "human_review.required must be boolean", "$.human_review.required")
    if human_review["status"] not in HUMAN_REVIEW_STATUSES:
        _fail("bad_value", "unknown human_review status", "$.human_review.status")
    return True


# --- schema/Python parity registry ------------------------------------------

def contract_key_registry():
    """Map each strict JSON-schema object to its Python allowed/required sets.

    ``pointer`` is a JSON pointer within the named schema file. A test resolves
    each pointer and asserts ``additionalProperties: false`` plus exact
    ``properties``/``required`` equality with these Python sets.
    """
    return [
        ("agent_context.schema.json", "", CONTEXT_ROOT_KEYS, CONTEXT_ROOT_KEYS),
        ("agent_context.schema.json", "/properties/asset_scope",
         ASSET_SCOPE_KEYS, ASSET_SCOPE_KEYS),
        ("agent_context.schema.json",
         "/properties/asset_scope/properties/ledger_match",
         LEDGER_MATCH_KEYS, LEDGER_MATCH_KEYS),
        ("agent_context.schema.json", "/properties/provenance_index",
         PROVENANCE_INDEX_KEYS, PROVENANCE_INDEX_KEYS),
        ("agent_context.schema.json",
         "/properties/provenance_index/properties/evidence/additionalProperties",
         PROVENANCE_EVIDENCE_KEYS, PROVENANCE_EVIDENCE_KEYS),
        ("agent_context.schema.json",
         "/properties/provenance_index/properties/knowledge/additionalProperties",
         PROVENANCE_KNOWLEDGE_KEYS, PROVENANCE_KNOWLEDGE_KEYS),
        ("agent_context.schema.json", "/properties/evidence_items/items",
         EVIDENCE_ITEM_KEYS, EVIDENCE_ITEM_KEYS),
        ("agent_context.schema.json", "/properties/descriptive_metrics/items",
         DESCRIPTIVE_METRIC_KEYS, DESCRIPTIVE_METRIC_KEYS),
        ("agent_context.schema.json", "/properties/known_gaps/items",
         GAP_KEYS, GAP_KEYS),
        ("agent_context.schema.json",
         "/properties/known_gaps/items/properties/search_scope",
         GAP_SEARCH_SCOPE_KEYS, GAP_SEARCH_SCOPE_KEYS),
        ("agent_context.schema.json",
         "/properties/known_gaps/items/properties/documentation_scope",
         GAP_DOCUMENTATION_SCOPE_KEYS, GAP_DOCUMENTATION_SCOPE_KEYS),
        ("agent_context.schema.json", "/properties/unresolved_questions/items",
         UNRESOLVED_QUESTION_KEYS, UNRESOLVED_QUESTION_KEYS),
        ("agent_context.schema.json", "/properties/approved_knowledge/items",
         KNOWLEDGE_ITEM_KEYS, KNOWLEDGE_ITEM_KEYS),
        ("agent_context.schema.json", "/properties/prohibitions/items",
         PROHIBITION_KEYS, PROHIBITION_KEYS),
        ("agent_context.schema.json", "/properties/determinism",
         DETERMINISM_KEYS, DETERMINISM_KEYS),
        ("agent_context.schema.json", "/properties/equipment_context",
         EQUIPMENT_CONTEXT_KEYS, EQUIPMENT_CONTEXT_KEYS),
        ("agent_context.schema.json",
         "/properties/equipment_context/properties/fields/items",
         EQUIPMENT_CONTEXT_FIELD_KEYS, EQUIPMENT_CONTEXT_FIELD_KEYS),
        ("agent_context.schema.json",
         "/properties/equipment_context/properties/dependent_unknowns/items",
         CONTEXT_UNKNOWN_KEYS, CONTEXT_UNKNOWN_KEYS),
        ("agent_context.schema.json",
         "/properties/equipment_context/properties/register_binding",
         CONTEXT_REGISTER_BINDING_KEYS, CONTEXT_REGISTER_BINDING_KEYS),
        ("agent_context.schema.json", "/$defs/locator_telemetry",
         LOCATOR_TELEMETRY_KEYS, LOCATOR_TELEMETRY_KEYS),
        ("agent_context.schema.json", "/$defs/locator_ledger",
         LOCATOR_LEDGER_KEYS, LOCATOR_LEDGER_KEYS),
        ("agent_context.schema.json", "/$defs/locator_knowledge",
         LOCATOR_KNOWLEDGE_KEYS, LOCATOR_KNOWLEDGE_KEYS),
        ("agent_draft.schema.json", "", DRAFT_ROOT_KEYS, DRAFT_ROOT_KEYS),
        ("agent_draft.schema.json", "/properties/claims/items",
         CLAIM_KEYS, CLAIM_KEYS),
        ("agent_draft.schema.json",
         "/properties/claims/items/properties/citations/items",
         CITATION_KEYS, CITATION_KEYS),
        ("agent_draft.schema.json",
         "/properties/claims/items/properties/computation",
         COMPUTATION_KEYS, COMPUTATION_KEYS),
        ("agent_draft.schema.json",
         "/properties/claims/items/properties/computation/properties/scope",
         COMPUTATION_SCOPE_KEYS, COMPUTATION_SCOPE_KEYS),
        ("agent_draft.schema.json",
         "/properties/claims/items/properties/relationship",
         RELATIONSHIP_KEYS, RELATIONSHIP_KEYS),
        ("agent_draft.schema.json", "/properties/known_gaps/items",
         GAP_KEYS, GAP_KEYS),
        ("agent_draft.schema.json",
         "/properties/known_gaps/items/properties/search_scope",
         GAP_SEARCH_SCOPE_KEYS, GAP_SEARCH_SCOPE_KEYS),
        ("agent_draft.schema.json",
         "/properties/known_gaps/items/properties/documentation_scope",
         GAP_DOCUMENTATION_SCOPE_KEYS, GAP_DOCUMENTATION_SCOPE_KEYS),
        ("agent_draft.schema.json", "/properties/suggestions/items",
         SUGGESTION_KEYS, SUGGESTION_KEYS),
        ("agent_draft.schema.json",
         "/properties/suggestions/items/properties/citations/items",
         CITATION_KEYS, CITATION_KEYS),
        ("agent_draft.schema.json", "/properties/human_review",
         HUMAN_REVIEW_KEYS, HUMAN_REVIEW_KEYS),
    ]


def topic_verification_action(topic):
    return _TOPIC_ACTION.get(topic)
