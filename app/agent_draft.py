"""Deterministic reference producer for the agent draft (stdlib only, no model).

There is deliberately no model call: this module builds a draft purely from the
supplied context bundle using the closed, tested formula allowlist. It proves the
contract end to end and fails closed. Every claim is either a direct copy of a
supplied evidence item or a reproducible calculation over supplied evidence ids;
every suggestion is a register-backed, documentary verification question. Nothing
is inferred, and no claim is emitted from evidence with incomplete provenance.
"""
from __future__ import annotations

import hashlib
import math
import statistics

try:
    from . import agent_contract as contract
except ImportError:  # Supports `python app/main.py` during local preview.
    import agent_contract as contract

_CAVEATS = (
    "Measurement units and scaling are not confirmed; values are raw recorded numbers.",
    "Timestamp timezone is not confirmed; no clock-time ordering is asserted.",
    "No approved engineering guidance is available; no condition statement is made.",
    "No verified outcome labels are recorded; nothing here is an equipment assessment.",
)
_SUGGESTION_CITATION_LIMIT = 6


def _r6(value):
    return round(value, 6)


def _claim_id(kind, text):
    return "claim_" + hashlib.sha1(f"{kind}|{text}".encode("utf-8")).hexdigest()[:12]


def _citation(evidence_id, index):
    entry = index[evidence_id]
    return {
        "type": entry["evidence_type"],
        "evidence_id": evidence_id,
        "chunk_id": None,
        "locator": entry["locator"],
        "source_sha256": entry["source_sha256"],
        "provenance_status": entry["provenance_status"],
    }


def _claim(kind, text, citations, computation=None, relationship=None):
    return {
        "claim_id": _claim_id(kind, text),
        "kind": kind,
        "text": text,
        "citations": citations,
        "computation": computation,
        "relationship": relationship,
    }


