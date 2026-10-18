"""Strict agent-draft contract tests (stdlib unittest, no cloud calls).

Proves the contract rejects unsupported diagnosis, failure probability,
maintenance instructions, uncited claims, fabricated sources, invented gaps, and
unapproved knowledge, and that on any violation it fails closed.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import agent_context as context  # noqa: E402
from app import agent_contract as contract  # noqa: E402
from app import agent_draft as draft_builder  # noqa: E402

TARGET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"


def setUpModule():
    import app.main as main_module
    main_module.BQ_TABLE = ""
    main_module.KNOWLEDGE_TABLE = ""
    main_module.REVIEW_TABLE = ""
    main_module.ENABLE_GEMINI = False
    main_module._cache.update(at=0.0, rows=None)


class ContractRejectionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = context.build_context_bundle(TARGET)
        cls.draft = draft_builder.build_deterministic_draft(cls.bundle)
        cls.computation = next(c for c in cls.draft["claims"]
                               if c["kind"] == "computation")
        cls.identity = next(c for c in cls.draft["claims"] if c["kind"] == "direct")

    def mutate(self, **changes):
        draft = copy.deepcopy(self.draft)
        draft.update(changes)
        return draft

    def assertRejected(self, draft, code):
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_draft(draft, self.bundle)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_valid_draft_passes(self):
        self.assertTrue(contract.validate_draft(self.draft, self.bundle))

    def test_unknown_top_level_key_rejected(self):
        self.assertRejected(self.mutate(surprise=1), "unknown_key")

    def test_failure_probability_field_rejected(self):
        self.assertRejected(self.mutate(failure_probability=0.42), "unknown_key")

    def test_diagnosis_in_summary_rejected(self):
        self.assertRejected(self.mutate(summary="Diagnosis: the winding is degraded."),
                            "prohibited_text")

    def test_maintenance_instruction_rejected(self):
        draft = copy.deepcopy(self.draft)
        draft["claims"][0]["text"] = "Replace the transformer before the next cycle."
        self.assertRejected(draft, "prohibited_text")

    def test_uncited_claim_rejected(self):
        draft = copy.deepcopy(self.draft)
        draft["claims"][0]["citations"] = []
        self.assertRejected(draft, "unresolved_citation")

    def test_synthetic_citation_rejected(self):
        draft = copy.deepcopy(self.draft)
        draft["claims"][0]["citations"][0]["evidence_id"] = "ev_deadbeef"
        self.assertRejected(draft, "unresolved_citation")

    def test_computation_mismatch_rejected(self):
        draft = copy.deepcopy(self.draft)
        target = next(c for c in draft["claims"] if c["kind"] == "computation")
        target["computation"]["result"] = target["computation"]["result"] + 1
        self.assertRejected(draft, "computation_mismatch")

    def test_unsupported_formula_rejected(self):
        draft = copy.deepcopy(self.draft)
        target = next(c for c in draft["claims"] if c["kind"] == "computation")
        target["computation"]["formula"] = "forecast_failure"
        self.assertRejected(draft, "unsupported_formula")

    def test_unknown_formula_version_rejected(self):
        draft = copy.deepcopy(self.draft)
        target = next(c for c in draft["claims"] if c["kind"] == "computation")
        target["computation"]["formula_version"] = "formula.v0"
        self.assertRejected(draft, "bad_value")

    def test_computation_input_count_mismatch_rejected(self):
        draft = copy.deepcopy(self.draft)
        target = next(c for c in draft["claims"] if c["kind"] == "computation")
        target["computation"]["input_count"] = target["computation"]["input_count"] + 1
        self.assertRejected(draft, "computation_mismatch")

    def test_computation_digest_mismatch_rejected(self):
        draft = copy.deepcopy(self.draft)
        target = next(c for c in draft["claims"] if c["kind"] == "computation")
        target["computation"]["inputs_digest_sha256"] = "0" * 64
        self.assertRejected(draft, "computation_mismatch")

    def test_computation_outside_declared_scope_rejected(self):
        draft = copy.deepcopy(self.draft)
        target = next(c for c in draft["claims"] if c["kind"] == "computation")
        target["computation"]["scope"]["measurement_type_code"] = "PhV_phsB"
        self.assertRejected(draft, "computation_scope_mismatch")

    def test_min_without_extremal_witness_rejected(self):
        draft = copy.deepcopy(self.draft)
        index = self.bundle["provenance_index"]["evidence"]
        target = next(c for c in draft["claims"]
                      if c["computation"] and c["computation"]["formula"] == "min")
        result = target["computation"]["result"]
        extremal = {eid for eid in target["computation"]["inputs"]
                    if round(float(index[eid]["value"]), 6) == result}
        target["citations"] = [c for c in target["citations"]
                               if c["evidence_id"] not in extremal]
        self.assertRejected(draft, "missing_witness")

    def test_relationship_must_cite_both_endpoints(self):
        draft = copy.deepcopy(self.draft)
        claim = next(c for c in draft["claims"] if c["kind"] == "direct")
        telemetry_id = claim["relationship"]["telemetry_evidence_id"]
        claim["citations"] = [c for c in claim["citations"]
                              if c["evidence_id"] != telemetry_id]
        self.assertRejected(draft, "missing_witness")

    def test_relationship_caveat_required(self):
        draft = copy.deepcopy(self.draft)
        claim = next(c for c in draft["claims"] if c["kind"] == "direct")
        claim["relationship"]["caveat"] = "the ID strings match"
        self.assertRejected(draft, "missing_caveat")

    def test_relationship_unknown_relation_rejected(self):
        draft = copy.deepcopy(self.draft)
        claim = next(c for c in draft["claims"] if c["kind"] == "direct")
        claim["relationship"]["relation"] = "proven_same_device"
        self.assertRejected(draft, "bad_value")

    def test_generated_requires_claims(self):
        self.assertRejected(self.mutate(claims=[]), "unsupported_claim")

    def test_insufficient_status_forbids_claims(self):
        self.assertRejected(self.mutate(status="insufficient_evidence"),
                            "unsupported_claim")

    def test_invented_gap_kind_rejected(self):
        draft = copy.deepcopy(self.draft)
        draft["known_gaps"][0]["kind"] = "made_up_kind"
        self.assertRejected(draft, "unknown_gap_kind")

    def test_draft_must_match_bundle_digest(self):
        self.assertRejected(self.mutate(bundle_digest_sha256="0" * 64),
                            "bundle_digest_mismatch")

    def test_suggestion_not_register_backed_rejected(self):
        draft = copy.deepcopy(self.draft)
        draft["suggestions"][0]["question_id"] = "EV-FAKE-QUESTION"
        self.assertRejected(draft, "unresolved_register_question")

    def test_suggestion_altered_question_rejected(self):
        draft = copy.deepcopy(self.draft)
        draft["suggestions"][0]["question"] = "Is the transformer overheating?"
        self.assertRejected(draft, "unresolved_register_question")

    def test_suggestion_operational_action_rejected(self):
        draft = copy.deepcopy(self.draft)
        draft["suggestions"][0]["verify_action"] = "inspect_equipment"
        self.assertRejected(draft, "unresolved_register_question")

    def test_suggestion_uncited_rejected(self):
        draft = copy.deepcopy(self.draft)
        draft["suggestions"][0]["citations"] = []
        self.assertRejected(draft, "unresolved_citation")

    def test_suggestion_group_metadata_must_match_question(self):
        draft = copy.deepcopy(self.draft)
        draft["suggestions"][0]["group_id"] = "grp_wrong"
        self.assertRejected(draft, "unresolved_register_question")

    def test_suggestion_on_non_evidence_specific_question_rejected(self):
        draft = copy.deepcopy(self.draft)
        suggestion = draft["suggestions"][0]
        suggestion["question_id"] = "EV-RULES-APPROVED"
        suggestion["register_id"] = "EV-RULES-APPROVED"
        self.assertRejected(draft, "unsupported_suggestion")

    def test_suggestion_on_unsupported_reason_rejected(self):
        # Readings witness that a code is present, not that its unit/scaling is
        # undocumented, so a suggestion resting on that reason must fail closed.
        draft = copy.deepcopy(self.draft)
        suggestion = draft["suggestions"][0]
        suggestion["question_id"] = "EV-CODE-A_phsA"
        suggestion["register_id"] = "EV-CODE-A_phsA"
        self.assertRejected(draft, "unsupported_suggestion")


class BundleStrictnessTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = context.build_context_bundle(TARGET)

    def rebound(self, mutate):
        bundle = copy.deepcopy(self.bundle)
        mutate(bundle)
        bundle["bundle_digest_sha256"] = contract.bundle_digest(bundle)
        return bundle

    def test_evidence_specific_must_be_boolean(self):
        def mutate(bundle):
            bundle["unresolved_questions"][0]["evidence_specific"] = "yes"
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "bad_type")

    def test_group_metadata_must_be_string_or_null(self):
        def mutate(bundle):
            bundle["unresolved_questions"][0]["group_id"] = 5
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "bad_type")

    def test_reason_supported_must_be_boolean(self):
        def mutate(bundle):
            bundle["unresolved_questions"][0]["reason_supported"] = "yes"
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "bad_type")

    def test_absence_gap_requires_search_scope(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unknown_units_scaling")
            gap["search_scope"] = None
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")

    def test_absence_gap_digest_must_match_record_set(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unknown_units_scaling")
            gap["search_scope"]["inventory_digest_sha256"] = "0" * 64
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "gap_scope_mismatch")

    def test_unavailable_scope_must_not_report_count(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unknown_units_scaling")
            gap["search_scope"]["status"] = "unavailable"
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")

    def test_unavailable_scope_without_count_is_allowed(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unknown_units_scaling")
            gap["search_scope"] = {
                "scope_id": "telemetry_record_set",
                "description": "the complete set of telemetry records is unavailable",
                "status": "unavailable",
                "record_count": None,
                "inventory_digest_sha256": None,
                "source_files": [],
            }
        self.assertTrue(contract.validate_context(self.rebound(mutate)))

    def test_documentation_gap_requires_documentation_scope(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unknown_units_scaling")
            gap["documentation_scope"] = None
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")

    def test_documentation_scope_cannot_claim_an_enumerated_inventory(self):
        # No authoritative documentation inventory is retained, so documentation
        # absence cannot be claimed: an enumerated documentation scope fails closed.
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unknown_timezone")
            gap["documentation_scope"]["status"] = "enumerated"
            gap["documentation_scope"]["inventory_source"] = "field_dictionary.json"
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")

    def test_unavailable_documentation_scope_cannot_name_a_source(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unknown_units_scaling")
            gap["documentation_scope"]["inventory_source"] = "field_dictionary.json"
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")

    def test_documentation_scope_only_on_documentation_gaps(self):
        # No documentation gap remains in the reviewed real bundle, so build a
        # bundle whose only gap is a non-documentation absence and try to attach
        # a documentation scope to it.
        import app.main as main_module
        rows = [{
            "source_file_id": "distribution_transformer_snapshot.json",
            "source_record_index": 0, "psr_id": "NO-LEDGER",
            "measurement_point_id": "P1", "equipment_type_code": "0408001",
            "measurement_type_code": "A_phsA", "measurement_name": "A_phsA",
            "measurement_value": 1.0, "measurement_unit": None,
            "observed_at": "2026-01-01T00:00:00", "dataset_type": "snapshot",
            "data_origin": "observed",
            "source_uri": "distribution_transformer_snapshot.json",
            "quality_flags": "unit not confirmed",
        }]
        rows[0]["evidence_id"] = main_module._evidence_id(rows[0])
        bundle = context.build_context_bundle(
            "NO-LEDGER", rows=rows, catalog={"records": [], "source": {}},
            manifest={"distribution_transformer_snapshot.json": "d" * 64}, chunks=[])
        gap = next(g for g in bundle["known_gaps"]
                   if g["kind"] == "unmatched_ledger_id")
        gap["documentation_scope"] = {
            "subject": "unit_scaling", "status": "unavailable",
            "description": "x", "inventory_source": None}
        bundle["bundle_digest_sha256"] = contract.bundle_digest(bundle)
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(bundle)
        self.assertEqual(caught.exception.code, "bad_value")

    def test_unknown_scope_id_rejected(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unknown_units_scaling")
            gap["search_scope"]["scope_id"] = "made_up_inventory"
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "bad_value")

    def test_ledger_review_status_must_be_string_or_null(self):
        def mutate(bundle):
            for item in bundle["evidence_items"]:
                if item["evidence_type"] == "ledger":
                    item["review_status"] = 7
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(mutate))
        self.assertEqual(caught.exception.code, "bad_type")


class ResolveCitationsTestCase(unittest.TestCase):
    def test_unapproved_knowledge_citation_rejected(self):
        locator = {"kind": "knowledge", "source_id": None,
                   "source_locator": None, "source_version": None}
        bundle = {"provenance_index": {
            "evidence": {},
            "knowledge": {"cid": {"review_status": "draft",
                                  "translation_status": "reviewed",
                                  "source_id": None, "source_uri": None,
                                  "source_sha256": "a" * 64,
                                  "source_version": None, "locator": locator}},
        }}
        citation = {"type": "knowledge", "evidence_id": None, "chunk_id": "cid",
                    "locator": locator, "source_sha256": "a" * 64,
                    "provenance_status": "complete"}
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.resolve_citations([citation], bundle)
        self.assertEqual(caught.exception.code, "unapproved_knowledge")

    def test_locator_drift_rejected(self):
        locator = {"kind": "ledger", "workbook": "equipment_ledger.xlsx",
                   "sheet": "hlt_device_ledger_pool", "key": "psr_id=X"}
        bundle = {"provenance_index": {
            "evidence": {f"ev_{'b' * 20}": {
                "evidence_type": "ledger", "source_file_id": "equipment_ledger.xlsx",
                "source_sha256": "b" * 64, "provenance_status": "complete",
                "locator": locator, "value": None, "measurement_type_code": None,
                "observed_at": "", "dataset_type": "ledger"}},
            "knowledge": {},
        }}
        citation = {"type": "ledger", "evidence_id": f"ev_{'b' * 20}",
                    "chunk_id": None, "locator": dict(locator, key="psr_id=Y"),
                    "source_sha256": "b" * 64, "provenance_status": "complete"}
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.resolve_citations([citation], bundle)
        self.assertEqual(caught.exception.code, "locator_mismatch")


class InsufficientEvidenceTestCase(unittest.TestCase):
    def test_incomplete_bundle_yields_fixed_result(self):
        import app.main as main_module
        row = {
            "source_file_id": "not_in_manifest.json", "source_record_index": 0,
            "psr_id": "A-9", "measurement_point_id": "P1",
            "equipment_type_code": "0408001", "measurement_type_code": "A_phsA",
            "measurement_name": "A_phsA", "measurement_value": 4.0,
            "measurement_unit": None, "observed_at": "2026-01-01T00:00:00",
            "dataset_type": "snapshot", "data_origin": "observed",
            "source_uri": "not_in_manifest.json", "quality_flags": "unit not confirmed",
        }
        row["evidence_id"] = main_module._evidence_id(row)
        bundle = context.build_context_bundle("A-9", rows=[row])
        generated = draft_builder.build_deterministic_draft(bundle)
        self.assertEqual(generated["status"], contract.STATUS_INSUFFICIENT_EVIDENCE)
        result = draft_builder.build_insufficient_evidence_result(bundle)
        self.assertTrue(contract.validate_draft(result, bundle))
        self.assertEqual(result["claims"], [])
        kinds = {gap["kind"] for gap in result["known_gaps"]}
        self.assertIn("missing_source_hash", kinds)

    def test_rejected_result_carries_no_claims(self):
        result = draft_builder.rejected_result("d" * 64, TARGET,
                                               "computation_mismatch", "bad")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["claims"], [])
        self.assertEqual(result["suggestions"], [])


class AbsenceScopeFailClosedTestCase(unittest.TestCase):
    """Rule 2: unsupported absence claims cannot be enumerated or become claims."""

    @staticmethod
    def _row(asset, code, value, observed, *, file="distribution_transformer_snapshot.json",
             record=0, dataset="snapshot"):
        import app.main as main_module
        row = {
            "source_file_id": file, "source_record_index": record, "psr_id": asset,
            "measurement_point_id": "P1", "equipment_type_code": "0408001",
            "measurement_type_code": code, "measurement_name": code,
            "measurement_value": value, "measurement_unit": None,
            "observed_at": observed, "dataset_type": dataset,
            "data_origin": "observed", "source_uri": file,
            "quality_flags": "unit not confirmed",
        }
        row["evidence_id"] = main_module._evidence_id(row)
        return row

    @classmethod
    def setUpClass(cls):
        cls.missing = context.build_context_bundle(
            "A-2", rows=[cls._row("A-2", "A_phsA", 9.0, "2026-01-01T00:00:00",
                                  file="not_in_manifest.json")])
        cls.unmatched = context.build_context_bundle(
            "UNMATCHED-1",
            rows=[cls._row("UNMATCHED-1", "A_phsA", 12.5, "2026-01-01T00:00:00")])

    def rebound(self, bundle, mutate):
        bundle = copy.deepcopy(bundle)
        mutate(bundle)
        bundle["bundle_digest_sha256"] = contract.bundle_digest(bundle)
        return bundle

    def test_missing_hash_scope_digest_must_match_manifest(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "missing_source_hash")
            gap["search_scope"]["inventory_digest_sha256"] = "0" * 64
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(self.missing, mutate))
        self.assertEqual(caught.exception.code, "gap_scope_mismatch")

    def test_missing_hash_scope_must_name_the_inventory(self):
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "missing_source_hash")
            gap["search_scope"] = None
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(self.missing, mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")

    def test_missing_hash_for_file_in_manifest_rejected(self):
        # The file is, in fact, present in the manifest, so the absence is bogus.
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "missing_source_hash")
            gap["detail"]["source_file_id"] = "distribution_transformer_snapshot.json"
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(self.missing, mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")

    def test_missing_hash_evidence_must_be_incomplete(self):
        bundle = context.build_context_bundle("A-2", rows=[
            self._row("A-2", "A_phsA", 1.0, "2026-01-01T00:00:00"),
            self._row("A-2", "A_phsA", 2.0, "2026-01-02T00:00:00", record=1),
            self._row("A-2", "A_phsA", 3.0, "2026-01-03T00:00:00",
                      file="not_in_manifest.json", record=2),
        ])
        complete = next(i for i in bundle["evidence_items"]
                        if i["provenance_status"] == "complete")

        def mutate(inner):
            gap = next(g for g in inner["known_gaps"]
                       if g["kind"] == "missing_source_hash")
            gap["evidence_ids"] = [complete["evidence_id"]]
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(bundle, mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")

    def test_unmatched_ledger_scope_cannot_be_enumerated(self):
        # The ledger catalog is not retained, so claiming to have enumerated it
        # fails closed rather than inventing a scope.
        def mutate(bundle):
            gap = next(g for g in bundle["known_gaps"]
                       if g["kind"] == "unmatched_ledger_id")
            gap["search_scope"] = {
                "scope_id": "ledger_catalog",
                "description": "the complete equipment ledger catalog",
                "status": "enumerated", "record_count": 100,
                "inventory_digest_sha256": "a" * 64, "source_files": []}
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(self.rebound(self.unmatched, mutate))
        self.assertEqual(caught.exception.code, "unsupported_gap")


class CountCitationFailClosedTestCase(unittest.TestCase):
    """Rule 3: count aggregates cannot cite outside their complete input set."""

    @staticmethod
    def _row(asset, code, value, observed, *, file="distribution_transformer_snapshot.json",
             record=0, dataset="snapshot"):
        import app.main as main_module
        row = {
            "source_file_id": file, "source_record_index": record, "psr_id": asset,
            "measurement_point_id": "P1", "equipment_type_code": "0408001",
            "measurement_type_code": code, "measurement_name": code,
            "measurement_value": value, "measurement_unit": None,
            "observed_at": observed, "dataset_type": dataset,
            "data_origin": "observed", "source_uri": file,
            "quality_flags": "unit not confirmed",
        }
        row["evidence_id"] = main_module._evidence_id(row)
        return row

    def _count_claim(self, bundle, code):
        draft = draft_builder.build_deterministic_draft(bundle)
        for claim in draft["claims"]:
            computation = claim["computation"]
            if computation and computation["formula"] == "count" \
                    and computation["scope"]["measurement_type_code"] == code:
                return draft, claim
        self.fail(f"no count claim for {code}")

    def test_count_over_incomplete_input_rejected(self):
        bundle = context.build_context_bundle("A-2", rows=[
            self._row("A-2", "A_phsA", 1.0, "2026-01-01T00:00:00"),
            self._row("A-2", "A_phsA", 2.0, "2026-01-02T00:00:00", record=1),
            self._row("A-2", "A_phsA", 3.0, "2026-01-03T00:00:00",
                      file="not_in_manifest.json", record=2),
        ])
        incomplete = next(i for i in bundle["evidence_items"]
                          if i["provenance_status"] == "incomplete")
        draft, claim = self._count_claim(bundle, "A_phsA")
        claim["computation"]["inputs"].append(incomplete["evidence_id"])
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_draft(draft, bundle)
        self.assertEqual(caught.exception.code, "incomplete_provenance")

    def test_count_over_conflicting_input_rejected(self):
        bundle = context.build_context_bundle("A-6", rows=[
            self._row("A-6", "A_phsA", 1.0, "2026-01-01T00:00:00", dataset="history"),
            self._row("A-6", "A_phsA", 2.0, "2026-01-01T00:00:00", dataset="history",
                      record=1),
            self._row("A-6", "A_phsA", 5.0, "2026-01-02T00:00:00", dataset="history",
                      record=2),
            self._row("A-6", "PhV_phsA", 100.0, "2026-01-02T00:00:00"),
        ])
        conflict = next(g for g in bundle["known_gaps"]
                        if g["kind"] == "conflicting_values")
        excluded = conflict["evidence_ids"][0]
        draft, claim = self._count_claim(bundle, "A_phsA")
        claim["computation"]["inputs"].append(excluded)
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_draft(draft, bundle)
        self.assertEqual(caught.exception.code, "computation_scope_mismatch")


class EquipmentContextFailClosedTestCase(unittest.TestCase):
    """The equipment-context gate cannot be forged past the recorded review."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = context.build_context_bundle(TARGET)

    def rebound(self, mutate, bundle=None):
        bundle = copy.deepcopy(bundle if bundle is not None else self.bundle)
        mutate(bundle)
        bundle["bundle_digest_sha256"] = contract.bundle_digest(bundle)
        return bundle

    def assertRejected(self, bundle, code):
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(bundle)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_forged_usable_field_rejected(self):
        def mutate(bundle):
            # A field with no recorded value is not usable; forging it usable must
            # fail closed.
            field = next(f for f in bundle["equipment_context"]["fields"]
                         if not f["usable_for_context"])
            field["usable_for_context"] = True
            field["blocked_reason_code"] = None
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_forged_usable_context_rejected(self):
        # The reviewed real bundle is usable; a claimed flag that disagrees with
        # the derived gate (either direction) must fail closed.
        def mutate(bundle):
            block = bundle["equipment_context"]
            block["context_usable"] = not block["context_usable"]
            block["blocked_reason_code"] = (
                None if block["context_usable"] else "no_usable_context_fields")
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_context_blocked_reason_drift_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["blocked_reason_code"] = "unmatched_ledger_id"
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_unknown_context_block_reason_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["blocked_reason_code"] = "made_up_reason"
        self.assertRejected(self.rebound(mutate), "bad_value")

    def test_field_blocked_reason_drift_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["fields"][0]["blocked_reason_code"] = \
                "missing_value"
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_field_review_status_forgery_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["fields"][0]["review_status"] = "draft"
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_block_review_status_forgery_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["review_status"] = "draft"
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_field_locator_drift_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["fields"][0]["locator"] = {
                "kind": "ledger", "workbook": "other.xlsx", "sheet": "s",
                "key": "psr_id=OTHER"}
        self.assertRejected(self.rebound(mutate), "locator_mismatch")

    def test_field_source_hash_drift_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["fields"][0]["source_sha256"] = "0" * 64
        self.assertRejected(self.rebound(mutate), "source_hash_mismatch")

    def test_unknown_context_key_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["surprise"] = 1
        self.assertRejected(self.rebound(mutate), "unknown_key")

    def test_unknown_field_key_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["fields"][0]["surprise"] = 1
        self.assertRejected(self.rebound(mutate), "unknown_key")

    def test_context_must_reference_a_ledger_item(self):
        def mutate(bundle):
            telemetry_id = next(i["evidence_id"] for i in bundle["evidence_items"]
                                if i["evidence_type"] == "telemetry")
            bundle["equipment_context"]["evidence_id"] = telemetry_id
        self.assertRejected(self.rebound(mutate), "bad_value")

    def test_context_evidence_must_resolve(self):
        def mutate(bundle):
            bundle["equipment_context"]["evidence_id"] = "ev_deadbeef"
        self.assertRejected(self.rebound(mutate), "unresolved_citation")

    def test_unmatched_context_must_not_carry_evidence(self):
        import app.main as main_module
        rows = [{
            "source_file_id": "distribution_transformer_snapshot.json",
            "source_record_index": 0, "psr_id": "NO-LEDGER",
            "measurement_point_id": "P1", "equipment_type_code": "0408001",
            "measurement_type_code": "A_phsA", "measurement_name": "A_phsA",
            "measurement_value": 1.0, "measurement_unit": None,
            "observed_at": "2026-01-01T00:00:00", "dataset_type": "snapshot",
            "data_origin": "observed",
            "source_uri": "distribution_transformer_snapshot.json",
            "quality_flags": "unit not confirmed",
        }]
        rows[0]["evidence_id"] = main_module._evidence_id(rows[0])
        bundle = context.build_context_bundle(
            "NO-LEDGER", rows=rows, catalog={"records": [], "source": {}},
            manifest={"distribution_transformer_snapshot.json": "d" * 64}, chunks=[])
        self.assertFalse(bundle["equipment_context"]["matched"])

        def mutate(inner):
            inner["equipment_context"]["evidence_id"] = "ev_anything"
        self.assertRejected(self.rebound(mutate, bundle), "bad_value")

    def test_context_matched_flag_must_agree_with_ledger_match(self):
        def mutate(bundle):
            block = bundle["equipment_context"]
            block["matched"] = False
            block["evidence_id"] = None
            block["locator"] = None
            block["source_sha256"] = None
            block["fields"] = []
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_forged_status_label_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["status_label"] = "translation unreviewed"
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_unknown_status_label_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["status_label"] = "totally fine"
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_forged_supported_dimension_rejected(self):
        def mutate(bundle):
            entry = bundle["equipment_context"]["dependent_unknowns"][0]
            entry["status"] = "supported"
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_dimension_cannot_claim_support_without_backing_ids(self):
        def mutate(bundle):
            entry = bundle["equipment_context"]["dependent_unknowns"][0]
            entry["register_ids"] = []
            entry["open_register_ids"] = []
            entry["status"] = "supported"
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_open_ids_must_subset_register_ids(self):
        def mutate(bundle):
            entry = bundle["equipment_context"]["dependent_unknowns"][0]
            entry["open_register_ids"] = entry["open_register_ids"] + ["EV-NOT-REGISTERED"]
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_repeated_register_ids_rejected(self):
        def mutate(bundle):
            entry = bundle["equipment_context"]["dependent_unknowns"][0]
            entry["register_ids"] = entry["register_ids"] * 2
            entry["open_register_ids"] = entry["open_register_ids"] * 2
        self.assertRejected(self.rebound(mutate), "bad_value")

    def test_unknown_dimension_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["dependent_unknowns"][0]["dimension"] = "made_up"
        self.assertRejected(self.rebound(mutate), "bad_value")

    def test_missing_dimension_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["dependent_unknowns"].pop()
        self.assertRejected(self.rebound(mutate), "unsupported_context")

    def test_duplicate_dimension_rejected(self):
        def mutate(bundle):
            unknowns = bundle["equipment_context"]["dependent_unknowns"]
            unknowns[1] = copy.deepcopy(unknowns[0])
        self.assertRejected(self.rebound(mutate), "bad_value")

    def test_unknown_dimension_key_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["dependent_unknowns"][0]["surprise"] = 1
        self.assertRejected(self.rebound(mutate), "unknown_key")

    # --- register binding (verification against the authoritative register) ---

    def test_extra_register_id_rejected(self):
        def mutate(bundle):
            entry = bundle["equipment_context"]["dependent_unknowns"][0]
            entry["register_ids"] = sorted(
                entry["register_ids"] + ["EV-NOT-IN-REGISTER"])
        self.assertRejected(self.rebound(mutate), "register_mismatch")

    def test_missing_register_ids_rejected(self):
        def mutate(bundle):
            entry = bundle["equipment_context"]["dependent_unknowns"][0]
            entry["register_ids"] = []
            entry["open_register_ids"] = []
            entry["status"] = "not_recorded"
        self.assertRejected(self.rebound(mutate), "register_mismatch")

    def test_mis_mapped_topics_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["dependent_unknowns"][0]["topics"] = \
                ["asset_identity"]
        self.assertRejected(self.rebound(mutate), "bad_value")

    def test_forged_register_digest_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["register_binding"][
                "register_digest_sha256"] = "0" * 64
        self.assertRejected(self.rebound(mutate), "register_mismatch")

    def test_forged_register_version_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["register_binding"]["register_version"] = "9.9.9"
        self.assertRejected(self.rebound(mutate), "register_mismatch")

    def test_forged_item_count_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["register_binding"]["item_count"] = 99
        self.assertRejected(self.rebound(mutate), "register_mismatch")

    def test_forged_dimension_count_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["register_binding"]["dimension_count"] = 4
        self.assertRejected(self.rebound(mutate), "bad_value")

    def test_unknown_binding_key_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["register_binding"]["surprise"] = 1
        self.assertRejected(self.rebound(mutate), "unknown_key")

    def test_missing_binding_key_rejected(self):
        def mutate(bundle):
            bundle["equipment_context"]["register_binding"].pop("register_version")
        self.assertRejected(self.rebound(mutate), "missing_key")

    def test_matching_digest_but_stale_dimensions_still_rejected(self):
        # A forger who also rewrites digest/version/count to match a changed
        # register still fails: the dimensions are re-derived from the register,
        # so the digest is not the only check.
        changed = copy.deepcopy(contract.load_authoritative_register())
        for item in changed["items"]:
            if item["id"] == "EV-RULES-APPROVED":
                item["status"] = "verified"

        def mutate(bundle):
            binding = bundle["equipment_context"]["register_binding"]
            binding["register_digest_sha256"] = contract.register_digest(changed)
            binding["register_version"] = contract.register_version(changed)
            binding["item_count"] = contract.register_item_count(changed)
        bundle = self.rebound(mutate)
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(bundle, changed)
        self.assertEqual(caught.exception.code, "register_mismatch")
        self.assertEqual(caught.exception.path,
                         "$.equipment_context.dependent_unknowns")


if __name__ == "__main__":
    unittest.main()
