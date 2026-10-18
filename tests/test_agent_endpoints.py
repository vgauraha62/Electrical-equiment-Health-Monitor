"""Gated read-only agent endpoint tests (stdlib unittest, no cloud calls).

Proves the two routes are disabled by default, read-only, contract-valid, and
free of prohibited conclusions; and that they create no review case.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.main as main_module  # noqa: E402
from app import agent_contract as contract  # noqa: E402
from app import agent_context as context  # noqa: E402

TARGET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")
FORBIDDEN_KEYS = {
    "failure_probability", "fault_state", "fault_classification", "fault_diagnosis",
    "health_index", "remaining_life", "remaining_useful_life", "maintenance_action",
    "work_order", "severity", "probability",
}


def forbidden_keys_in(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                found.add(key)
            found |= forbidden_keys_in(item)
    elif isinstance(value, list):
        for item in value:
            found |= forbidden_keys_in(item)
    return found


class AgentEndpointTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()

    def setUp(self):
        main_module.ENABLE_AGENT_DRAFT = False

    def tearDown(self):
        main_module.ENABLE_AGENT_DRAFT = False

    def enable(self):
        main_module.ENABLE_AGENT_DRAFT = True

    def test_disabled_by_default(self):
        self.assertFalse(main_module.ENABLE_AGENT_DRAFT)
        context_response = self.client.get(f"/api/agent/context/{TARGET}")
        draft_response = self.client.get(f"/api/agent/draft/{TARGET}")
        self.assertEqual(context_response.status_code, 404)
        self.assertEqual(draft_response.status_code, 404)
        self.assertIn("disabled", context_response.get_json()["error"])

    def test_health_reports_flag(self):
        self.assertFalse(self.client.get("/api/health").get_json()["agent_draft_enabled"])
        self.enable()
        self.assertTrue(self.client.get("/api/health").get_json()["agent_draft_enabled"])

    def test_context_bundle_matches_library_and_validates(self):
        self.enable()
        response = self.client.get(f"/api/agent/context/{TARGET}")
        self.assertEqual(response.status_code, 200)
        bundle = response.get_json()
        self.assertTrue(contract.validate_context(bundle))
        self.assertEqual(bundle["bundle_digest_sha256"],
                         context.build_context_bundle(TARGET)["bundle_digest_sha256"])

    def test_draft_validates_and_resolves_citations(self):
        self.enable()
        bundle = self.client.get(f"/api/agent/context/{TARGET}").get_json()
        response = self.client.get(f"/api/agent/draft/{TARGET}")
        self.assertEqual(response.status_code, 200)
        draft = response.get_json()
        self.assertEqual(draft["status"], contract.STATUS_GENERATED)
        self.assertTrue(contract.validate_draft(draft, bundle))
        index = bundle["provenance_index"]["evidence"]
        for claim in draft["claims"]:
            self.assertTrue(claim["citations"])
            for citation in claim["citations"]:
                self.assertEqual(citation["source_sha256"],
                                 index[citation["evidence_id"]]["source_sha256"])

    def test_unknown_asset_returns_404(self):
        self.enable()
        self.assertEqual(
            self.client.get("/api/agent/context/no-such-asset").status_code, 404)
        self.assertEqual(
            self.client.get("/api/agent/draft/no-such-asset").status_code, 404)

    def test_no_prohibited_keys_in_responses(self):
        self.enable()
        for path in (f"/api/agent/context/{TARGET}", f"/api/agent/draft/{TARGET}"):
            body = self.client.get(path).get_json()
            self.assertEqual(forbidden_keys_in(body), set())
            self.assertIsNone(CJK.search(json.dumps(body, ensure_ascii=False)))

    def test_no_review_case_created(self):
        self.enable()
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("REVIEW_CASES_DIR")
            os.environ["REVIEW_CASES_DIR"] = tmp
            try:
                self.client.get(f"/api/agent/context/{TARGET}")
                self.client.get(f"/api/agent/draft/{TARGET}")
                self.assertEqual(os.listdir(tmp), [])
            finally:
                if previous is None:
                    os.environ.pop("REVIEW_CASES_DIR", None)
                else:
                    os.environ["REVIEW_CASES_DIR"] = previous

    def test_class_level_questions_stay_unresolved_but_unsuggested(self):
        self.enable()
        bundle = self.client.get(f"/api/agent/context/{TARGET}").get_json()
        draft = self.client.get(f"/api/agent/draft/{TARGET}").get_json()
        questions = {q["question_id"]: q for q in bundle["unresolved_questions"]}
        suggested = {s["question_id"] for s in draft["suggestions"]}
        for register_id in ("EV-RULES-APPROVED", "EV-OUTCOMES-LABELED",
                            "EV-COVERAGE-EVAL", "EV-TIME-TIMEZONE"):
            self.assertTrue(questions[register_id]["applies"])
            self.assertFalse(questions[register_id]["evidence_specific"])
            self.assertNotIn(register_id, suggested)

    def test_per_code_unit_questions_keep_group_but_are_unsuggested(self):
        self.enable()
        bundle = self.client.get(f"/api/agent/context/{TARGET}").get_json()
        draft = self.client.get(f"/api/agent/draft/{TARGET}").get_json()
        questions = [q for q in bundle["unresolved_questions"]
                     if q["question_id"].startswith("EV-CODE-")]
        self.assertEqual(len(questions), 14)
        self.assertEqual({q["group_id"] for q in questions}, {"grp_units_scaling"})
        self.assertEqual({q["group_label"] for q in questions},
                         {"Measurement unit and scaling (per measurement code)"})
        # Readings do not establish that unit/scaling is undocumented, so no
        # per-code suggestion is emitted and the whole group stays unresolved.
        self.assertFalse(any(q["reason_supported"] for q in questions))
        self.assertEqual([s for s in draft["suggestions"] if s["group_id"]], [])

    def test_aggregates_record_scope_count_and_digest(self):
        self.enable()
        bundle = self.client.get(f"/api/agent/context/{TARGET}").get_json()
        draft = self.client.get(f"/api/agent/draft/{TARGET}").get_json()
        seen = 0
        for claim in draft["claims"]:
            computation = claim["computation"]
            if not computation:
                continue
            seen += 1
            self.assertEqual(computation["formula_version"], contract.FORMULA_VERSION)
            self.assertEqual(computation["input_count"], len(computation["inputs"]))
            self.assertEqual(computation["inputs_digest_sha256"],
                             contract.computation_inputs_digest(computation["inputs"]))
            self.assertEqual(contract.verify_computation(computation, bundle),
                             computation["inputs"])
        self.assertTrue(seen)

    def test_absence_gaps_name_the_complete_record_set(self):
        self.enable()
        bundle = self.client.get(f"/api/agent/context/{TARGET}").get_json()
        telemetry_ids = sorted(item["evidence_id"] for item in bundle["evidence_items"]
                               if item["evidence_type"] == "telemetry")
        kinds = set()
        for gap in bundle["known_gaps"]:
            if gap["kind"] not in contract.ABSENCE_GAP_KINDS:
                continue
            kinds.add(gap["kind"])
            scope = gap["search_scope"]
            self.assertEqual(scope["status"], "enumerated")
            self.assertEqual(scope["record_count"], len(telemetry_ids))
            self.assertEqual(scope["inventory_digest_sha256"],
                             contract.computation_inputs_digest(telemetry_ids))
        self.assertIn("unknown_units_scaling", kinds)

    def test_equipment_context_is_carried_and_reviewed(self):
        self.enable()
        bundle = self.client.get(f"/api/agent/context/{TARGET}").get_json()
        block = bundle["equipment_context"]
        self.assertTrue(block["matched"])
        self.assertTrue(block["context_usable"])
        self.assertIsNone(block["blocked_reason_code"])
        self.assertEqual(block["status_label"], "translation reviewed")
        self.assertEqual(block["translation_status"], "reviewed")
        self.assertEqual(block["review_status"], "approved")
        self.assertEqual({u["dimension"] for u in block["dependent_unknowns"]},
                         set(contract.CONTEXT_UNKNOWN_DIMENSIONS))
        # The reviewed translation is not an engineering conclusion: every
        # dependent conclusion still needs its own verified register item.
        self.assertTrue(all(u["status"] == "unknown"
                            for u in block["dependent_unknowns"]))
        binding = block["register_binding"]
        self.assertEqual(binding["register_path"], contract.CONTEXT_REGISTER_ID)
        self.assertEqual(binding["register_version"], "1.0.0")
        self.assertEqual(binding["item_count"],
                         contract.register_item_count(
                             contract.load_authoritative_register()))
        self.assertTrue(block["fields"])
        self.assertTrue(any(field["usable_for_context"] for field in block["fields"]))
        draft = self.client.get(f"/api/agent/draft/{TARGET}").get_json()
        prose = " ".join(claim["text"] for claim in draft["claims"])
        for needle in ("oil-immersed", "S11-M-400/10", "10 kV AC"):
            self.assertNotIn(needle, prose)

    def test_exact_id_claim_cites_both_records_and_keeps_caveat(self):
        self.enable()
        draft = self.client.get(f"/api/agent/draft/{TARGET}").get_json()
        direct = [c for c in draft["claims"] if c["kind"] == "direct"]
        self.assertEqual(len(direct), 1)
        self.assertIn("same exact ID string", direct[0]["text"])
        self.assertIn("physical identity has not been verified", direct[0]["text"])
        self.assertEqual(sorted(c["type"] for c in direct[0]["citations"]),
                         ["ledger", "telemetry"])
        self.assertEqual(direct[0]["relationship"]["relation"], "same_id_string")


if __name__ == "__main__":
    unittest.main()
