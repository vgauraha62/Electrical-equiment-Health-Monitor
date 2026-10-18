"""Explicit unit handling. Units in current exports are unconfirmed.

Never infer a unit from a measurement name. Normalization only applies
when a verified mapping exists; otherwise values stay unconfirmed.
"""
from __future__ import annotations

UNIT_STATUS_UNCONFIRMED = "unconfirmed"

# Verified conversions only. Empty by design until a domain reviewer
# confirms source units for each measurement_type_code.
VERIFIED_CONVERSIONS = {}


def normalize(value, original_unit, measurement_type_code):
    """Return (normalized_value, normalized_unit, unit_status).

    With no verified mappings, always returns the original value with
    unit_status='unconfirmed' and normalized_unit=None.
    """
    key = (measurement_type_code, original_unit)
    if key in VERIFIED_CONVERSIONS:
        factor, target = VERIFIED_CONVERSIONS[key]
        return value * factor if value is not None else None, target, "verified"
    return value, None, UNIT_STATUS_UNCONFIRMED
