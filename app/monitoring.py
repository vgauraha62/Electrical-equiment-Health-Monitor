"""Deterministic condition-monitoring workflow (no model training).

Composes existing pieces only: evidence assembly and readiness routing from
the application, review-candidate counts from the frozen statistical baseline,
and status counts from the evidence register. No new calculations, no new
thresholds, no AI dependency for the deterministic core.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED_ANALYSIS_TYPES = {"descriptive_monitoring"}
SUPPORTED_DATASET_VERSIONS = {"1.0.0"}
WORKFLOW_VERSION = "monitoring_workflow_v1"
UNSUPPORTED_CONCLUSIONS = (
    "fault_classification",
    "failure_probability",
    "health_index",
    "maintenance_recommendation",
)


class RequestError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def validate_request(body):
    """Return (asset_id, window) or raise RequestError. Window must be absent."""
    if not isinstance(body, dict):
        raise RequestError("Request body must be a JSON object.")
    asset_id = body.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise RequestError("asset_id is required as a non-empty string.")
    analysis_type = body.get("analysis_type", "descriptive_monitoring")
    if analysis_type not in SUPPORTED_ANALYSIS_TYPES:
        raise RequestError(
            f"Unsupported analysis_type {analysis_type!r}; "
            "descriptive_monitoring is the only supported analysis.", status=422)
    dataset_version = body.get("dataset_version", "1.0.0")
    if dataset_version not in SUPPORTED_DATASET_VERSIONS:
        raise RequestError(
            f"Unsupported dataset_version {dataset_version!r}; "
            "only canonical schema version 1.0.0 is supported.")
    window = body.get("window")
    if window is not None:
        if not isinstance(window, dict) or "start" not in window or "end" not in window:
            raise RequestError("window must be null or an object with start and end.")
        raise RequestError(
            "Explicit time windows are not supported: wall-clock timestamp "
            "interpretation is ambiguous pending timezone verification. "
            "Omit window for full-coverage analysis.", status=422)
    return asset_id.strip(), None


def build_review(evidence, target_rows, total_rows, root,
                 review_persistence_configured, ai_available):
    """Assemble the monitoring-review response from existing components."""
    frozen = _load_json(root / "data/output/statistical_baseline.json") or {}
    frozen_dists = frozen.get("distributions", {})
    candidates = []
    for code in sorted(evidence.get("history_summary", {})):
        info = frozen_dists.get(code, {})
        flagged = info.get("outlier_candidates", {}) or {}
        high = flagged.get("high", []) or []
        low = flagged.get("low", []) or []
        if high or low:
            candidates.append({
                "measurement_type_code": code,
                "candidate_count": len(high) + len(low),
                "method": "tukey_fences",
                "params": {"k": info.get("tukey_k", 1.5)},
                "status": "review_candidate_only",
            })
    descriptive = {}
    for code, summary in sorted(evidence.get("history_summary", {}).items()):
        descriptive[code] = {
            "observation_count": summary.get("observation_count"),
            "minimum": summary.get("minimum"),
            "maximum": summary.get("maximum"),
            "mean": summary.get("mean"),
        }
    register = _load_json(root / "docs/evidence_register.json") or {}
    statuses = [item.get("status") for item in register.get("items", [])]
    knowledge_status = "empty"
    return {
        "request_asset_id": evidence["asset_id"],
        "analysis": {"name": "descriptive_monitoring",
                     "version": WORKFLOW_VERSION,
                     "deterministic": True},
        "provenance": {
            "source_files": evidence.get("source_files", []),
            "evidence_id_count": len(evidence.get("evidence_ids", [])),
            "decision_component": (evidence.get("decision", {}) or {}).get("component"),
            "baseline_artifact": "data/output/statistical_baseline.json"
                                 if frozen_dists else "unavailable",
        },
        "readiness": evidence.get("decision", {}),
        "population": {
            "observation_count": evidence.get("observation_count"),
            "history_observation_count": evidence.get("history_observation_count"),
            "measurement_point_count": evidence.get("measurement_point_count"),
            "history_coverage_days": evidence.get("history_coverage_days"),
            "missing_measurement_codes": evidence.get("missing_measurement_codes", []),
            "non_target_scope_excluded": max(0, total_rows - target_rows),
            "independence_note": "Repeated observations of one asset are not "
                                 "independent samples.",
        },
        "descriptive": descriptive,
        "review_candidates": candidates,
        "unknowns": {
            "quality_warnings": evidence.get("quality_warnings", []),
            "units": "unconfirmed",
            "timezone": "unconfirmed",
            "sensor_semantics": "unverified",
            "evidence_register": {
                "pending": sum(1 for s in statuses if s == "evidence_pending"),
                "unverified": sum(1 for s in statuses if s == "unverified"),
                "verified": sum(1 for s in statuses if s == "verified"),
            },
        },
        "knowledge": {"status": knowledge_status,
                      "note": "Only approved, reviewed English chunks are retrievable; "
                              "the approved knowledge base is currently empty."},
        "human_review": {
            "route": (evidence.get("decision", {}) or {}).get("review_route"),
            "persistence_configured": review_persistence_configured,
            "how": "POST /api/reviews with asset_id, outcome, rationale, evidence_ids",
        },
        "unsupported": [{"conclusion": name, "state": "blocked",
                         "reason": "No approved engineering evidence; "
                                   "readiness-only contract in force."}
                        for name in UNSUPPORTED_CONCLUSIONS],
        "ai": {"available": ai_available,
               "note": "Explains already-computed findings only; the deterministic "
                       "workflow operates identically with AI unavailable."},
    }


def build_response_id():
    return {"request_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat()}
