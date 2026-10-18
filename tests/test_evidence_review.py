"""Equipment evidence review tests (stdlib unittest, no cloud calls, no model).

Proves the read-only equipment evidence review flow for the target asset: it
layers deterministic, claim-level cited facts, the approved-reference state, and
the register-backed unresolved questions on the validated asset evidence profile.
It works with no approved knowledge, shows approved knowledge when a fixture
supplies it, refuses unapproved or unreviewed chunks, rejects fabricated,
mismatched, irrelevant, and merely-resolvable citations, limits conflicting and
unknown evidence to only the dependent claims, and writes nothing.
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import agent_context as context  # noqa: E402
from app import agent_contract as contract  # noqa: E402
from app import asset_evidence as evidence  # noqa: E402
from app import equipment as equipment_directory  # noqa: E402
from app import evidence_review as review_module  # noqa: E402

TARGET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
REGISTER = ROOT / "docs/evidence_register.json"

APPROVED_CHUNK = {
    "chunk_id": "kn-1", "title": "Approved transformer reference",
    "status": "approved", "language": "en", "translation_status": "reviewed",
    "equipment_type_code": "0408001", "source_id": "SRC-KN-1",
    "source_uri": "gs://example/kn-1.pdf", "source_locator": "p.1",
    "source_version": "v1", "source_sha256": "a" * 64,
}
UNAPPROVED_CHUNK = dict(APPROVED_CHUNK, chunk_id="kn-2", status="draft",
                        source_sha256="b" * 64)
UNREVIEWED_CHUNK = dict(APPROVED_CHUNK, chunk_id="kn-3",
                        translation_status="draft", source_sha256="c" * 64)
OUT_OF_SCOPE_CHUNK = dict(APPROVED_CHUNK, chunk_id="kn-x",
                          equipment_type_code="9999", source_sha256="d" * 64)


def setUpModule():
    import app.main as main_module
    main_module.BQ_TABLE = ""
    main_module.KNOWLEDGE_TABLE = ""
    main_module.REVIEW_TABLE = ""
    main_module.ENABLE_GEMINI = False
    main_module._cache.update(at=0.0, rows=None)


def target_bundle(chunks=None, rows=None):
    return context.build_context_bundle(TARGET, chunks=chunks, rows=rows)


def target_review(chunks=None, rows=None):
    bundle = target_bundle(chunks=chunks, rows=rows)
    profile = evidence.build_asset_evidence(TARGET, bundle=bundle)
    return review_module.build_evidence_review(
        TARGET, bundle=bundle, profile=profile)


class TargetReviewTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = target_bundle()
        cls.profile = evidence.build_asset_evidence(TARGET, bundle=cls.bundle)
        cls.review = review_module.build_evidence_review(
            TARGET, bundle=cls.bundle, profile=cls.profile)

    def test_review_is_valid_and_bound_to_bundle_and_profile(self):
        self.assertTrue(review_module.validate_evidence_review(
            self.review, bundle=self.bundle, profile=self.profile))
        self.assertEqual(self.review["asset_id"], TARGET)
        self.assertEqual(self.review["bundle_digest_sha256"],
                         self.bundle["bundle_digest_sha256"])
        self.assertEqual(self.review["profile_digest_sha256"],
                         contract.content_digest(self.profile))
        self.assertTrue(self.review["read_only"])
        self.assertFalse(self.review["model_call"])
        # Deterministic: the same inputs reproduce the same review.
        self.assertEqual(target_review(), self.review)

    def test_supported_facts_have_claim_level_citations(self):
        claims = self.review["cited_claims"]
        self.assertGreater(len(claims), 0)
        index = self.bundle["provenance_index"]["evidence"]
        for claim in claims:
            self.assertTrue(claim["citations"])
            self.assertEqual(claim["support"]["relevance"], "supported")
            for citation in claim["citations"]:
                self.assertEqual(index[citation["evidence_id"]]["provenance_status"],
                                 "complete")
            if claim["kind"] == "computation":
                self.assertTrue(claim["support"]["citations_within_inputs"])
                self.assertIn("citations_within_inputs", claim["support"]["checks"])
        self.assertEqual(self.review["supported_evidence"]["claim_count"], len(claims))

    def test_unknown_units_time_criteria_limit_only_dependent_conclusions(self):
        limitations = {item["dimension"]: item for item in self.review["limitations"]}
        for dimension in ("units", "time_semantics", "criteria_applicability",
                          "physical_asset_identity", "outcome_linkage"):
            self.assertIn(dimension, limitations)
            self.assertEqual(limitations[dimension]["status"], "unknown")
            self.assertTrue(limitations[dimension]["open_register_ids"])
            self.assertTrue(limitations[dimension]["blocking_question_ids"])
            self.assertIn("unaffected", limitations[dimension]["statement"])
        # Unrelated supported facts and calculations are not suppressed.
        self.assertGreater(len(self.review["cited_claims"]), 0)
        self.assertEqual(self.review["supported_evidence"]["units_status"],
                         "unconfirmed")
        self.assertEqual(self.review["supported_evidence"]["timestamp_timezone_status"],
                         "unconfirmed")

    def test_no_approved_relevant_knowledge_state(self):
        references = self.review["approved_references"]
        self.assertEqual(references["state"],
                         "no_approved_relevant_knowledge_available")
        self.assertEqual(references["items"], [])
        self.assertEqual(references["approved_relevant_count"], 0)
        self.assertEqual(references["considered_chunk_count"], 0)
        self.assertIn("No approved relevant knowledge", references["statement"])
        self.assertEqual(references["equipment_type_code"], "0408001")
        # The review is still useful without any approved reference.
        self.assertGreater(len(self.review["cited_claims"]), 0)

    def test_human_checks_are_register_backed_and_non_operational(self):
        checks = self.review["human_checks"]
        self.assertGreater(len(checks), 0)
        questions = {item["question_id"]: item
                     for item in self.bundle["unresolved_questions"]}
        for check in checks:
            self.assertTrue(check["non_operational"])
            self.assertIn(check["verify_action"], contract.VERIFICATION_ACTIONS)
            self.assertEqual(check["question"],
                             questions[check["question_id"]]["question"])
            self.assertTrue(check["citations"])

    def test_unresolved_questions_are_verbatim_register_backed(self):
        self.assertGreater(len(self.review["unresolved_questions"]), 0)
        for question in self.review["unresolved_questions"]:
            self.assertIn(question["status"], ("evidence_pending", "unverified"))
            self.assertTrue(question["blocks"])
            self.assertIn(question["verification_action"],
                          contract.VERIFICATION_ACTIONS)

    def test_route_carries_the_review(self):
        import app.main as main_module
        client = main_module.app.test_client()
        body = client.get(f"/api/lineage/equipment/{TARGET}").get_json()
        self.assertIn("evidence_review", body)
        self.assertEqual(body["evidence_review"], self.review)
        self.assertEqual(
            client.get("/api/lineage/equipment/not-a-known-asset").status_code, 404)


class KnowledgeStateTestCase(unittest.TestCase):
    def test_approved_relevant_knowledge_is_surfaced(self):
        review = target_review(chunks=[APPROVED_CHUNK])
        references = review["approved_references"]
        self.assertEqual(references["state"], "approved_relevant_knowledge_available")
        self.assertEqual([item["chunk_id"] for item in references["items"]], ["kn-1"])
        self.assertEqual(references["approved_relevant_count"], 1)
        self.assertIn("approved", references["statement"])
        # The workflow stays useful and the facts are unchanged.
        self.assertGreater(len(review["cited_claims"]), 0)

    def test_unapproved_and_unreviewed_chunks_are_never_promoted(self):
        review = target_review(chunks=[UNAPPROVED_CHUNK, UNREVIEWED_CHUNK])
        references = review["approved_references"]
        self.assertEqual(references["state"],
                         "no_approved_relevant_knowledge_available")
        self.assertEqual(references["items"], [])
        self.assertGreaterEqual(references["considered_chunk_count"], 2)

    def test_out_of_scope_chunk_is_excluded(self):
        review = target_review(chunks=[OUT_OF_SCOPE_CHUNK])
        self.assertEqual(review["approved_references"]["items"], [])
        self.assertEqual(review["approved_references"]["approved_relevant_count"], 0)


class CitationAuditTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = target_bundle()
        cls.profile = evidence.build_asset_evidence(TARGET, bundle=cls.bundle)
        cls.review = review_module.build_evidence_review(
            TARGET, bundle=cls.bundle, profile=cls.profile)

    def _revalidate(self, review):
        return review_module.validate_evidence_review(
            review, bundle=self.bundle, profile=self.profile)

    def _mutation(self):
        return json.loads(json.dumps(self.review))

    def test_fabricated_citation_rejected(self):
        bad = self._mutation()
        claim = next(item for item in bad["cited_claims"]
                     if item["kind"] == "computation")
        claim["citations"][0]["evidence_id"] = "ev_does_not_exist"
        with self.assertRaises(contract.AgentContractError) as caught:
            self._revalidate(bad)
        self.assertEqual(caught.exception.code, "unresolved_citation")

    def test_merely_resolvable_citation_rejected(self):
        # A citation that resolves with complete provenance but is not part of the
        # calculation must be rejected as unsupported, not merely accepted.
        bad = self._mutation()
        counts = [item for item in bad["cited_claims"]
                  if item["kind"] == "computation"
                  and item["computation"]["formula"] == "count"]
        target = next(item for item in counts
                      if item["computation"]["scope"]["measurement_type_code"]
                      != None)  # noqa: E711 - explicit: per-code count claim
        donor = next(item for item in counts
                     if item["computation"]["scope"]["measurement_type_code"]
                     not in (None, target["computation"]["scope"]["measurement_type_code"]))
        foreign = dict(donor["citations"][0])
        target["citations"] = target["citations"] + [foreign]
        with self.assertRaises(contract.AgentContractError) as caught:
            self._revalidate(bad)
        self.assertEqual(caught.exception.code, "unsupported_claim")

    def test_mismatched_locator_rejected(self):
        bad = self._mutation()
        bad["cited_claims"][0]["citations"][0]["locator"] = {"kind": "telemetry"}
        with self.assertRaises(contract.AgentContractError):
            self._revalidate(bad)

    def test_tampered_support_audit_rejected(self):
        bad = self._mutation()
        bad["cited_claims"][0]["support"]["relevance"] = "unsupported"
        with self.assertRaises(contract.AgentContractError) as caught:
            self._revalidate(bad)
        self.assertEqual(caught.exception.code, "unsupported_claim")

    def test_promoted_chunk_cannot_survive_re_derivation(self):
        bad = self._mutation()
        bad["approved_references"]["items"] = [dict(APPROVED_CHUNK)]
        bad["approved_references"]["approved_relevant_count"] = 1
        bad["approved_references"]["state"] = "approved_relevant_knowledge_available"
        with self.assertRaises(contract.AgentContractError) as caught:
            self._revalidate(bad)
        self.assertEqual(caught.exception.code, "unsupported_context")

    def test_unsupported_conclusion_phrase_rejected(self):
        bad = self._mutation()
        bad["supported_evidence"]["statement"] = "This yields a failure probability."
        with self.assertRaises(contract.AgentContractError) as caught:
            self._revalidate(bad)
        self.assertEqual(caught.exception.code, "prohibited_text")

    def test_bundle_digest_mismatch_rejected(self):
        bad = self._mutation()
        bad["bundle_digest_sha256"] = "0" * 64
        with self.assertRaises(contract.AgentContractError) as caught:
            self._revalidate(bad)
        self.assertEqual(caught.exception.code, "bundle_digest_mismatch")

    def test_read_only_and_model_flags_enforced(self):
        for field, value, code in (("read_only", False, "bad_value"),
                                   ("model_call", True, "bad_value")):
            bad = self._mutation()
            bad[field] = value
            with self.assertRaises(contract.AgentContractError) as caught:
                self._revalidate(bad)
            self.assertEqual(caught.exception.code, code)


class ConflictTestCase(unittest.TestCase):
    def test_conflicting_cell_limits_only_that_code(self):
        import app.main as main_module
        rows = [dict(row) for row in main_module._target_rows()]
        target_rows = [row for row in rows
                       if equipment_directory.normalize_id(row.get("psr_id")) == TARGET]
        self.assertTrue(target_rows)
        base = target_rows[0]
        duplicate = dict(base)
        duplicate["source_record_index"] = 10 ** 6
        duplicate["evidence_id"] = base["evidence_id"] + "-dup"
        duplicate["measurement_value"] = (base.get("measurement_value") or 0) + 1
        rows = rows + [duplicate]
        conflict_code = base["measurement_type_code"]

        bundle = context.build_context_bundle(TARGET, rows=rows)
        conflict_gaps = [gap for gap in bundle["known_gaps"]
                         if gap["kind"] == "conflicting_values"]
        self.assertTrue(conflict_gaps)
        conflict_ids = set(conflict_gaps[0]["evidence_ids"])
        self.assertIn(base["evidence_id"], conflict_ids)

        review = review_module.build_evidence_review(TARGET, bundle=bundle)
        self.assertTrue(review_module.validate_evidence_review(review, bundle=bundle))

        code_claims = [item for item in review["cited_claims"]
                       if item["kind"] == "computation"
                       and item["computation"]["scope"]["measurement_type_code"]
                       == conflict_code]
        self.assertTrue(code_claims)
        for claim in code_claims:
            excluded = set(claim["computation"]["scope"]["excluded_evidence_ids"])
            self.assertTrue(conflict_ids.issubset(excluded)
                            or not (conflict_ids & set(claim["computation"]["inputs"])))

        other_claims = [item for item in review["cited_claims"]
                        if item["kind"] == "computation"
                        and item["computation"]["scope"]["measurement_type_code"]
                        not in (conflict_code, None)]
        self.assertTrue(other_claims)
        for claim in other_claims:
            self.assertEqual(claim["computation"]["scope"]["excluded_evidence_ids"], [])
            self.assertEqual(claim["support"]["relevance"], "supported")


class ReadOnlyTestCase(unittest.TestCase):
    def test_nothing_is_written_and_no_cloud_or_model_module_is_imported(self):
        output = ROOT / "data/output"
        before = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in output.rglob("*") if path.is_file()}
        register_before = hashlib.sha256(REGISTER.read_bytes()).hexdigest()
        target_review()
        after = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in output.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(hashlib.sha256(REGISTER.read_bytes()).hexdigest(),
                         register_before)
        self.assertNotIn("google.genai", sys.modules)
        self.assertNotIn("google.cloud.bigquery", sys.modules)

    def test_registry_statuses_are_not_changed_by_the_review(self):
        register = json.loads(REGISTER.read_text(encoding="utf-8"))
        statuses = {item["id"]: item["status"] for item in register["items"]}
        target_review()
        register_after = json.loads(REGISTER.read_text(encoding="utf-8"))
        self.assertEqual(statuses,
                         {item["id"]: item["status"] for item in register_after["items"]})
        self.assertEqual(sum(1 for item in register_after["items"]
                             if item["status"] == "verified"), 0)


if __name__ == "__main__":
    unittest.main()