def _calculation_citations(inputs, index, formula, result):
    """Readable representative citations plus the record behind a min/max result."""
    picks = {inputs[0], inputs[len(inputs) // 2], inputs[-1]}
    if formula in ("min", "max"):
        for evidence_id in inputs:
            value = index[evidence_id]["value"]
            if isinstance(value, (int, float)) and not isinstance(value, bool) \
                    and _r6(float(value)) == result:
                picks.add(evidence_id)
                break
    return [_citation(evidence_id, index) for evidence_id in sorted(picks)]


def _computation(formula, scope, inputs, result):
    """Reproducible aggregate: scope + complete input set + count + digest + version."""
    return {
        "formula": formula,
        "formula_version": contract.FORMULA_VERSION,
        "scope": scope,
        "inputs": list(inputs),
        "input_count": len(inputs),
        "inputs_digest_sha256": contract.computation_inputs_digest(inputs),
        "result": result,
    }


def _code_scope(code, excluded):
    return {
        "description": (f"telemetry evidence for code {code} with complete provenance, "
                        "excluding conflicting cells"),
        "evidence_type": "telemetry",
        "measurement_type_code": code,
        "provenance_status": "complete",
        "excluded_evidence_ids": sorted(excluded),
    }


def _asset_scope_block(excluded):
    return {
        "description": ("all telemetry evidence for this asset with complete provenance, "
                        "excluding conflicting cells"),
        "evidence_type": "telemetry",
        "measurement_type_code": None,
        "provenance_status": "complete",
        "excluded_evidence_ids": sorted(excluded),
    }


def _complete_telemetry_by_code(bundle):
    by_code = {}
    for item in bundle["evidence_items"]:
        if item["evidence_type"] != "telemetry":
            continue
        by_code.setdefault(item["measurement_type_code"], []).append(item)
    return by_code


def _conflicting_ids(bundle):
    ids = set()
    for gap in bundle["known_gaps"]:
        if gap["kind"] == "conflicting_values":
            ids.update(gap["evidence_ids"])
    return ids


def _claims(bundle):
    index = bundle["provenance_index"]["evidence"]
    conflicts = _conflicting_ids(bundle)
    code_of = {item["evidence_id"]: item["measurement_type_code"]
               for item in bundle["evidence_items"]}
    claims = []
    complete_ids = []
    for code in sorted(_complete_telemetry_by_code(bundle)):
        usable = [item for item in _complete_telemetry_by_code(bundle)[code]
                  if item["provenance_status"] == "complete"
                  and item["evidence_id"] not in conflicts]
        if not usable:
            continue
        inputs = sorted(item["evidence_id"] for item in usable)
        complete_ids.extend(inputs)
        scope = _code_scope(code, {eid for eid in conflicts
                                   if code_of.get(eid) == code})
        finite = [item["value"] for item in usable
                  if isinstance(item["value"], (int, float))
                  and not isinstance(item["value"], bool)
                  and math.isfinite(item["value"])]
        yes = f"{len(inputs)} raw recorded value(s)"
        claims.append(_claim(
            "computation",
            f"Code {code}: {yes} with complete provenance (units unconfirmed).",
            _calculation_citations(inputs, index, "count", len(inputs)),
            _computation("count", scope, inputs, len(inputs))))
        if finite:
            low, high = _r6(min(finite)), _r6(max(finite))
            claims.append(_claim(
                "computation",
                f"Code {code}: minimum raw recorded value {low} (units unconfirmed).",
                _calculation_citations(inputs, index, "min", low),
                _computation("min", scope, inputs, low)))
            claims.append(_claim(
                "computation",
                f"Code {code}: maximum raw recorded value {high} (units unconfirmed).",
                _calculation_citations(inputs, index, "max", high),
                _computation("max", scope, inputs, high)))
            zeros = sum(1 for value in finite if value == 0)
            if zeros:
                claims.append(_claim(
                    "computation",
                    f"Code {code}: {zeros} raw recorded value(s) equal zero "
                    "(zero semantics unconfirmed).",
                    _calculation_citations(inputs, index, "zero_count", zeros),
                    _computation("zero_count", scope, inputs, zeros)))
    if complete_ids:
        inputs = sorted(complete_ids)
        claims.append(_claim(
            "computation",
            f"Asset {bundle['asset_id']} has {len(inputs)} raw recorded telemetry "
            "value(s) with complete provenance (units unconfirmed).",
            _calculation_citations(inputs, index, "count", len(inputs)),
            _computation("count", _asset_scope_block(conflicts), inputs, len(inputs))))
    match = bundle["asset_scope"]["ledger_match"]
    telemetry_ids = sorted(item["evidence_id"] for item in bundle["evidence_items"]
                           if item["evidence_type"] == "telemetry")
    if match["matched"] and telemetry_ids \
            and match["evidence_id"] in index \
            and index[match["evidence_id"]]["provenance_status"] == "complete":
        ledger_id = match["evidence_id"]
        telemetry_id = telemetry_ids[0]
        claims.append(_claim(
            "direct",
            "The telemetry and ledger records have the same exact ID string; "
            f"physical identity has not been verified (psr_id={bundle['asset_id']}).",
            [_citation(telemetry_id, index), _citation(ledger_id, index)],
            relationship={
                "relation": "same_id_string",
                "telemetry_evidence_id": telemetry_id,
                "ledger_evidence_id": ledger_id,
                "caveat": "matching ID strings do not prove physical identity",
            }))
    return claims


def _suggestions(bundle):
    index = bundle["provenance_index"]["evidence"]
    suggestions = []
    for question in bundle["unresolved_questions"]:
        if not question["applies"] or not question["evidence_specific"] \
                or not question["reason_supported"]:
            continue
        related = [evidence_id for evidence_id in question["related_evidence_ids"]
                   if index.get(evidence_id)
                   and index[evidence_id]["provenance_status"] == "complete"]
        related = sorted(set(related))[:_SUGGESTION_CITATION_LIMIT]
        if not related:
            continue
        suggestions.append({
            "suggestion_id": "sug_" + hashlib.sha1(
                question["question_id"].encode("utf-8")).hexdigest()[:12],
            "question_id": question["question_id"],
            "register_id": question["register_id"],
            "domain": question["domain"],
            "question": question["question"],
            "why": "; ".join(question["applies_because"]),
            "verify_action": question["verification_action"],
            "citations": [_citation(evidence_id, index) for evidence_id in related],
            "uncertainty_resolved": (
                f"Resolves register item {question['register_id']} "
                f"(topic {question['domain']}, status {question['status']}); "
                "unblocks: " + "; ".join(question["blocks"])),
            "blocks": list(question["blocks"]),
            "group_id": question["group_id"],
            "group_label": question["group_label"],
        })
    return suggestions


def claims_for_bundle(bundle):
    """The deterministic claim set for a bundle (shared, public producer).

    Public accessor used by the equipment evidence review so the review and the
    agent draft share one producer. The claims are the same direct copies and
    reproducible calculations the draft uses; callers must still validate them
    with ``contract.validate_claim`` (the review does) before presenting them.
    """
    return _claims(bundle)


def suggestions_for_bundle(bundle):
    """The register-backed documentary suggestions for a bundle (shared producer)."""
    return _suggestions(bundle)


def build_deterministic_draft(bundle, register=None):
    """Build and validate the deterministic draft; raise on any contract violation."""
    contract.validate_context(bundle, register)
    if bundle["data_origin"] != contract.DATA_ORIGIN:
        raise contract.AgentContractError("bad_value", "bundle is not observed data")
    claims = _claims(bundle)
    status = contract.STATUS_GENERATED if claims else contract.STATUS_INSUFFICIENT_EVIDENCE
    if status == contract.STATUS_GENERATED:
        codes = bundle["asset_scope"]["measurement_code_count"]
        summary = (f"Raw recorded telemetry for asset {bundle['asset_id']}: "
                   f"{len(claims)} reproducible fact(s) over {codes} measurement "
                   "code(s) with complete provenance. Units/scaling and timestamp "
                   "timezone are unconfirmed, so no engineering interpretation is made.")
    else:
        summary = ("Insufficient evidence: no claim can be supported from the supplied "
                   "evidence. Only machine-recorded gaps are reported.")
    draft = {
        "draft_version": contract.DRAFT_VERSION,
        "contract_version": contract.CONTRACT_VERSION,
        "origin": contract.PRODUCER_DETERMINISTIC,
        "data_origin": contract.DATA_ORIGIN,
        "asset_id": bundle["asset_id"],
        "bundle_digest_sha256": bundle["bundle_digest_sha256"],
        "status": status,
        "summary": summary,
        "claims": claims,
        "caveats": list(_CAVEATS),
        "known_gaps": [dict(gap) for gap in bundle["known_gaps"]],
        "suggestions": _suggestions(bundle),
        "human_review": {"required": True, "status": "pending"},
    }
    contract.validate_draft(draft, bundle, register)
    return draft


def build_insufficient_evidence_result(bundle, gaps=None, register=None):
    """Fixed insufficient-evidence result: no claims, only machine-recorded gaps."""
    contract.validate_context(bundle, register)
    draft = {
        "draft_version": contract.DRAFT_VERSION,
        "contract_version": contract.CONTRACT_VERSION,
        "origin": contract.PRODUCER_DETERMINISTIC,
        "data_origin": contract.DATA_ORIGIN,
        "asset_id": bundle["asset_id"],
        "bundle_digest_sha256": bundle["bundle_digest_sha256"],
        "status": contract.STATUS_INSUFFICIENT_EVIDENCE,
        "summary": ("Insufficient evidence: no claim can be supported from the supplied "
                    "evidence. Only machine-recorded gaps are reported."),
        "claims": [],
        "caveats": list(_CAVEATS),
        "known_gaps": [dict(gap) for gap in (gaps if gaps is not None
                                             else bundle["known_gaps"])],
        "suggestions": _suggestions(bundle),
        "human_review": {"required": True, "status": "pending"},
    }
    contract.validate_draft(draft, bundle, register)
    return draft


def rejected_result(bundle_digest_sha256, asset_id, code, message):
    """Fail-closed result for step 2: no generated claims, only the reason code."""
    return {
        "draft_version": contract.DRAFT_VERSION,
        "contract_version": contract.CONTRACT_VERSION,
        "origin": contract.PRODUCER_DETERMINISTIC,
        "data_origin": contract.DATA_ORIGIN,
        "asset_id": asset_id,
        "bundle_digest_sha256": bundle_digest_sha256,
        "status": "rejected",
        "reason_code": code,
        "message": message,
        "claims": [],
        "suggestions": [],
    }
