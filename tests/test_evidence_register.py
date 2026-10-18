"""Tests for the evidence-register validator (stdlib unittest, synthetic only)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_evidence_register import (REQUIRED_CODES, RegisterError,  # noqa: E402
                                                validate_register)


def item(item_id="EV-X", status="unverified", code=None, **overrides):
    entry = {"id": item_id, "topic": "t", "question": "q?", "status": status,
             "evidence_required": "e", "reviewer_required": "r",
             "applicability": "target_asset",
             "blocks": ["downstream decision"]}
    if code is not None:
        entry["measurement_code"] = code
    entry.update(overrides)
    return entry


def full_register(status="unverified"):
    items = [item(f"EV-CODE-{code}", status=status, code=code)
             for code in REQUIRED_CODES]
    items.append(item("EV-OTHER", status=status))
    return {"register_version": "1.0.0", "items": items}


class TestRegisterValidator(unittest.TestCase):
    def test_valid_register_passes(self):
        count, statuses = validate_register(full_register())
        self.assertEqual(count, len(REQUIRED_CODES) + 1)
        self.assertEqual(statuses, {"unverified": len(REQUIRED_CODES) + 1})

    def test_real_register_passes_with_zero_verified(self):
        register = json.loads((ROOT / "docs/evidence_register.json").read_text())
        count, statuses = validate_register(register)
        self.assertGreater(count, 0)
        self.assertEqual(statuses.get("verified", 0), 0)

    def test_duplicate_ids_rejected(self):
        register = full_register()
        register["items"].append(item("EV-OTHER"))
        with self.assertRaises(RegisterError):
            validate_register(register)

    def test_missing_fields_rejected(self):
        register = full_register()
        del register["items"][0]["blocks"]
        with self.assertRaises(RegisterError):
            validate_register(register)

    def test_invalid_status_rejected(self):
        register = full_register()
        register["items"][0]["status"] = "confirmed"
        with self.assertRaises(RegisterError):
            validate_register(register)

    def test_verified_without_evidence_rejected(self):
        register = full_register(status="verified")
        with self.assertRaises(RegisterError):
            validate_register(register)

    def test_verified_with_evidence_and_approval_passes(self):
        register = full_register()
        register["items"][0].update(
            status="verified", evidence_location="docs/std.pdf p.3",
            approval={"reviewed_by": "domain reviewer", "reviewed_at": "2026-01-01"})
        count, statuses = validate_register(register)
        self.assertEqual(statuses.get("verified"), 1)

    def test_missing_measurement_code_rejected(self):
        register = full_register()
        register["items"] = [i for i in register["items"]
                             if i.get("measurement_code") != "TotW"]
        with self.assertRaises(RegisterError):
            validate_register(register)


if __name__ == "__main__":
    unittest.main()
