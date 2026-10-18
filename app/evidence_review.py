"""Read-only equipment evidence review (stdlib only, deterministic, no model).

This module is the equipment evidence review flow: a thin, deterministic
projection layered on the already-validated asset evidence profile
(``app/asset_evidence.py``) and the context bundle it projects
(``app/agent_context.py::build_context_bundle``). It organizes the recorded
equipment context with the asset's available operating evidence, states what the
supplied records support, shows the sources, and surfaces the unresolved
questions that limit only the conclusions depending on them.

It adds no new source, no inference, and no engineering conclusion:

- every supported fact/calculation is the deterministic, citation-validated
  reference producer output (``app/agent_draft.py``); each citation must resolve
  to the provenance index with complete provenance *and* actually support the
  specific statement (citations must be a subset of the computation inputs, with
  the extremal record cited for min/max), so a fabricated, mismatched,
  irrelevant, or merely-resolvable citation is rejected;
- technical references come only from the repository's approved-chunk predicate
  (approved + english + reviewed + matching equipment type code); unapproved or
  unreviewed chunks are never promoted and model pretraining is never treated as
  an approved source;
- when no approved relevant reference exists the review still shows the supported
  facts, sources, unresolved questions, and non-operational checks, and states
  that no approved reference supports an engineering interpretation;
- register-backed questions remain verbatim and limit only the conclusions that
  depend on them.

There is no model call, no cloud call, and nothing is written.
"""
from __future__ import annotations

from pathlib import Path

try:
    from . import agent_contract as contract
    from . import agent_context as context
    from . import agent_draft
    from . import asset_evidence
except ImportError:  # Supports `python app/main.py` during local preview.
    import agent_contract as contract
    import agent_context as context
    import agent_draft
    import asset_evidence

REVIEW_VERSION = "equipment_evidence_review.v1"
ROOT = Path(__file__).resolve().parents[1]

REFERENCE_STORE_ID = "approved_chunk_store"
# Gap kinds the bundle records for an in-scope chunk that could not support a
# claim. Summed with the approved items they name every in-scope chunk examined.
KNOWLEDGE_GAP_KINDS = (
    "unapproved_knowledge", "unreviewed_translation", "missing_knowledge_provenance",
)
REFERENCE_STATES = (
    "approved_relevant_knowledge_available",
    "no_approved_relevant_knowledge_available",
)

# --- allowed-key sets (this module owns the projection shape; it is not an
# evidence contract and is not mirrored by a JSON schema) ---------------------

REVIEW_KEYS = {
    "review_version", "contract_version", "asset_id", "data_origin",
    "read_only", "model_call", "bundle_digest_sha256", "profile_version",
    "profile_digest_sha256", "supported_evidence", "cited_claims",
    "approved_references", "unresolved_questions", "human_checks",
    "limitations", "caveats", "prohibitions",
}
SUPPORTED_KEYS = {
    "claim_count", "measurement_code_count", "observation_count", "statement",
    "units_status", "timestamp_timezone_status",
}
CLAIM_KEYS = contract.CLAIM_KEYS | {"support"}
SUPPORT_KEYS = {
    "basis", "citation_count", "input_count", "citations_within_inputs",
    "relevance", "checks",
}
REFERENCES_KEYS = {
    "state", "store_id", "considered_chunk_count", "approved_relevant_count",
    "retrieval_basis", "equipment_type_code", "ledger_context_terms", "items",
    "statement", "excluded_note",
}
QUESTION_KEYS = {
    "question_id", "register_id", "topic", "question", "status", "blocks",
    "applies", "verification_action",
}
CHECK_KEYS = {
    "check_id", "question_id", "register_id", "topic", "question", "why",
    "verify_action", "citations", "blocks", "non_operational",
}
LIMITATION_KEYS = {
    "dimension", "status", "open_register_ids", "blocking_question_ids",
    "blocking_gap_ids", "blocks", "statement",
}


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


# --- projections of the validated bundle -------------------------------------

