"""Conservative, transparent review routing for the current unlabeled sample.

This module checks evidence readiness only. It does not diagnose failure or
apply electrical limits. Replace it with a validated decision model only after
engineer-reviewed examples and thresholds are available.
"""

from __future__ import annotations


def evaluate_data_readiness(evidence: dict) -> dict:
    warnings = list(evidence.get("quality_warnings", []))
    reasons = []

    if evidence.get("equipment_type_code") != "0408001":
        reasons.append("equipment_class_not_in_transformer_poc_scope")
    if not evidence.get("oil_immersed_confirmed", False):
        reasons.append("oil_immersed_construction_unconfirmed")
    if any("unit" in warning.lower() for warning in warnings):
        reasons.append("measurement_units_unconfirmed")
    if not evidence.get("history"):
        reasons.append("historical_evidence_missing")
    if evidence.get("missing_measurement_codes"):
        reasons.append("expected_measurements_missing")

    return {
        "component": "data_readiness_rules_v1",
        "mode": "assist_only",
        "data_sufficiency": "needs_review" if reasons else "available_for_descriptive_review",
        "review_route": "human_review" if reasons else "analyst_review",
        "reason_codes": reasons,
        "fault_state": "not_assessed",
        "failure_probability": None,
        "explanation": "This component checks evidence readiness only. It does not classify transformer condition or predict failure.",
    }
