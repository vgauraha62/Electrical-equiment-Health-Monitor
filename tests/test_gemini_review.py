"""Deterministic tests for the question-grounded Gemini evidence assistant.

No live Gemini or cloud call is made: the model adapter is replaced with a fake
generator that returns fixed strings. The suite covers flag-off behavior, the
empty-approved-knowledge state, approved chunks, irrelevant/fabricated citations,
conflicting evidence, unknown units/time/criteria, malformed output, model
errors, question-driven selection, an unsupported direct answer, and that the flow
writes nothing and creates no review case.
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.main as main_module  # noqa: E402
import app.gemini_review as review  # noqa: E402

ASSET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
NO_ASSET = "00000000-0000-0000-0000-000000000000-00000"


def _tmpdir(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


class GeminiReviewTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module.ENABLE_GEMINI_REVIEW = False
        main_module.ENABLE_AGENT_DRAFT = False
        main_module.ENABLE_ASSET_MAPPING = False
        main_module._cache.update(at=0.0, rows=None)
        cls.review_dir = _tmpdir("gr_review_")
        cls.mapping_dir = _tmpdir("gr_mapping_")
        cls.intake_dir = _tmpdir("gr_intake_")
        cls._env = {key: os.environ.get(key) for key in
                    ("REVIEW_CASES_DIR", "ASSET_MAPPING_DIR", "EVIDENCE_INTAKE_DIR")}
        os.environ["REVIEW_CASES_DIR"] = str(cls.review_dir)
        os.environ["ASSET_MAPPING_DIR"] = str(cls.mapping_dir)
        os.environ["EVIDENCE_INTAKE_DIR"] = str(cls.intake_dir)
        cls.client = main_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # --- helpers -------------------------------------------------------------

    def package(self, chunks_path=None):
        return review.build_review_input(ASSET, approved_chunks_path=chunks_path)

    def cited(self, text, *citations, quotes=None):
        return {"text": text,
                "citations": [{"kind": kind, "id": cid} for kind, cid in citations],
                "quotes": list(quotes or [])}

    def claim_item(self, package, index=0, text=None, citations=None, quotes=None):
        claim = package["observations"][index]
        return {
            "text": text if text is not None else claim["text"],
            "citations": (citations if citations is not None
                          else [{"kind": "claim", "id": claim["claim_id"]}]),
            "quotes": list(quotes or []),
        }

    def empty_item(self):
        return {"text": "", "citations": [], "quotes": []}

    def output(self, package, **overrides):
        """A well-formed response whose direct answer is valid by default."""
        base = {
            "output_version": review.OUTPUT_VERSION,
            "asset_id": package["asset_id"],
            "direct_answer": self.claim_item(package),
            "supporting_points": [], "unknowns": [], "human_checks": [],
            "abstained": False, "abstention_reason": None,
        }
        base.update(overrides)
        return base

    def validate(self, package, payload):
        return review.validate_model_output(json.dumps(payload), package)

    def outcome_events(self, logs):
        """Parse the ``gemini_review_outcome`` JSON events out of captured logs."""
        events = []
        for line in logs.output:
            marker = line.find("{")
            if marker == -1:
                continue
            try:
                payload = json.loads(line[marker:])
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("event") == review.OUTCOME_EVENT:
                events.append(payload)
        return events

    # --- flag and health -----------------------------------------------------

    def test_flag_off_route_is_not_available(self):
        main_module.ENABLE_GEMINI_REVIEW = False
        response = self.client.post(f"/api/assets/{ASSET}/gemini-review")
        self.assertEqual(response.status_code, 404)
        self.assertIn("disabled", response.get_json()["error"])

    def test_health_reports_gemini_review_flag(self):
        body = self.client.get("/api/health").get_json()
        self.assertIn("gemini_review_enabled", body)
        self.assertEqual(body["gemini_review_enabled"], main_module.ENABLE_GEMINI_REVIEW)
        # The legacy flag and endpoint are unchanged.
        self.assertIn("gemini_enabled", body)

    def test_input_package_and_prompt(self):
        package = self.package()
        self.assertEqual(package["asset_id"], ASSET)
        self.assertTrue(package["observations"])
        self.assertEqual(package["approved_references"], [])
        self.assertEqual(package["knowledge_state"]["approved_relevant_count"], 0)
        self.assertEqual(package["units_status"], "unconfirmed")
        self.assertEqual(package["timestamp_timezone_status"], "unconfirmed")
        prompt = review.build_prompt(package)
        self.assertIn("VALIDATED_INPUT_JSON", prompt)
        self.assertIn("approved reference", prompt)

    def test_invalid_asset_returns_none(self):
        self.assertIsNone(review.explain(NO_ASSET, generate=lambda _: "{}"))

    # --- no approved knowledge ----------------------------------------------

    def test_no_approved_knowledge_is_visible_and_reference_section_empty(self):
        package = self.package()
        payload = self.output(package, supporting_points=[self.claim_item(package)])
        result = self.validate(package, payload)
        self.assertEqual(result["status"], "explained")
        # The deterministic claim restatement is accepted from supporting_points.
        self.assertEqual(len(result["sections"]["supporting_points"]), 1)
        self.assertEqual(result["sections"]["unknowns"], [])
        self.assertEqual(result["sections"]["human_checks"], [])
        response = review.explain(ASSET, generate=lambda _: json.dumps(payload))
        self.assertTrue(response["no_approved_knowledge"])
        self.assertTrue(response["no_relevant_reference"])
        self.assertEqual(response["knowledge_state"]["state"],
                         "no_approved_relevant_knowledge_available")
        # A reference statement without a supplied reference is omitted.
        bad = self.output(package, supporting_points=[self.cited(
            "Some approved guidance applies.", ("reference", "chunk_missing"))])
        omitted = self.validate(package, bad)["omitted"]
        self.assertEqual(omitted[0]["reason_code"], "unresolved_citation")

    # --- approved knowledge --------------------------------------------------

    def _chunk_path(self):
        directory = _tmpdir("gr_chunks_")
        path = directory / "approved_chunks.json"
        content = ("Phase current imbalance must be confirmed against the recorded "
                   "units before any interpretation; recorded values are raw until "
                   "units are confirmed.")
        import hashlib
        path.write_text(json.dumps({"chunks": [{
            "chunk_id": "chunk_demo_001", "title": "Phase current note",
            "content": content, "source_id": "doc_001",
            "source_uri": "gs://demo/doc.pdf", "source_locator": "p.4 row 2",
            "source_version": "v1", "status": "approved", "language": "en",
            "translation_status": "reviewed", "equipment_type_code": "0408001",
            "source_sha256": hashlib.sha256(b"demo").hexdigest()}]}), encoding="utf-8")
        return path

    def test_approved_reference_requires_verbatim_quote(self):
        package = self.package(self._chunk_path())
        self.assertEqual(len(package["approved_references"]), 1)
        reference_id = package["approved_references"][0]["chunk_id"]
        good = self.output(package, supporting_points=[self.cited(
            "The reference notes recorded values stay raw until units are confirmed.",
            ("reference", reference_id),
            quotes=["recorded values are raw until units are confirmed."])])
        result = self.validate(package, good)
        self.assertEqual(result["status"], "explained")
        self.assertEqual(len(result["sections"]["supporting_points"]), 1)
        fabricated = self.output(package, supporting_points=[self.cited(
            "The reference concerns the phase current note.",
            ("reference", reference_id),
            quotes=["a sentence that is not in the chunk"])])
        omitted = self.validate(package, fabricated)["omitted"]
        self.assertEqual(omitted[0]["reason_code"], "unsupported_reference")
        noquote = self.output(package, supporting_points=[self.cited(
            "The reference is relevant.", ("reference", reference_id))])
        self.assertEqual(self.validate(package, noquote)["omitted"][0]["reason_code"],
                         "unsupported_reference")
        # A direct answer may only rest on an approved reference with a quote too.
        answer_noquote = self.output(package, direct_answer=self.cited(
            "The approved reference applies.", ("reference", reference_id)))
        result = self.validate(package, answer_noquote)
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason_code"], "direct_answer_unsupported")
        self.assertEqual(result["omitted"][0]["reason_code"], "unsupported_reference")

    def test_draft_knowledge_is_not_included(self):
        directory = _tmpdir("gr_draft_")
        path = directory / "chunks.json"
        path.write_text(json.dumps({"chunks": [{
            "chunk_id": "chunk_draft", "title": "Draft", "content": "draft text",
            "status": "draft", "language": "en",
            "translation_status": "reviewed", "equipment_type_code": "0408001",
            "source_sha256": "a" * 64}]}), encoding="utf-8")
        package = self.package(path)
        self.assertEqual(package["approved_references"], [])
        self.assertEqual(package["knowledge_state"]["approved_relevant_count"], 0)

    # --- citation validation -------------------------------------------------

    def test_fabricated_and_unknown_citations_are_rejected(self):
        package = self.package()
        payload = self.output(package, supporting_points=[self.cited(
            "A restatement.", ("claim", "claim_fabricated"))])
        self.assertEqual(self.validate(package, payload)["omitted"][0]["reason_code"],
                         "unresolved_citation")
        payload = self.output(package, supporting_points=[self.cited(
            "A restatement.", ("not_a_kind", package["observations"][0]["claim_id"]))])
        self.assertEqual(self.validate(package, payload)["omitted"][0]["reason_code"],
                         "unknown_key")

    def test_irrelevant_evidence_citation_is_rejected(self):
        package = self.package()
        pairs = [(claim["claim_id"], citation["evidence_id"])
                 for claim in package["observations"]
                 for citation in claim["citations"] if citation.get("evidence_id")]
        by_claim = {}
        for claim_id, evidence_id in pairs:
            by_claim.setdefault(claim_id, set()).add(evidence_id)
        reference_claim = next(iter(by_claim))
        foreign = next(evidence_id for claim_id, evidence_id in pairs
                       if evidence_id not in by_claim[reference_claim])
        payload = self.output(package, supporting_points=[self.cited(
            "A restatement of the cited claim.",
            ("claim", reference_claim), ("evidence", foreign))])
        omitted = self.validate(package, payload)["omitted"]
        self.assertEqual(omitted[0]["reason_code"], "irrelevant_citation")

    def test_conflicting_number_and_code_are_rejected(self):
        package = self.package()
        conflict = self.output(package, supporting_points=[self.claim_item(
            package, text="Code A_phsA: 999999 raw recorded value(s) (units unconfirmed).")])
        self.assertEqual(self.validate(package, conflict)["omitted"][0]["reason_code"],
                         "unsupported_number")
        by_code = {c["text"].split(":")[0]: c for c in package["observations"]}
        codes = [c for c in by_code if c.startswith("Code ")]
        if len(codes) >= 2:
            first, second = codes[0], codes[1]
            other_code = second.split(" ", 1)[1]
            payload = self.output(package, supporting_points=[self.claim_item(
                package, text=f"Code {other_code}: a restated value.")])
            # citing claim `first` while naming the other code must be omitted
            item = payload["supporting_points"][0]
            claim = next(c for c in package["observations"]
                         if c["claim_id"] == item["citations"][0]["id"])
            if other_code not in claim["text"]:
                self.assertEqual(self.validate(package, payload)["omitted"][0]["reason_code"],
                                 "conflicting_evidence")

    def test_prohibited_identity_timezone_unit_are_rejected(self):
        package = self.package()
        for text, code in (
                ("Replace the transformer immediately.", "prohibited_conclusion"),
                ("This is the same physical asset as the ledger row.", "identity_conclusion"),
                ("The value was recorded at 12:00 UTC.", "timezone_inference"),
                ("The phase current reached 12.5 kV.", "unit_inference")):
            payload = self.output(package, supporting_points=[self.claim_item(
                package, text=text)])
            omitted = self.validate(package, payload)["omitted"]
            self.assertTrue(omitted, text)
            self.assertEqual(omitted[0]["reason_code"], code, text)

    def test_prohibited_direct_answer_is_not_shown(self):
        package = self.package()
        payload = self.output(package, direct_answer=self.claim_item(
            package, text="Replace the transformer immediately."))
        result = self.validate(package, payload)
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason_code"], "direct_answer_unsupported")
        self.assertIsNone(result["direct_answer"])
        self.assertEqual(result["omitted"][0]["reason_code"], "prohibited_conclusion")

    def test_unknowns_and_human_checks_sections(self):
        package = self.package()
        gap = package["gaps"][0]
        good = self.output(package, unknowns=[self.cited(
            "This gap limits the dependent conclusion only.", ("gap", gap["gap_id"]))])
        self.assertEqual(self.validate(package, good)["status"], "explained")
        missing_kind = self.output(package, unknowns=[self.cited(
            "An unsupported unknown.",
            ("evidence", package["observations"][0]["citations"][0]["evidence_id"]))])
        self.assertEqual(self.validate(package, missing_kind)["omitted"][0]["reason_code"],
                         "unsupported_claim")
        check = package["questions"][0]
        good_q = self.output(package, human_checks=[self.cited(
            "A person could verify this item from the cited source.",
            ("question", check["check_id"]))])
        self.assertEqual(self.validate(package, good_q)["status"], "explained")
        # An unknown is not a human check: the kind rules are section specific.
        wrong_section = self.output(package, human_checks=[self.cited(
            "This is a gap, not a check.", ("gap", gap["gap_id"]))])
        self.assertEqual(self.validate(package, wrong_section)["omitted"][0]["reason_code"],
                         "unsupported_claim")

    def test_unknown_criteria_dimension_is_surfaceable(self):
        package = self.package()
        dimensions = {item["dimension"] for item in package["unresolved_conclusions"]}
        self.assertIn("criteria_applicability", dimensions)
        payload = self.output(package, unknowns=[self.cited(
            "Applicability criteria are unresolved for this equipment.",
            ("dimension", "criteria_applicability"))])
        result = self.validate(package, payload)
        self.assertEqual(result["status"], "explained")
        response = review.explain(ASSET, generate=lambda _: json.dumps(payload))
        self.assertIn("unresolved_conclusions", response["supported_context"])
        self.assertEqual(response["status"], "explained")

    # --- structural / malformed ---------------------------------------------

    def test_malformed_output_is_rejected(self):
        package = self.package()
        self.assertEqual(review.validate_model_output("not json", package)["reason_code"],
                         "malformed_json")
        self.assertEqual(review.validate_model_output("", package)["reason_code"],
                         "empty_model_output")
        self.assertEqual(review.validate_model_output("[]", package)["reason_code"],
                         "bad_type")
        missing = self.output(package)
        missing.pop("human_checks")
        self.assertEqual(self.validate(package, missing)["reason_code"], "missing_key")
        extra = self.output(package)
        extra["invented"] = True
        self.assertEqual(self.validate(package, extra)["reason_code"], "unknown_key")

    def test_version_and_asset_mismatch_rejected(self):
        package = self.package()
        bad = self.output(package, output_version="gemini_answer.v9")
        self.assertEqual(self.validate(package, bad)["reason_code"], "bad_value")
        bad = self.output(package, asset_id="other-asset")
        self.assertEqual(self.validate(package, bad)["reason_code"], "asset_mismatch")

    def test_abstention_shapes(self):
        package = self.package()
        clean = self.output(package, direct_answer=self.empty_item(),
                            abstained=True, abstention_reason="Nothing supported.")
        result = self.validate(package, clean)
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason_code"], "model_abstained")
        inconsistent = self.output(package, abstained=True,
                                   abstention_reason="reason",
                                   direct_answer=self.empty_item(),
                                   supporting_points=[self.claim_item(package)])
        self.assertEqual(self.validate(package, inconsistent)["reason_code"], "bad_value")
        unsupported = self.output(package, direct_answer=self.cited(
            "A confused statement.", ("claim", "nope")))
        result = self.validate(package, unsupported)
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason_code"], "direct_answer_unsupported")

    def test_omitted_items_are_reported(self):
        package = self.package()
        payload = self.output(package, supporting_points=[
            self.claim_item(package),
            self.cited("An invented value 424242.", ("claim", "nope"))])
        result = self.validate(package, payload)
        self.assertEqual(result["status"], "explained")
        self.assertEqual(len(result["sections"]["supporting_points"]), 1)
        self.assertEqual(len(result["omitted"]), 1)

    # --- question-driven selection ------------------------------------------

    def test_question_sends_only_relevant_facts_not_the_whole_set(self):
        package = self.package()
        selection = review.select_relevant_evidence(package, "What evidence is missing?")
        counts = selection["counts"]
        self.assertLess(counts["selected_facts"], counts["total_facts"])
        self.assertEqual(counts["selected_facts"], review.MAX_SELECTED_FACTS)
        self.assertLessEqual(counts["selected_facts"], review.MAX_SELECTED_FACTS)
        self.assertEqual(selection["references"], [])
        # Both documented gaps are supplied for a "what is missing" question.
        self.assertEqual([g["gap_id"] for g in selection["gaps"]],
                         [g["gap_id"] for g in package["gaps"]])
        self.assertTrue(selection["register_questions"])
        # With no question the full validated set is kept (a deliberate review).
        full = review.select_relevant_evidence(package, None)
        self.assertFalse(full["question_provided"])
        self.assertEqual(full["counts"]["selected_facts"], full["counts"]["total_facts"])

    def test_selected_items_preserve_ids_citations_and_provenance(self):
        package = self.package()
        selection = review.select_relevant_evidence(package, "Summarize the recordings")
        source = {claim["claim_id"]: claim for claim in package["observations"]}
        self.assertTrue(selection["facts"])
        for fact in selection["facts"]:
            self.assertEqual(fact, source[fact["claim_id"]])
            for citation in fact["citations"]:
                self.assertIn("locator", citation)
                self.assertIn("provenance_status", citation)
                self.assertIn("source_sha256", citation)

    def test_prompt_carries_question_draft_and_selected_facts_only(self):
        package = self.package()
        question = "What evidence is missing?"
        selection = review.select_relevant_evidence(package, question)
        model_package = review._selected_package(package, selection)
        draft = review.agent_draft_module.build_deterministic_draft(
            review.build_review_inputs(ASSET)[1])
        prompt = review.build_prompt(model_package, user_question=question,
                                     selection=selection, draft=draft)
        payload = json.loads(prompt.split("VALIDATED_INPUT_JSON:\n", 1)[1])
        self.assertEqual(payload["user_question"], question)
        self.assertIsNotNone(payload["deterministic_draft"]["summary"])
        self.assertEqual(payload["deterministic_draft"]["fact_count_supplied"],
                         selection["counts"]["selected_facts"])
        self.assertLess(payload["deterministic_draft"]["fact_count_supplied"],
                        payload["deterministic_draft"]["fact_count_total"])
        self.assertEqual(len(payload["selected_deterministic_facts"]),
                         selection["counts"]["selected_facts"])
        # A fact that was not selected is not in the prompt at all.
        sent = {claim["claim_id"] for claim in selection["facts"]}
        not_sent = next(claim for claim in package["observations"]
                        if claim["claim_id"] not in sent)
        self.assertNotIn(not_sent["claim_id"], prompt)
        # Only ids that were actually supplied are offered.
        self.assertEqual(set(payload["available_citation_ids"]["claim"]), sent)

    def test_prompt_requires_a_grounded_answer_and_no_chain_of_thought(self):
        package = self.package()
        prompt = review.build_prompt(package, user_question="What evidence is missing?")
        self.assertIn("direct_answer", prompt)
        self.assertIn("supporting_points", prompt)
        self.assertIn("human_checks", prompt)
        self.assertIn("chain-of-thought", prompt)
        self.assertIn("never assert a unit or a timezone", prompt)
        self.assertIn("no approved reference applies", prompt)

    def test_citation_outside_the_selection_is_not_supplied(self):
        package = self.package()
        question = "What evidence is missing?"
        selection = review.select_relevant_evidence(package, question)
        sent = {claim["claim_id"] for claim in selection["facts"]}
        not_sent = next(claim for claim in package["observations"]
                        if claim["claim_id"] not in sent)
        payload = self.output(package, direct_answer=self.cited(
            not_sent["text"], ("claim", not_sent["claim_id"])))
        response = review.explain(ASSET, user_question=question,
                                  generate=lambda _: json.dumps(payload))
        self.assertEqual(response["status"], "abstained")
        self.assertEqual(response["reason_code"], "direct_answer_unsupported")
        self.assertEqual(response["omitted"][0]["reason_code"], "unresolved_citation")

    def test_missing_evidence_question_gets_a_gap_grounded_answer(self):
        """"What evidence is missing?" must answer honestly with no reference."""
        question = "What evidence is missing?"
        package = self.package()
        selection = review.select_relevant_evidence(package, question)
        gap_ids = [gap["gap_id"] for gap in selection["gaps"]]
        payload = self.output(
            package,
            direct_answer=self.cited(
                "No approved engineering reference applies. Missing evidence: the "
                "unit/scaling documentation and the timestamp timezone.",
                ("gap", gap_ids[0])),
            unknowns=[self.cited("Units and scaling are unconfirmed.", ("gap", gid))
                      for gid in gap_ids])
        response = review.explain(ASSET, user_question=question,
                                  generate=lambda _: json.dumps(payload))
        self.assertEqual(response["status"], "explained")
        self.assertTrue(response["explanation_accepted"])
        self.assertTrue(response["question_provided"])
        self.assertEqual(response["direct_answer"]["citations"][0]["kind"], "gap")
        self.assertIn("missing evidence", response["direct_answer"]["text"].lower())
        # The deployment has no approved engineering reference.
        self.assertTrue(response["no_relevant_reference"])
        self.assertTrue(response["no_approved_knowledge"])
        self.assertLess(response["evidence_selection"]["selected_facts"],
                        response["evidence_selection"]["total_facts"])

    def test_model_failure_with_question_abstains_safely(self):
        def boom(_prompt):
            raise RuntimeError("provider failed")

        response = review.explain(ASSET, user_question="What evidence is missing?",
                                  generate=boom)
        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["reason_code"], "model_error")
        self.assertFalse(response["model_generated"])
        self.assertFalse(response["explanation_accepted"])
        self.assertIsNone(response["direct_answer"])
        self.assertTrue(response["question_provided"])

    # --- route + no writes ---------------------------------------------------

    def test_route_explained_with_mocked_model_and_no_writes(self):
        package = self.package()
        payload = self.output(package, supporting_points=[self.claim_item(package)])
        main_module.ENABLE_GEMINI_REVIEW = True
        try:
            with mock.patch.object(review, "default_generate",
                                   lambda prompt: json.dumps(payload)):
                response = self.client.post(f"/api/assets/{ASSET}/gemini-review")
            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertEqual(body["status"], "explained")
            self.assertTrue(body["model_generated"])
            self.assertTrue(body["requires_human_review"])
            self.assertIn("cited_claims", body["supported_context"])
            self.assertEqual(len(body["sections"]["supporting_points"]), 1)
            self.assertIsNotNone(body["direct_answer"]["text"])
        finally:
            main_module.ENABLE_GEMINI_REVIEW = False
        self.assertEqual(list(self.review_dir.iterdir()), [])
        self.assertEqual(list(self.mapping_dir.iterdir()), [])
        self.assertEqual(list(self.intake_dir.iterdir()), [])

    def test_route_abstains_on_malformed_model_output(self):
        main_module.ENABLE_GEMINI_REVIEW = True
        try:
            with mock.patch.object(review, "default_generate",
                                   lambda prompt: "```json\n{oops}\n```"):
                response = self.client.post(f"/api/assets/{ASSET}/gemini-review")
            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertEqual(body["status"], "rejected")
            self.assertEqual(body["reason_code"], "malformed_json")
            self.assertEqual(body["sections"]["supporting_points"], [])
        finally:
            main_module.ENABLE_GEMINI_REVIEW = False

    def test_route_abstains_on_model_error(self):
        def boom(_prompt):
            raise RuntimeError("vertex unavailable")

        main_module.ENABLE_GEMINI_REVIEW = True
        try:
            with mock.patch.object(review, "default_generate", boom):
                response = self.client.post(f"/api/assets/{ASSET}/gemini-review")
            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertEqual(body["status"], "rejected")
            self.assertEqual(body["reason_code"], "model_error")
        finally:
            main_module.ENABLE_GEMINI_REVIEW = False

    # --- failure diagnostics and truthful labelling --------------------------

    def test_failed_request_is_not_labelled_model_generated(self):
        """A provider failure must not be presented as a model response."""
        secret = "PROMPT-AND-KEY-MUST-NOT-LEAK"

        class ProviderFailure(Exception):
            code = 403
            status = "PERMISSION_DENIED"

        def boom(_prompt):
            raise ProviderFailure(secret)

        main_module.ENABLE_GEMINI_REVIEW = True
        try:
            with mock.patch.object(review, "default_generate", boom):
                with self.assertLogs(level="WARNING") as logs:
                    response = self.client.post(f"/api/assets/{ASSET}/gemini-review")
        finally:
            main_module.ENABLE_GEMINI_REVIEW = False

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(body["reason_code"], "model_error")
        # A failed call is not a model response, and it says so.
        self.assertFalse(body["model_generated"])
        self.assertTrue(body["model_call"])
        self.assertEqual(body["model_route"], "failed")
        self.assertEqual(body["sections"], {
            "supporting_points": [], "unknowns": [], "human_checks": []})

        diagnostic = body["diagnostics"]
        self.assertEqual(diagnostic["error_class"], "ProviderFailure")
        self.assertEqual(diagnostic["provider_error_class"], "ProviderFailure")
        self.assertEqual(diagnostic["http_status"], 403)
        self.assertEqual(diagnostic["provider_status"], "PERMISSION_DENIED")
        self.assertEqual(diagnostic["category"], "permission_denied")

        # Nothing sensitive is echoed into the response or the log.
        self.assertNotIn(secret, json.dumps(body))
        self.assertNotIn(secret, "\n".join(logs.output))

    def test_provider_error_behind_route_wrapper_is_still_reportable(self):
        """The adapter wraps the provider error; its status must survive."""

        class FakeProviderError(Exception):
            """Duck-types ``google.genai.errors.APIError`` without importing it."""

            def __init__(self, code, status):
                super().__init__(f"{code} {status}")
                self.code = code
                self.status = status

        wrapped = review.ModelRouteError(
            "Primary Gemini model call failed and no fallback is configured.")
        try:
            try:
                raise FakeProviderError(429, "RESOURCE_EXHAUSTED")
            except FakeProviderError as provider_error:
                raise wrapped from provider_error
        except review.ModelRouteError as caught:
            diagnostic = review.diagnose_model_failure(caught)
        self.assertEqual(diagnostic["error_class"], "ModelRouteError")
        self.assertEqual(diagnostic["provider_error_class"], "FakeProviderError")
        self.assertEqual(diagnostic["http_status"], 429)
        self.assertEqual(diagnostic["provider_status"], "RESOURCE_EXHAUSTED")
        self.assertEqual(diagnostic["category"], "rate_limited")

    def test_diagnostics_record_only_allowlisted_fields(self):
        class Unauthenticated(Exception):
            code = 401
            status = "UNAUTHENTICATED"

        class NotFound(Exception):
            code = 404

        diagnostic = review.diagnose_model_failure(
            Unauthenticated("api_key=SECRET prompt=SECRET"))
        self.assertEqual(diagnostic, {
            "error_class": "Unauthenticated",
            "provider_error_class": "Unauthenticated",
            "http_status": 401,
            "provider_status": "UNAUTHENTICATED",
            "category": "unauthenticated",
        })
        self.assertNotIn("SECRET", json.dumps(diagnostic))
        self.assertEqual(
            review.diagnose_model_failure(NotFound())["category"], "not_found")
        self.assertEqual(
            review.diagnose_model_failure(TimeoutError())["category"], "timeout")
        self.assertEqual(
            review.diagnose_model_failure(review.ModelRouteError("x"))["category"],
            "model_route_error")

    def test_empty_model_output_is_not_labelled_generated(self):
        response = review.explain(ASSET, generate=lambda _: "   ")
        self.assertEqual(response["reason_code"], "empty_model_output")
        self.assertFalse(response["model_generated"])
        self.assertTrue(response["model_call"])

    def test_successful_call_reports_generated_without_diagnostics(self):
        package = self.package()
        payload = self.output(package, supporting_points=[self.claim_item(package)])
        response = review.explain(ASSET, generate=lambda _: json.dumps(payload))
        self.assertEqual(response["status"], "explained")
        self.assertTrue(response["model_generated"])
        self.assertTrue(response["explanation_accepted"])
        self.assertIsNone(response["diagnostics"])

    # --- output version: prompt, schema, validator ---------------------------

    def test_prompt_and_schema_require_exact_output_version(self):
        package = self.package()
        prompt = review.build_prompt(package)
        # The prompt names the exact literal the validator accepts.
        self.assertIn('"' + review.OUTPUT_VERSION + '"', prompt)
        self.assertIn("output_version", prompt)
        # The schema constrains output_version to that literal only.
        version_schema = review.MODEL_OUTPUT_SCHEMA["properties"]["output_version"]
        self.assertEqual(version_schema["type"], "string")
        self.assertEqual(version_schema.get("enum"), [review.OUTPUT_VERSION])
        self.assertIn("output_version", review.MODEL_OUTPUT_SCHEMA["required"])

    def test_schema_matches_the_answer_contract(self):
        properties = review.MODEL_OUTPUT_SCHEMA["properties"]
        self.assertEqual(set(properties), review.OUTPUT_ROOT_KEYS)
        for name in ("direct_answer",):
            self.assertEqual(properties[name]["type"], "object")
        for name in review.SECTION_ORDER:
            self.assertEqual(properties[name]["type"], "array")
        self.assertEqual(properties["direct_answer"]["properties"]["citations"]
                         ["items"]["properties"]["kind"]["enum"],
                         list(review.CITATION_KINDS))

    def test_matching_output_version_proceeds_to_item_validation(self):
        package = self.package()
        payload = self.output(package, supporting_points=[self.claim_item(package)])
        self.assertEqual(payload["output_version"], review.OUTPUT_VERSION)
        result = self.validate(package, payload)
        self.assertEqual(result["status"], "explained")
        self.assertEqual(len(result["sections"]["supporting_points"]), 1)
        self.assertEqual(result["omitted"], [])

    def test_output_version_is_strictly_compared(self):
        package = self.package()
        for wrong in ("gemini_answer.v9", "gemini_answer.v2 ",
                      "GEMINI_ANSWER.V2", "v2", "", "gemini_answer.v20"):
            result = self.validate(package, self.output(package, output_version=wrong))
            self.assertEqual(result["reason_code"], "bad_value", repr(wrong))
            self.assertEqual(result["sections"], {
                "supporting_points": [], "unknowns": [], "human_checks": []})

    def test_rejected_model_text_is_not_returned(self):
        package = self.package()
        sentinel = "MODEL-TEXT-SENTINEL"
        payload = self.output(
            package, output_version="gemini_answer.v9",
            direct_answer={"text": sentinel, "citations": [], "quotes": []})
        response = review.explain(ASSET, generate=lambda _: json.dumps(payload))
        # Gemini returned output, but the app accepted no answer from it.
        self.assertTrue(response["model_generated"])
        self.assertFalse(response["explanation_accepted"])
        self.assertEqual(response["status"], "rejected")
        self.assertNotIn(sentinel, json.dumps(response))
        self.assertEqual(response["sections"], {
            "supporting_points": [], "unknowns": [], "human_checks": []})

    def test_ui_separates_model_output_from_accepted_answer(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("j.explanation_accepted", js)
        self.assertIn("Gemini returned output", js)
        self.assertIn("accepted the explanation", js)
        self.assertIn("Model output withheld (no accepted explanation)", js)
        # Only accepted section items are ever rendered as model text.
        self.assertIn("(j.sections||{})[name]", js)

    # --- structured outcome logging ------------------------------------------

    def test_success_outcome_is_logged_with_match(self):
        package = self.package()
        payload = self.output(package, supporting_points=[self.claim_item(package)])
        with self.assertLogs(level="INFO") as logs:
            response = review.explain(ASSET, generate=lambda _: json.dumps(payload))
        self.assertEqual(response["status"], "explained")
        events = self.outcome_events(logs)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["status"], "explained")
        self.assertIsNone(event["reason_code"])
        self.assertEqual(event["model"], response["model"])
        self.assertEqual(event["model_route"], "primary")
        self.assertEqual(event["output_version_expected"], review.OUTPUT_VERSION)
        self.assertEqual(event["output_version_received"], review.OUTPUT_VERSION)
        self.assertTrue(event["output_version_matched"])

    def test_rejection_outcome_is_logged_with_version_mismatch(self):
        package = self.package()
        payload = self.output(package, output_version="gemini_answer.v9")
        with self.assertLogs(level="WARNING") as logs:
            response = review.explain(ASSET, generate=lambda _: json.dumps(payload))
        self.assertEqual(response["reason_code"], "bad_value")
        events = self.outcome_events(logs)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["status"], "rejected")
        self.assertEqual(event["reason_code"], "bad_value")
        self.assertFalse(event["output_version_matched"])
        self.assertEqual(event["output_version_received"], "gemini_answer.v9")
        self.assertEqual(event["output_version_expected"], review.OUTPUT_VERSION)

    def test_unsupported_direct_answer_outcome_is_logged(self):
        package = self.package()
        payload = self.output(package, direct_answer=self.cited(
            "An unsupported answer.", ("claim", "nope")))
        with self.assertLogs(level="WARNING") as logs:
            response = review.explain(ASSET, generate=lambda _: json.dumps(payload))
        self.assertEqual(response["status"], "abstained")
        event = self.outcome_events(logs)[0]
        self.assertEqual(event["status"], "abstained")
        self.assertEqual(event["reason_code"], "direct_answer_unsupported")
        self.assertTrue(event["output_version_matched"])

    def test_provider_failure_outcome_is_logged(self):
        def boom(_prompt):
            raise TimeoutError("provider timed out")

        with self.assertLogs(level="WARNING") as logs:
            response = review.explain(ASSET, generate=boom)
        self.assertEqual(response["reason_code"], "model_error")
        events = self.outcome_events(logs)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["status"], "rejected")
        self.assertEqual(event["reason_code"], "model_error")
        self.assertEqual(event["model"], response["model"])
        self.assertEqual(event["model_route"], "failed")
        self.assertFalse(event["output_version_matched"])
        self.assertEqual(event["diagnostics"]["category"], "timeout")

    def test_outcome_logs_never_include_sensitive_content(self):
        package = self.package()
        api_key = "API-KEY-SENTINEL-XYZ"
        question = "USER-QUESTION-SENTINEL"
        model_leak = "MODEL-OUTPUT-SENTINEL"
        oversized_version = "gemini_answer." + "v" * 80
        payload = self.output(package, output_version=oversized_version)
        payload[model_leak] = "value"  # an unknown key carries the sentinel
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": api_key}):
            with self.assertLogs(level="INFO") as logs:
                response = review.explain(
                    ASSET, generate=lambda _: json.dumps(payload), user_question=question)
        blob = "\n".join(logs.output)
        self.assertNotIn(api_key, blob)
        self.assertNotIn(question, blob)
        self.assertNotIn(model_leak, blob)
        self.assertEqual(response["reason_code"], "unknown_key")
        event = self.outcome_events(logs)[0]
        # The oversized/odd version token is not logged verbatim.
        self.assertEqual(event["output_version_received"], "invalid")
        self.assertFalse(event["output_version_matched"])
        self.assertNotIn(oversized_version, json.dumps(event))
        self.assertNotIn(model_leak, json.dumps(event))
        self.assertIn("redacted", json.dumps(event))

    def test_ui_distinguishes_failed_request_from_model_output(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("geminiModelAttribution", js)
        self.assertIn("j.model_generated", js)
        self.assertIn("no model output was produced", js)
        self.assertIn("No text was generated by any model", js)
        self.assertIn("Model output: none (request failed)", js)
        # The failure wording is defined before any "generated by" attribution.
        self.assertLess(js.index("no model output was produced"),
                        js.index("accepted the explanation, generated by "))

    def test_failure_logging_never_stringifies_the_exception(self):
        source = (ROOT / "app" / "gemini_review.py").read_text(encoding="utf-8")
        self.assertIn("diagnose_model_failure", source)
        self.assertIn("_log_review_outcome", source)
        self.assertIn("_sanitize_reason_message", source)
        # The raw message/response/prompt must never be copied into a log.
        self.assertNotIn("str(exc)", source)
        self.assertNotIn("str(exc.", source)
        self.assertNotIn("str(raw)", source)
        self.assertNotIn("json.dumps(raw", source)
        self.assertNotIn("json.dumps(prompt", source)

    # --- static safety -------------------------------------------------------

    def test_adapter_uses_secret_injected_gemini_api_key_not_vertex_identity(self):
        source = (ROOT / "app" / "gemini_review.py").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts" / "deploy_demo.sh").read_text(encoding="utf-8")
        self.assertIn('os.getenv("GEMINI_API_KEY"', source)
        self.assertIn("genai.Client(api_key=api_key)", source)
        self.assertNotIn("vertexai=True", source)
        self.assertIn("--set-secrets", deploy)
        self.assertNotIn("GEMINI_API_KEY=", deploy.split("ENV_VARS=")[0])

    def test_ui_labels_and_escapes_model_output(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Grounded evidence explanation", js)
        self.assertIn("Deterministic evidence (not model-generated)", js)
        self.assertIn("Model-generated explanation (unverified)", js)
        self.assertIn("gemini-review-disabled", js)
        self.assertIn("escapeHtml(it.text)", js)
        # The answer is rendered first, then evidence, unknowns, and checks.
        self.assertIn("assistant-direct-answer", js)
        self.assertIn("Direct answer", js)

    def test_review_route_is_not_reachable_by_get(self):
        # POST-only: a page load or a GET never triggers a model call.
        main_module.ENABLE_GEMINI_REVIEW = True
        try:
            response = self.client.get(f"/api/assets/{ASSET}/gemini-review")
        finally:
            main_module.ENABLE_GEMINI_REVIEW = False
        self.assertEqual(response.status_code, 405)

    def test_assistant_question_is_forwarded_to_existing_grounded_review(self):
        main_module.ENABLE_GEMINI_REVIEW = True
        expected = {"status": "abstained", "model_generated": True}
        try:
            with mock.patch.object(review, "explain", return_value=expected) as explain:
                response = self.client.post(
                    f"/api/assets/{ASSET}/gemini-review",
                    json={"question": "Summarize the observed evidence"})
        finally:
            main_module.ENABLE_GEMINI_REVIEW = False
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), expected)
        explain.assert_called_once_with(
            ASSET, root=main_module.ROOT,
            user_question="Summarize the observed evidence")

    def test_assistant_rejects_invalid_question_before_review(self):
        main_module.ENABLE_GEMINI_REVIEW = True
        try:
            with mock.patch.object(review, "explain") as explain:
                blank = self.client.post(
                    f"/api/assets/{ASSET}/gemini-review", json={"question": "  "})
                long = self.client.post(
                    f"/api/assets/{ASSET}/gemini-review",
                    json={"question": "x" * 501})
        finally:
            main_module.ENABLE_GEMINI_REVIEW = False
        self.assertEqual(blank.status_code, 400)
        self.assertEqual(long.status_code, 400)
        explain.assert_not_called()

    def test_ui_calls_model_only_after_explicit_click(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        # One endpoint is shared by the summary button and explicit monitoring
        # action; page load itself never calls the model.
        self.assertEqual(js.count("/gemini-review"), 1)
        self.assertRegex(
            js, r"#gemini-review-button'\)\.onclick=\(\)=>generateGeminiReview")
        self.assertIn("AI summary preview", js)
        self.assertIn("generateGeminiReview(requestedAsset,host)", js)
        self.assertIn("Gemini review is shown in AI summary preview below.", js)
        self.assertIn("geminiReviewInFlight", js)
        monitoring = next(line for line in js.splitlines()
                          if line.startswith("async function genMonitoring()"))
        self.assertIn("/api/monitoring-reviews", monitoring)
        self.assertIn("if(window.GEMINI_REVIEW_ENABLED)", monitoring)
        self.assertIn("generateGeminiReview(requestedAsset,host)", monitoring)
        self.assertIn("Gemini review is disabled in this deployment.", monitoring)
        # Page load does not touch the model endpoint.
        load_line = next(line for line in js.splitlines()
                         if line.strip().startswith("async function load()"))
        self.assertNotIn("gemini-review", load_line)
        # No scheduled poll or keepalive that would keep the service warm.
        self.assertNotIn("setInterval", js)

    def test_smart_assistant_uses_deterministic_agent_then_shared_gemini_route(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts" / "deploy_demo.sh").read_text(encoding="utf-8")
        self.assertIn("/api/agent/context/", js)
        self.assertIn("/api/agent/draft/", js)
        self.assertIn("fetch(geminiReviewUrl(asset)", js)
        self.assertIn("function geminiReviewUrl(id)", js)
        self.assertEqual(js.count("/gemini-review'"), 1)
        self.assertIn("assistant-form", html)
        self.assertIn('ENV_VARS+=",ENABLE_AGENT_DRAFT=true"', deploy)
        self.assertIn("GEMINI_API_KEY", (ROOT / "app" / "gemini_review.py").read_text())


class GeminiProviderRoutingTestCase(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {
            "GEMINI_API_KEY": "unit-test-key",
            "GEMINI_PRIMARY_MODEL": "gemini-4-argon",
            "GEMINI_FALLBACK_MODEL": "gemini-3.8-flash",
        })
        self._env.start()
        fake_google = types.ModuleType("google")
        fake_google.genai = types.SimpleNamespace(
            Client=mock.Mock(return_value=object()))
        self._google = mock.patch.dict(sys.modules, {"google": fake_google})
        self._google.start()

    def tearDown(self):
        self._google.stop()
        self._env.stop()

    def test_primary_success_reports_argon(self):
        with mock.patch.object(review, "_call_interactions", return_value='{"ok":true}') as call:
            result = review.default_generate("prompt")
        self.assertEqual(call.call_args.args[1], "gemini-4-argon")
        self.assertEqual(result.model, "gemini-4-argon")
        self.assertEqual(result.route, "primary")

    def test_primary_failure_uses_flash_fallback(self):
        with mock.patch.object(review, "_call_interactions",
                               side_effect=[TimeoutError(), '{"ok":true}']) as call:
            result = review.default_generate("prompt")
        self.assertEqual([c.args[1] for c in call.call_args_list],
                         ["gemini-4-argon", "gemini-3.8-flash"])
        self.assertEqual(result.model, "gemini-3.8-flash")
        self.assertEqual(result.route, "fallback")

    def test_auth_rejection_does_not_retry_with_same_key(self):
        class Unauthorized(Exception):
            code = 401

        with mock.patch.object(review, "_call_interactions",
                               side_effect=Unauthorized()) as call:
            with self.assertRaises(review.ModelRouteError):
                review.default_generate("prompt")
        call.assert_called_once()

    def test_missing_api_key_fails_before_client_creation(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaises(review.ModelRouteError):
                review.default_generate("prompt")


if __name__ == "__main__":
    unittest.main()