def _support(claim, bundle):
    """Per-claim relevance audit: does each citation support *this* statement?

    ``citations_resolve`` and ``provenance_complete`` are enforced by
    ``contract.validate_claim`` before this runs. This records that computation
    citations are a subset of the formula's inputs (so a citation that merely
    resolves but is not part of the calculation is ``unsupported``), that a
    min/max claim cites the extremal record, and that an exact-ID relationship
    cites both endpoints.
    """
    resolved = contract.resolve_citations(claim["citations"], bundle)
    checks = ["citations_resolve", "provenance_complete"]
    if claim["kind"] == "computation":
        input_ids = set(contract.verify_computation(claim["computation"], bundle))
        cited = {item["evidence_id"] for item in resolved if item.get("evidence_id")}
        within = cited.issubset(input_ids)
        checks.append("citations_within_inputs")
        if claim["computation"]["formula"] in ("min", "max"):
            checks.append("extremal_witness")
        return {
            "basis": "computation",
            "citation_count": len(resolved),
            "input_count": claim["computation"]["input_count"],
            "citations_within_inputs": bool(within),
            "relevance": "supported" if within else "unsupported",
            "checks": checks,
        }
    if claim["relationship"] is not None:
        checks.append("relationship_witnesses")
    return {
        "basis": "direct",
        "citation_count": len(resolved),
        "input_count": None,
        "citations_within_inputs": None,
        "relevance": "supported",
        "checks": checks,
    }


def _cited_claims(bundle):
    """The deterministic claims with claim-level citations and a support audit."""
    claims = []
    for claim in agent_draft.claims_for_bundle(bundle):
        contract.validate_claim(claim, bundle)
        entry = {key: claim[key] for key in claim}
        entry["support"] = _support(claim, bundle)
        claims.append(entry)
    return claims


def _supported_evidence(bundle, claims):
    scope = bundle["asset_scope"]
    return {
        "claim_count": len(claims),
        "measurement_code_count": scope["measurement_code_count"],
        "observation_count": scope["observation_count"],
        "statement": (
            f"Supplied telemetry supports {len(claims)} reproducible fact(s) and "
            f"calculation(s) over {scope['measurement_code_count']} measurement "
            f"code(s) for this asset, each traced to its source records. Units and "
            "scaling are unconfirmed and the timestamp timezone is unconfirmed, so "
            "no engineering interpretation is offered."),
        "units_status": "unconfirmed",
        "timestamp_timezone_status": "unconfirmed",
    }


def _ledger_context_terms(bundle):
    terms = []
    for entry in bundle["equipment_context"]["fields"]:
        if entry["usable_for_context"] and entry["value"] not in (None, ""):
            terms.append(f"{entry['field']}={entry['value']}")
    return terms


def _approved_references(bundle):
    items = [dict(item) for item in bundle["approved_knowledge"]]
    gaps = [gap for gap in bundle["known_gaps"] if gap["kind"] in KNOWLEDGE_GAP_KINDS]
    considered = len(items) + len(gaps)
    code = bundle["asset_scope"]["equipment_type_code"]
    if items:
        state = "approved_relevant_knowledge_available"
        statement = (
            f"{len(items)} approved, reviewed reference chunk(s) match equipment "
            f"type {code}; each is source-linked and available to a person. An "
            "approved reference may inform an engineering interpretation, and the "
            "app still states none by itself.")
    else:
        state = "no_approved_relevant_knowledge_available"
        detail = (f"0 of {considered} in-scope chunk(s) are approved, English, "
                  "reviewed, and source-hashed." if considered else
                  "the approved-chunk store supplied no in-scope chunk.")
        statement = (
            f"No approved relevant knowledge is available for equipment type {code}: "
            f"{detail} No engineering interpretation is offered from references; "
            "model pretraining is not an approved source.")
    return {
        "state": state,
        "store_id": REFERENCE_STORE_ID,
        "considered_chunk_count": considered,
        "approved_relevant_count": len(items),
        "retrieval_basis": ("approved + english + reviewed + matching "
                            "equipment_type_code"),
        "equipment_type_code": code,
        "ledger_context_terms": _ledger_context_terms(bundle),
        "items": items,
        "statement": statement,
        "excluded_note": (
            "Unapproved, unreviewed, out-of-scope, or unhashed chunks are never "
            "promoted; model pretraining is never treated as an approved source."),
    }


def _unresolved_questions(bundle):
    return [{
        "question_id": question["question_id"],
        "register_id": question["register_id"],
        "topic": question["domain"],
        "question": question["question"],
        "status": question["status"],
        "blocks": list(question["blocks"]),
        "applies": bool(question["applies"]),
        "verification_action": question["verification_action"],
    } for question in bundle["unresolved_questions"]]


def _human_checks(bundle):
    question_index = {question["question_id"]: question
                      for question in bundle["unresolved_questions"]}
    checks = []
    for suggestion in agent_draft.suggestions_for_bundle(bundle):
        contract.validate_suggestion(suggestion, bundle, question_index)
        checks.append({
            "check_id": suggestion["suggestion_id"],
            "question_id": suggestion["question_id"],
            "register_id": suggestion["register_id"],
            "topic": suggestion["domain"],
            "question": suggestion["question"],
            "why": suggestion["why"],
            "verify_action": suggestion["verify_action"],
            "citations": [dict(citation) for citation in suggestion["citations"]],
            "blocks": list(suggestion["blocks"]),
            "non_operational": True,
        })
    return checks


