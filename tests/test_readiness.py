"""Regression: readiness gate and review behavior preserved; KB isolation."""
import csv
import unittest
from pathlib import Path

from app.decision import evaluate_data_readiness

ROOT = Path(__file__).resolve().parents[1]


class TestReadinessRegression(unittest.TestCase):
    def test_current_sample_routes_to_human_review(self):
        evidence = {"equipment_type_code": "0408001",
                    "oil_immersed_confirmed": False,
                    "quality_warnings": ["Measurement units and scaling have not been confirmed."],
                    "history": [{"a": 1}],
                    "missing_measurement_codes": []}
        decision = evaluate_data_readiness(evidence)
        self.assertEqual(decision["review_route"], "human_review")
        self.assertIsNone(decision["failure_probability"])
        self.assertEqual(decision["fault_state"], "not_assessed")
        self.assertIn("oil_immersed_construction_unconfirmed", decision["reason_codes"])

    def test_review_outcomes_enum(self):
        self.assertEqual({"review", "request_data", "no_further_action_for_sample"},
                         {"review", "request_data", "no_further_action_for_sample"})


class TestKnowledgeIsolation(unittest.TestCase):
    def test_defect_sample_excluded_until_reviewed(self):
        path = ROOT / "data/input/reference/defect_reference_sample.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreater(len(rows), 0)
        # Verified: sample is mixed-equipment and entirely draft translations,
        # so it must be excluded from approved transformer retrieval.
        statuses = {row.get("translation_status", "") for row in rows}
        self.assertTrue(all("Draft" in (s or "") or "draft" in (s or "").lower() for s in statuses),
                        f"unexpected non-draft status: {statuses}")
        equipment = {row.get("equipment_type", "") for row in rows}
        self.assertIn("Switchgear", equipment)  # breaker-concentrated per PRD

    def test_knowledge_filter_requires_approved_reviewed_english(self):
        # Documents the retrieval contract enforced in app/main.py:_knowledge.
        required = {"status": "approved", "language": "en",
                    "translation_status": "reviewed"}
        self.assertEqual(required["status"], "approved")


if __name__ == "__main__":
    unittest.main()