def _limitations(bundle):
    """Each open dimension with the specific evidence that limits it.

    Only the conclusions that depend on an open dimension are limited; the
    supported facts and calculations are unaffected and stay visible.
    """
    questions = bundle["unresolved_questions"]
    gaps = bundle["known_gaps"]
    limitations = []
    for dimension in bundle["equipment_context"]["dependent_unknowns"]:
        open_ids = set(dimension["open_register_ids"])
        related = [question for question in questions
                   if question["register_id"] in open_ids]
        blocks = sorted({block for question in related
                         for block in question["blocks"]})
        limitations.append({
            "dimension": dimension["dimension"],
            "status": dimension["status"],
            "open_register_ids": list(dimension["open_register_ids"]),
            "blocking_question_ids": sorted(question["question_id"]
                                            for question in related),
            "blocking_gap_ids": sorted(gap["gap_id"] for gap in gaps
                                       if gap.get("register_id") in open_ids),
            "blocks": blocks,
            "statement": (
                f"{dimension['dimension']}: {dimension['status']}. "
                f"{len(open_ids)} open register item(s) limit only the conclusions "
                "that depend on this dimension; other supported facts and "
                "calculations are unaffected."),
        })
    return limitations


# --- public builder / validator ---------------------------------------------

def build_evidence_review(asset_id, *, root=None, rows=None, bundle=None,
                          profile=None, chunks=None, register=None):
    """Build the read-only equipment evidence review, or ``None`` when no evidence.

    ``None`` means the asset has no telemetry evidence in the target class, so the
    caller fails closed (404). Any contract violation raises ``AgentContractError``.
    ``bundle`` and ``profile`` may be supplied by a caller that already built them
    for the same asset; they are still re-derived and validated here.
    """
    root = Path(root or ROOT)
    if bundle is None:
        bundle = context.build_context_bundle(asset_id, root=root, rows=rows,
                                              chunks=chunks)
    if bundle is None:
        return None
    contract.validate_context(bundle, register)
    if profile is None:
        profile = asset_evidence.build_asset_evidence(
            asset_id, root=root, rows=rows, chunks=chunks, bundle=bundle,
            register=register)
    if profile is None:
        return None
    claims = _cited_claims(bundle)
    review = {
        "review_version": REVIEW_VERSION,
        "contract_version": contract.CONTRACT_VERSION,
        "asset_id": bundle["asset_id"],
        "data_origin": contract.DATA_ORIGIN,
        "read_only": True,
        "model_call": False,
        "bundle_digest_sha256": bundle["bundle_digest_sha256"],
        "profile_version": profile["profile_version"],
        "profile_digest_sha256": contract.content_digest(profile),
        "supported_evidence": _supported_evidence(bundle, claims),
        "cited_claims": claims,
        "approved_references": _approved_references(bundle),
        "unresolved_questions": _unresolved_questions(bundle),
        "human_checks": _human_checks(bundle),
        "limitations": _limitations(bundle),
        "caveats": list(profile["caveats"]),
        "prohibitions": [dict(item) for item in profile["prohibitions"]],
    }
    validate_evidence_review(review, bundle=bundle, profile=profile,
                             register=register)
    return review


def validate_evidence_review(review, bundle=None, profile=None, register=None):
    """Fail-closed validation of an equipment evidence review.

    Structural checks always run. When ``bundle`` is supplied the whole review is
    re-derived from that validated bundle and required to match, so an altered
    claim, a drifted citation, a promoted chunk, or a forged limitation cannot
    survive. Each cited claim is re-validated for citation relevance, and the
    authored prose is scanned for prohibited conclusions. When ``profile`` is
    supplied the review is also required to be bound to that profile.
    """
    _strict(review, REVIEW_KEYS, REVIEW_KEYS)
    if review["review_version"] != REVIEW_VERSION:
        _fail("bad_value", "unknown review_version", "$.review_version")
    if review["contract_version"] != contract.CONTRACT_VERSION:
        _fail("bad_value", "unknown contract_version", "$.contract_version")
    if review["data_origin"] != contract.DATA_ORIGIN:
        _fail("bad_value", "data_origin must be observed", "$.data_origin")
    if review["read_only"] is not True:
        _fail("bad_value", "the review must be read-only", "$.read_only")
    if review["model_call"] is not False:
        _fail("bad_value", "the review must make no model call", "$.model_call")

    _strict(review["supported_evidence"], SUPPORTED_KEYS, SUPPORTED_KEYS,
            "$.supported_evidence")
    contract.scan_prohibited(review["supported_evidence"]["statement"],
                             "$.supported_evidence.statement")

    references = review["approved_references"]
    _strict(references, REFERENCES_KEYS, REFERENCES_KEYS, "$.approved_references")
    if references["state"] not in REFERENCE_STATES:
        _fail("bad_value", "unknown reference state", "$.approved_references.state")
    for field in ("statement", "excluded_note"):
        contract.scan_prohibited(references[field],
                                 f"$.approved_references.{field}")
    if references["approved_relevant_count"] != len(references["items"]):
        _fail("bad_value", "approved_relevant_count must equal the item count",
              "$.approved_references.approved_relevant_count")

    for index, claim in enumerate(review["cited_claims"]):
        path = f"$.cited_claims[{index}]"
        _strict(claim, CLAIM_KEYS, CLAIM_KEYS, path)
        _strict(claim["support"], SUPPORT_KEYS, SUPPORT_KEYS, f"{path}.support")
        core = {key: claim[key] for key in contract.CLAIM_KEYS}
        contract.validate_claim(core, bundle)
        if claim["support"] != _support(core, bundle):
            _fail("unsupported_claim", "claim support audit does not re-derive",
                  f"{path}.support")

    for index, question in enumerate(review["unresolved_questions"]):
        _strict(question, QUESTION_KEYS, QUESTION_KEYS,
                f"$.unresolved_questions[{index}]")
        if question["verification_action"] not in contract.VERIFICATION_ACTIONS:
            _fail("bad_value", "unknown verification action",
                  f"$.unresolved_questions[{index}].verification_action")

    for index, check in enumerate(review["human_checks"]):
        path = f"$.human_checks[{index}]"
        _strict(check, CHECK_KEYS, CHECK_KEYS, path)
        if check["non_operational"] is not True:
            _fail("bad_value", "human checks must be non-operational",
                  f"{path}.non_operational")
        if check["verify_action"] not in contract.VERIFICATION_ACTIONS:
            _fail("bad_value", "unknown verification action", f"{path}.verify_action")

    for index, limitation in enumerate(review["limitations"]):
        _strict(limitation, LIMITATION_KEYS, LIMITATION_KEYS,
                f"$.limitations[{index}]")
        contract.scan_prohibited(limitation["statement"],
                                 f"$.limitations[{index}].statement")

    for index, caveat in enumerate(review["caveats"]):
        if not isinstance(caveat, str) or not caveat.strip():
            _fail("bad_type", "caveat must be a non-empty string",
                  f"$.caveats[{index}]")
        contract.scan_prohibited(caveat, f"$.caveats[{index}]")
    for index, prohibition in enumerate(review["prohibitions"]):
        _strict(prohibition, contract.PROHIBITION_KEYS, contract.PROHIBITION_KEYS,
                f"$.prohibitions[{index}]")

    if bundle is None:
        return True

    if review["asset_id"] != bundle["asset_id"]:
        _fail("bad_value", "asset_id mismatch", "$.asset_id")
    if review["bundle_digest_sha256"] != bundle["bundle_digest_sha256"]:
        _fail("bundle_digest_mismatch", "review is not bound to this bundle",
              "$.bundle_digest_sha256")
    if profile is not None:
        if review["profile_version"] != profile["profile_version"]:
            _fail("bad_value", "profile_version mismatch", "$.profile_version")
        if review["profile_digest_sha256"] != contract.content_digest(profile):
            _fail("unsupported_context", "review is not bound to this profile",
                  "$.profile_digest_sha256")

    expected = _cited_claims(bundle)
    if review["cited_claims"] != expected:
        _fail("unsupported_claim",
              "cited claims do not re-derive from the validated bundle",
              "$.cited_claims")
    if review["supported_evidence"] != _supported_evidence(bundle, expected):
        _fail("unsupported_claim", "supported evidence does not re-derive",
              "$.supported_evidence")
    if review["approved_references"] != _approved_references(bundle):
        _fail("unsupported_context", "approved references do not re-derive",
              "$.approved_references")
    if review["unresolved_questions"] != _unresolved_questions(bundle):
        _fail("unsupported_context", "unresolved questions do not re-derive",
              "$.unresolved_questions")
    if review["human_checks"] != _human_checks(bundle):
        _fail("unsupported_suggestion", "human checks do not re-derive",
              "$.human_checks")
    if review["limitations"] != _limitations(bundle):
        _fail("unsupported_conclusion", "limitations do not re-derive",
              "$.limitations")
    return True
