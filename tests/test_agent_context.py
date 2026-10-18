"""Deterministic agent context bundle tests (stdlib unittest, no cloud calls).

Proves the bundle keeps source ids, hashes, locators, and uncertainty; that
citations resolve to real evidence; and that missing/conflicting/unmatched
evidence is preserved and marked rather than hidden or fabricated.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import agent_context as context  # noqa: E402
from app import agent_contract as contract  # noqa: E402
from app import agent_draft as draft_builder  # noqa: E402
from app.provenance import sha256_file  # noqa: E402

TARGET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
TELEMETRY_DIR = ROOT / "data/input/telemetry"
CHUNKS = Path(__file__).parent / "fixtures" / "agent" / "chunks_mixed.json"


def setUpModule():
    import app.main as main_module
    main_module.BQ_TABLE = ""
    main_module.KNOWLEDGE_TABLE = ""
    main_module.REVIEW_TABLE = ""
    main_module.ENABLE_GEMINI = False
    main_module._cache.update(at=0.0, rows=None)


def make_row(asset, code, value, observed, *, point="P1",
             file="distribution_transformer_snapshot.json", dataset="snapshot",
             record=0, equipment_type="0408001"):
    import app.main as main_module
    row = {
        "source_file_id": file,
        "source_record_index": record,
        "psr_id": asset,
        "measurement_point_id": point,
        "equipment_type_code": equipment_type,
        "measurement_type_code": code,
        "measurement_name": code,
        "measurement_value": value,
        "measurement_unit": None,
        "observed_at": observed,
        "dataset_type": dataset,
        "data_origin": "observed",
        "source_uri": file,
        "quality_flags": "unit not confirmed",
    }
    row["evidence_id"] = main_module._evidence_id(row)
    return row


LEDGER_SHA = "c" * 64
TELEMETRY_SHA = "d" * 64
TELEMETRY_FILE = "distribution_transformer_snapshot.json"
LEDGER_RECORD = {
    "psr_id": "LEDGER-1", "ast_id": "LEDGER-1", "ast_type": "0302",
    "ast_type_meaning": None, "voltage_level": "22", "voltage_level_en": "10 kV AC",
    "psr_state": "20", "psr_state_en": "in service",
    "insulation_medium": "2", "insulation_medium_en": "oil-immersed",
    "manufacturer": "006037", "model": "S11-M-400/10",
    "station": None, "line": None, "pole": None,
    "operate_date": "42086", "operate_date_iso": "2015-03-23",
    "manufacture_date": "40645", "manufacture_date_iso": "2011-04-12",
}


def ledger_catalog(record=LEDGER_RECORD):
    return {
        "source": {"path": "/tmp/equipment_ledger.xlsx",
                   "sheet": "hlt_device_ledger_pool", "sha256": LEDGER_SHA},
        "records": [record] if record is not None else [],
    }


def write_ledger_review(tmp, *, translation_status, review_status,
                        include_review_status=True):
    """Write the small runtime review record bound to the on-disk catalog."""
    out = Path(tmp) / "data/output"
    record = {
        "record_version": contract.LEDGER_REVIEW_RECORD_VERSION,
        "source_id": contract.LEDGER_SOURCE_ID,
        "catalog_sha256": hashlib.sha256(
            (out / "equipment_catalog.json").read_bytes()).hexdigest(),
        "translation_status": translation_status,
        "review_status": review_status,
        "review": {
            "decision": contract.LEDGER_REVIEW_DECISION,
            "reviewed_at": "2026-01-01",
            "reviewed_by": "synthetic-fixture",
            "reviewer_role": "synthetic reviewer",
            "scope": contract.LEDGER_REVIEW_SCOPE,
        },
        "excludes": list(contract.LEDGER_REVIEW_EXCLUDES),
    }
    if not include_review_status:
        del record["review_status"]
    (out / "ledger_review_record.json").write_text(
        json.dumps(record), encoding="utf-8")


def build_ledger_bundle(tmp, *, translation_status="translated_draft",
                        review_status="unreviewed", record=LEDGER_RECORD,
                        rows=None, asset="LEDGER-1", register=None,
                        include_review_status=True):
    """Build a bundle over a temp root that records the ledger review status.

    The runtime review record (bound to the exact on-disk catalog) is the single
    source of the gate, as it is locally and in the deployed package. The real
    evidence register is copied into the temp root (never modified); pass a
    ``register`` dict to substitute a synthetic one.
    """
    out = Path(tmp) / "data/output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "equipment_catalog.json").write_text(
        json.dumps(ledger_catalog(record)), encoding="utf-8")
    write_ledger_review(tmp, translation_status=translation_status,
                        review_status=review_status,
                        include_review_status=include_review_status)
    if register is None:
        register = json.loads((ROOT / "docs/evidence_register.json").read_text())
    docs = Path(tmp) / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "evidence_register.json").write_text(
        json.dumps(register), encoding="utf-8")
    if rows is None:
        rows = [make_row(asset, "A_phsA", 1.0, "2026-01-01T00:00:00")]
    return context.build_context_bundle(
        asset, root=Path(tmp), rows=rows, catalog=None,
        manifest={TELEMETRY_FILE: TELEMETRY_SHA}, chunks=[])


def synthetic_register(verified_ids=()):
    """A copy of the real register with the named items marked verified.

    Used only to demonstrate how the semantic gate would behave once a review is
    recorded; the real register is never modified.
    """
    register = json.loads((ROOT / "docs/evidence_register.json").read_text())
    verified = set(verified_ids)
    for item in register["items"]:
        if item["id"] in verified:
            item["status"] = "verified"
            item["approval"] = {"reviewed_by": "synthetic-fixture",
                                "reviewed_at": "2026-01-01T00:00:00"}
    return register


class ContextRealDataTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = context.build_context_bundle(TARGET)
        cls.manifest = context.load_manifest(ROOT)

    def test_bundle_validates(self):
        self.assertTrue(contract.validate_context(self.bundle))

    def test_evidence_ids_match_existing_evidence_path(self):
        import app.main as main_module
        evidence = main_module._build_evidence(TARGET)
        telemetry_ids = {item["evidence_id"] for item in self.bundle["evidence_items"]
                         if item["evidence_type"] == "telemetry"}
        self.assertEqual(telemetry_ids, set(evidence["evidence_ids"]))

    def test_source_hashes_match_files_and_manifest(self):
        telemetry = [item for item in self.bundle["evidence_items"]
                     if item["evidence_type"] == "telemetry"]
        self.assertTrue(telemetry)
        for item in telemetry:
            expected = sha256_file(TELEMETRY_DIR / item["source_file_id"])
            self.assertEqual(item["source_sha256"], expected)
            self.assertEqual(item["source_sha256"],
                             self.manifest[item["source_file_id"]])
            self.assertEqual(item["provenance_status"], "complete")
            self.assertFalse(item["provenance_gap"])

    def test_deterministic_digest(self):
        again = context.build_context_bundle(TARGET)
        self.assertEqual(self.bundle["bundle_digest_sha256"],
                         again["bundle_digest_sha256"])
        self.assertEqual(self.bundle, again)

    def test_locators_present_and_no_translated_path(self):
        for item in self.bundle["evidence_items"]:
            self.assertIsInstance(item["locator"], dict)
            self.assertTrue(item["locator"].get("kind"))
        blob = json.dumps(self.bundle, ensure_ascii=True)
        self.assertNotIn("data/en", blob)
        self.assertNotIn("data_english", blob)

    def test_ledger_match_is_exact_and_marks_uncertainty(self):
        match = self.bundle["asset_scope"]["ledger_match"]
        self.assertTrue(match["matched"])
        self.assertEqual(match["evidence_id"][:10], "ev_ledger_")
        self.assertEqual(match["profile"]["insulation_medium_en"], "oil-immersed")
        self.assertEqual(match["locator"]["kind"], "ledger")
        self.assertIn("psr_id=", match["locator"]["key"])

    def test_questions_are_register_backed_verbatim(self):
        register = json.loads((ROOT / "docs/evidence_register.json").read_text())
        by_id = {item["id"]: item for item in register["items"]}
        self.assertTrue(self.bundle["unresolved_questions"])
        for question in self.bundle["unresolved_questions"]:
            self.assertIn(question["question_id"], by_id)
            self.assertEqual(question["question"], by_id[question["question_id"]]["question"])
            self.assertEqual(question["domain"], by_id[question["question_id"]]["topic"])
            self.assertIn(question["verification_action"],
                          contract.VERIFICATION_ACTIONS)
            # The acquisition package need not ship: its only runtime field is
            # requests[].evidence_id, which equals the register id.
            self.assertEqual(question["acquisition_evidence_id"], question["register_id"])

    CLASS_LEVEL_IDS = ("EV-RULES-APPROVED", "EV-OUTCOMES-LABELED",
                       "EV-COVERAGE-EVAL", "EV-TIME-TIMEZONE")

    def test_class_level_items_stay_unresolved_but_are_not_suggested(self):
        register = {item["id"]: item for item in json.loads(
            (ROOT / "docs/evidence_register.json").read_text())["items"]}
        by_id = {q["question_id"]: q for q in self.bundle["unresolved_questions"]}
        draft = draft_builder.build_deterministic_draft(self.bundle)
        suggested = {s["question_id"] for s in draft["suggestions"]}
        for register_id in self.CLASS_LEVEL_IDS:
            # Kept unresolved with its original register id and verbatim question.
            self.assertIn(register_id, by_id)
            question = by_id[register_id]
            self.assertEqual(question["register_id"], register_id)
            self.assertEqual(question["question"], register[register_id]["question"])
            self.assertEqual(question["status"], register[register_id]["status"])
            self.assertTrue(question["applies"])
            # Its applicability is not specifically supported by the cited
            # evidence, so it is never presented as an evidence-backed suggestion.
            self.assertFalse(question["evidence_specific"])
            self.assertFalse(question["reason_supported"])
            self.assertNotIn(register_id, suggested)

    def test_per_code_unit_questions_keep_identity_and_share_group(self):
        register = json.loads((ROOT / "docs/evidence_register.json").read_text())["items"]
        code_items = [item for item in register if item["topic"] == "measurement_code"]
        by_id = {q["question_id"]: q for q in self.bundle["unresolved_questions"]}
        draft = draft_builder.build_deterministic_draft(self.bundle)
        suggested = {s["question_id"] for s in draft["suggestions"]}
        self.assertEqual(len(code_items), 14)
        codes = {item["measurement_code"] for item in code_items}
        self.assertEqual(len(codes), 14)
        for item in code_items:
            register_id = item["id"]
            self.assertIn(register_id, by_id)
            question = by_id[register_id]
            # Each code keeps its own register id, exact question text, and status.
            self.assertEqual(question["register_id"], register_id)
            self.assertEqual(question["question"], item["question"])
            self.assertEqual(question["status"], item["status"])
            self.assertEqual(question["measurement_code"], item["measurement_code"])
            # Shared grouping metadata marks them as one unit/scaling topic.
            self.assertEqual(question["group_id"], context.GROUP_UNITS_SCALING_ID)
            self.assertEqual(question["group_label"], context.GROUP_UNITS_SCALING_LABEL)
            # Readings witness that the code is present, not that its unit/scaling is
            # undocumented, so the item stays unresolved and is not suggested.
            self.assertTrue(question["evidence_specific"])
            self.assertFalse(question["reason_supported"])
            self.assertNotIn(register_id, suggested)
        # The whole unit/scaling topic is unresolved: no grouped suggestion is emitted.
        self.assertEqual([s for s in draft["suggestions"] if s["group_id"]], [])

    def test_ledger_translation_status_is_carried_and_reviewed(self):
        ledger = next(item for item in self.bundle["evidence_items"]
                      if item["evidence_type"] == "ledger")
        self.assertEqual(ledger["translation_status"], "reviewed")
        self.assertEqual(ledger["review_status"], "approved")
        match = self.bundle["asset_scope"]["ledger_match"]
        self.assertEqual(match["translation_status"], "reviewed")
        self.assertEqual(match["review_status"], "approved")
        # The recorded review removes the unreviewed-translation gap.
        self.assertFalse(any(g["kind"] == "unreviewed_translation"
                             for g in self.bundle["known_gaps"]))
        draft = draft_builder.build_deterministic_draft(self.bundle)
        # A reviewed translation still never turns the carried value into a claim
        # or a reason: the draft prose never states the translated meaning.
        prose = [claim["text"] for claim in draft["claims"]]
        prose += [suggestion["why"] for suggestion in draft["suggestions"]]
        for text in prose:
            self.assertNotIn("oil-immersed", text)
        # The construction suggestion cites the recorded (reviewed) status and
        # keeps the register question open.
        oil = next(s for s in draft["suggestions"] if s["question_id"] == "EV-ASSET-OIL")
        self.assertIn("review_status=approved", oil["why"])
        self.assertIn("translation_status=reviewed", oil["why"])
        self.assertIn("not verified", oil["why"])
        self.assertEqual(oil["citations"][0]["evidence_id"], ledger["evidence_id"])

    def test_exact_id_claim_cites_both_records_and_keeps_caveat(self):
        draft = draft_builder.build_deterministic_draft(self.bundle)
        direct = [c for c in draft["claims"] if c["kind"] == "direct"]
        self.assertEqual(len(direct), 1)
        claim = direct[0]
        self.assertIn("same exact ID string", claim["text"])
        self.assertIn("physical identity has not been verified", claim["text"])
        self.assertNotIn("identity matched exactly", claim["text"])
        # Both endpoints are cited: one telemetry record and one ledger record.
        types = sorted(citation["type"] for citation in claim["citations"])
        self.assertEqual(types, ["ledger", "telemetry"])
        relationship = claim["relationship"]
        self.assertEqual(relationship["relation"], "same_id_string")
        self.assertIn("physical identity", relationship["caveat"])
        telemetry_cited = {c["evidence_id"] for c in claim["citations"]
                           if c["type"] == "telemetry"}
        ledger_cited = {c["evidence_id"] for c in claim["citations"]
                        if c["type"] == "ledger"}
        self.assertIn(relationship["telemetry_evidence_id"], telemetry_cited)
        self.assertIn(relationship["ledger_evidence_id"], ledger_cited)

    def test_computations_are_independently_reproducible(self):
        draft = draft_builder.build_deterministic_draft(self.bundle)
        computations = [c for c in draft["claims"] if c["kind"] == "computation"]
        self.assertTrue(computations)
        for claim in computations:
            computation = claim["computation"]
            self.assertEqual(computation["formula_version"], contract.FORMULA_VERSION)
            self.assertIn(computation["formula"], contract.FORMULAS)
            scope = computation["scope"]
            self.assertEqual(scope["evidence_type"], "telemetry")
            self.assertEqual(scope["provenance_status"], "complete")
            self.assertTrue(scope["description"])
            self.assertEqual(computation["input_count"], len(computation["inputs"]))
            self.assertEqual(computation["inputs_digest_sha256"],
                             contract.computation_inputs_digest(computation["inputs"]))
            # The validator re-runs the formula over the recorded complete input set.
            self.assertEqual(contract.verify_computation(computation, self.bundle),
                             computation["inputs"])

    def test_min_and_max_cite_the_record_that_produced_the_result(self):
        draft = draft_builder.build_deterministic_draft(self.bundle)
        index = self.bundle["provenance_index"]["evidence"]
        checked = 0
        for claim in draft["claims"]:
            computation = claim["computation"]
            if not computation or computation["formula"] not in ("min", "max"):
                continue
            checked += 1
            extremal = {eid for eid in computation["inputs"]
                        if round(float(index[eid]["value"]), 6) == computation["result"]}
            cited = {citation["evidence_id"] for citation in claim["citations"]}
            self.assertTrue(cited & extremal,
                            f"{claim['claim_id']} omits the extremal record")
        self.assertEqual(checked, 28)

    def test_absence_gaps_name_the_complete_record_set_searched(self):
        telemetry_ids = sorted(item["evidence_id"] for item in self.bundle["evidence_items"]
                               if item["evidence_type"] == "telemetry")
        seen = set()
        for gap in self.bundle["known_gaps"]:
            if gap["kind"] not in contract.ABSENCE_GAP_KINDS:
                continue
            seen.add(gap["kind"])
            scope = gap["search_scope"]
            self.assertIsNotNone(scope, gap["gap_id"])
            self.assertEqual(scope["scope_id"], "telemetry_record_set")
            self.assertEqual(scope["status"], "enumerated")
            self.assertEqual(scope["record_count"], len(telemetry_ids))
            self.assertEqual(scope["inventory_digest_sha256"],
                             contract.computation_inputs_digest(telemetry_ids))
            self.assertTrue(scope["source_files"])
            # The statement states the scope of the search, not a sample of readings.
            self.assertIn(str(scope["record_count"]), gap["statement"])
        self.assertIn("unknown_units_scaling", seen)
        self.assertIn("unknown_timezone", seen)

    def test_record_set_scope_reports_unavailable_when_empty(self):
        scope = context._record_set_scope([], "telemetry_record_set", "telemetry records")
        self.assertEqual(scope["status"], "unavailable")
        self.assertIsNone(scope["record_count"])
        self.assertIsNone(scope["inventory_digest_sha256"])

    def test_reading_only_questions_are_not_suggested(self):
        register = {item["id"]: item for item in json.loads(
            (ROOT / "docs/evidence_register.json").read_text())["items"]}
        by_id = {q["question_id"]: q for q in self.bundle["unresolved_questions"]}
        draft = draft_builder.build_deterministic_draft(self.bundle)
        suggested = {s["question_id"] for s in draft["suggestions"]}
        # Units/scaling and source-key meaning need documentation, which readings
        # cannot establish, so they are never presented as evidence-backed suggestions.
        for register_id in ("EV-CODE-A_phsA", "EV-META-astId", "EV-META-astType"):
            question = by_id[register_id]
            self.assertTrue(question["applies"])
            self.assertTrue(question["evidence_specific"])
            self.assertFalse(question["reason_supported"])
            self.assertEqual(question["question"], register[register_id]["question"])
            self.assertEqual(question["status"], register[register_id]["status"])
            self.assertNotIn(register_id, suggested)
        # Observation-backed questions are still suggested.
        for register_id in ("EV-ASSET-IDENTITY", "EV-ASSET-OIL",
                            "EV-REACTIVE-SIGN", "EV-ZERO-READINGS"):
            self.assertTrue(by_id[register_id]["reason_supported"])
            self.assertIn(register_id, suggested)

    def test_default_has_no_approved_knowledge(self):
        self.assertEqual(self.bundle["approved_knowledge"], [])
        self.assertEqual(self.bundle["provenance_index"]["knowledge"], {})

    def test_draft_from_real_bundle_is_valid_and_cited(self):
        draft = draft_builder.build_deterministic_draft(self.bundle)
        self.assertTrue(contract.validate_draft(draft, self.bundle))
        index = self.bundle["provenance_index"]["evidence"]
        for claim in draft["claims"]:
            self.assertTrue(claim["citations"])
            for citation in claim["citations"]:
                self.assertIn(citation["evidence_id"], index)
                self.assertEqual(citation["source_sha256"],
                                 index[citation["evidence_id"]]["source_sha256"])
        for suggestion in draft["suggestions"]:
            self.assertIn(suggestion["verify_action"], contract.VERIFICATION_ACTIONS)

    def test_build_does_not_touch_frozen_artifacts(self):
        outputs = sorted((ROOT / "data/output").glob("**/*"))
        outputs = [path for path in outputs if path.is_file()]
        register = ROOT / "docs/evidence_register.json"
        before = {str(path): (path.stat().st_mtime_ns, path.stat().st_size)
                  for path in outputs + [register]}
        bundle = context.build_context_bundle(TARGET)
        draft_builder.build_deterministic_draft(bundle)
        after = {str(path): (path.stat().st_mtime_ns, path.stat().st_size)
                 for path in outputs + [register]}
        self.assertEqual(before, after)


class ContextEdgeCaseTestCase(unittest.TestCase):
    def test_unmatched_ledger_blocks_identity_only(self):
        rows = [make_row("UNMATCHED-1", "A_phsA", 12.5, "2026-01-01T00:00:00")]
        bundle = context.build_context_bundle("UNMATCHED-1", rows=rows)
        self.assertFalse(bundle["asset_scope"]["ledger_match"]["matched"])
        kinds = {gap["kind"] for gap in bundle["known_gaps"]}
        self.assertIn("unmatched_ledger_id", kinds)
        draft = draft_builder.build_deterministic_draft(bundle)
        self.assertEqual(draft["status"], contract.STATUS_GENERATED)
        self.assertFalse(any("same exact ID string" in claim["text"]
                             for claim in draft["claims"]))
        self.assertTrue(any(claim["computation"] for claim in draft["claims"]))

    def test_missing_hash_is_preserved_and_blocks_claims(self):
        rows = [make_row("A-2", "A_phsA", 9.0, "2026-01-01T00:00:00",
                         file="not_in_manifest.json")]
        bundle = context.build_context_bundle("A-2", rows=rows)
        item = next(i for i in bundle["evidence_items"]
                    if i["evidence_type"] == "telemetry")
        self.assertEqual(item["provenance_status"], "incomplete")
        self.assertTrue(item["provenance_gap"])
        self.assertIsNone(item["source_sha256"])
        self.assertEqual(item["value"], 9.0)  # the reading itself is not hidden
        gap = next(g for g in bundle["known_gaps"] if g["kind"] == "missing_source_hash")
        self.assertIn(item["evidence_id"], gap["evidence_ids"])
        draft = draft_builder.build_deterministic_draft(bundle)
        self.assertEqual(draft["status"], contract.STATUS_INSUFFICIENT_EVIDENCE)
        self.assertEqual(draft["claims"], [])

    def test_conflicting_values_block_only_the_conflict(self):
        rows = [
            make_row("A-3", "A_phsA", 1.0, "2026-01-01T00:00:00", dataset="history"),
            make_row("A-3", "A_phsA", 2.0, "2026-01-01T00:00:00", dataset="history",
                     record=1),
            make_row("A-3", "PhV_phsA", 100.0, "2026-01-01T00:00:00"),
        ]
        bundle = context.build_context_bundle("A-3", rows=rows)
        conflict = next(g for g in bundle["known_gaps"]
                        if g["kind"] == "conflicting_values")
        self.assertEqual(len(conflict["evidence_ids"]), 2)
        draft = draft_builder.build_deterministic_draft(bundle)
        texts = " ".join(claim["text"] for claim in draft["claims"])
        self.assertIn("PhV_phsA", texts)
        self.assertNotIn("Code A_phsA", texts)

    def test_zero_and_reactive_questions_apply(self):
        rows = [
            make_row("A-4", "A_phsA", 0.0, "2026-01-01T00:00:00"),
            make_row("A-4", "TotVar", -5.0, "2026-01-01T00:00:00"),
        ]
        bundle = context.build_context_bundle("A-4", rows=rows)
        by_id = {q["question_id"]: q for q in bundle["unresolved_questions"]}
        self.assertTrue(by_id["EV-ZERO-READINGS"]["applies"])
        self.assertTrue(by_id["EV-REACTIVE-SIGN"]["applies"])
        self.assertTrue(by_id["EV-TIME-TIMEZONE"]["applies"])
        self.assertFalse(by_id["EV-TIME-INTERVAL"]["applies"])

    def test_chunk_store_enforces_approved_reviewed_predicate(self):
        chunks = json.loads(CHUNKS.read_text())["chunks"]
        rows = [make_row("A-5", "A_phsA", 3.0, "2026-01-01T00:00:00")]
        bundle = context.build_context_bundle("A-5", rows=rows, chunks=chunks)
        self.assertEqual([item["chunk_id"] for item in bundle["approved_knowledge"]],
                         ["chunk-approved-1"])
        kinds = {gap["kind"] for gap in bundle["known_gaps"]}
        self.assertIn("unapproved_knowledge", kinds)
        self.assertIn("unreviewed_translation", kinds)
        self.assertIn("missing_knowledge_provenance", kinds)
        blob = json.dumps(bundle, ensure_ascii=True)
        self.assertNotIn("chunk-other-equipment-1", blob)
        self.assertTrue(contract.validate_context(bundle))


class CitationQualityTestCase(unittest.TestCase):
    """Per-rule coverage: supported, unsupported, incomplete, and conflicting cases.

    Rules exercised here (the validator's fail-closed side lives in
    ``test_agent_contract.py``): documentation status for units/timezone, scoped
    absence claims for the other audited kinds, and the reproducible count path.
    """

    @classmethod
    def setUpClass(cls):
        cls.bundle = context.build_context_bundle(TARGET)
        cls.draft = draft_builder.build_deterministic_draft(cls.bundle)

    def test_units_and_timezone_report_documentation_unavailable_not_absent(self):
        # Rule 1 (supported): the record-set claim is scoped to the telemetry set,
        # while documentation status is reported unavailable, never absent.
        by_kind = {gap["kind"]: gap for gap in self.bundle["known_gaps"]}
        for kind, subject in (("unknown_units_scaling", "unit_scaling"),
                              ("unknown_timezone", "timestamp_timezone")):
            gap = by_kind[kind]
            doc = gap["documentation_scope"]
            self.assertIsNotNone(doc, kind)
            self.assertEqual(doc["subject"], subject)
            self.assertEqual(doc["status"], "unavailable")
            self.assertIsNone(doc["inventory_source"])
            self.assertEqual(gap["search_scope"]["scope_id"], "telemetry_record_set")
            lowered = gap["statement"].lower()
            self.assertIn("documentation status: unavailable", lowered)
            self.assertIn("no documentation-absence claim", lowered)

    def test_units_and_timezone_questions_stay_unresolved_and_unsuggested(self):
        by_id = {q["question_id"]: q for q in self.bundle["unresolved_questions"]}
        suggested = {s["question_id"] for s in self.draft["suggestions"]}
        for register_id in ("EV-CODE-A_phsA", "EV-TIME-TIMEZONE"):
            question = by_id[register_id]
            self.assertTrue(question["applies"])
            self.assertFalse(question["reason_supported"])
            self.assertNotIn(register_id, suggested)

    def test_documentation_scope_is_unavailable_without_an_inventory(self):
        # Rule 1 (incomplete/unsupported): with no documentation inventory the
        # helper can only report unavailable and cannot name a source.
        scope = context._documentation_scope("unit_scaling", "not retained")
        self.assertEqual(scope["status"], "unavailable")
        self.assertIsNone(scope["inventory_source"])

    def test_missing_hash_scope_names_the_complete_manifest_inventory(self):
        # Rule 2 (supported): the manifest list is retained and independently
        # re-derived; the missing file is absent from it.
        rows = [make_row("A-2", "A_phsA", 9.0, "2026-01-01T00:00:00",
                         file="not_in_manifest.json")]
        bundle = context.build_context_bundle("A-2", rows=rows)
        gap = next(g for g in bundle["known_gaps"]
                   if g["kind"] == "missing_source_hash")
        scope = gap["search_scope"]
        entries = bundle["determinism"]["manifest_sources"]
        self.assertEqual(scope["scope_id"], "manifest_sources")
        self.assertEqual(scope["status"], "enumerated")
        self.assertEqual(scope["record_count"], len(entries))
        self.assertEqual(scope["inventory_digest_sha256"],
                         contract.content_digest(entries))
        self.assertNotIn("not_in_manifest.json", scope["source_files"])
        self.assertTrue(contract.validate_context(bundle))

    def test_missing_hash_scope_is_unavailable_without_a_manifest(self):
        # Rule 2 (incomplete): no manifest inventory is retained, so the scope is
        # reported unavailable rather than claiming the file absent.
        rows = [make_row("A-2", "A_phsA", 9.0, "2026-01-01T00:00:00",
                         file="not_in_manifest.json")]
        bundle = context.build_context_bundle("A-2", rows=rows, manifest={})
        self.assertEqual(bundle["determinism"]["manifest_sources"], [])
        gap = next(g for g in bundle["known_gaps"]
                   if g["kind"] == "missing_source_hash")
        self.assertEqual(gap["search_scope"]["status"], "unavailable")
        self.assertIsNone(gap["search_scope"]["record_count"])
        self.assertIsNone(gap["search_scope"]["inventory_digest_sha256"])
        self.assertTrue(contract.validate_context(bundle))

    def test_unmatched_ledger_scope_is_reported_unavailable(self):
        # Rule 2 (unsupported): the ledger catalog is not retained, so no scope is
        # invented; the status is explicitly unavailable.
        rows = [make_row("UNMATCHED-1", "A_phsA", 12.5, "2026-01-01T00:00:00")]
        bundle = context.build_context_bundle("UNMATCHED-1", rows=rows)
        gap = next(g for g in bundle["known_gaps"]
                   if g["kind"] == "unmatched_ledger_id")
        self.assertEqual(gap["search_scope"]["scope_id"], "ledger_catalog")
        self.assertEqual(gap["search_scope"]["status"], "unavailable")
        self.assertIsNone(gap["search_scope"]["record_count"])
        self.assertTrue(contract.validate_context(bundle))

    def test_missing_knowledge_provenance_scope_is_unavailable(self):
        # Rule 2 (unsupported): the full chunk store is not retained either.
        chunks = json.loads(CHUNKS.read_text())["chunks"]
        rows = [make_row("A-5", "A_phsA", 3.0, "2026-01-01T00:00:00")]
        bundle = context.build_context_bundle("A-5", rows=rows, chunks=chunks)
        gap = next(g for g in bundle["known_gaps"]
                   if g["kind"] == "missing_knowledge_provenance")
        self.assertEqual(gap["search_scope"]["scope_id"], "approved_chunk_store")
        self.assertEqual(gap["search_scope"]["status"], "unavailable")
        self.assertTrue(contract.validate_context(bundle))

    def test_count_and_zero_count_claims_expose_complete_input_paths(self):
        # Rule 3 (supported): citations stay concise, but the complete input record
        # ids, count, digest, and formula make the aggregate independently checkable.
        index = self.bundle["provenance_index"]["evidence"]
        checked = 0
        for claim in self.draft["claims"]:
            computation = claim["computation"]
            if not computation or computation["formula"] not in ("count", "zero_count"):
                continue
            checked += 1
            inputs = computation["inputs"]
            self.assertEqual(len(inputs), computation["input_count"])
            self.assertEqual(computation["inputs_digest_sha256"],
                             contract.computation_inputs_digest(inputs))
            if computation["formula"] == "count":
                self.assertEqual(computation["result"], len(inputs))
            else:
                self.assertEqual(computation["result"],
                                 sum(1 for eid in inputs if index[eid]["value"] == 0))
            cited = {citation["evidence_id"] for citation in claim["citations"]}
            self.assertTrue(cited)
            self.assertTrue(cited.issubset(set(inputs)))
            # The displayed citation is a readable subset, not the whole input set.
            self.assertLessEqual(len(cited), 3)
        self.assertGreater(checked, 0)


class EquipmentContextTestCase(unittest.TestCase):
    """Fail-closed equipment context: carried with provenance, gated on review.

    Covers a reviewed (supported) path, partial/missing ledger context, unmatched
    ids, unreviewed translations, conflicting records, and abstention when the
    context cannot support a conclusion. The validator's fail-closed side lives in
    ``test_agent_contract.EquipmentContextFailClosedTestCase``.
    """

    def test_real_bundle_carries_reviewed_context(self):
        bundle = context.build_context_bundle(TARGET)
        self.assertTrue(contract.validate_context(bundle))
        ec = bundle["equipment_context"]
        ledger = next(i for i in bundle["evidence_items"]
                      if i["evidence_type"] == "ledger")
        self.assertEqual(ec["source_id"], "SRC-LEDGER")
        self.assertTrue(ec["matched"])
        self.assertTrue(ec["context_usable"])
        self.assertIsNone(ec["blocked_reason_code"])
        self.assertEqual(ec["status_label"], "translation reviewed")
        self.assertTrue(ec["review_recorded"])
        self.assertEqual(ec["translation_status"], "reviewed")
        self.assertEqual(ec["review_status"], "approved")
        self.assertEqual(ec["evidence_id"], ledger["evidence_id"])
        self.assertEqual(ec["locator"], ledger["locator"])
        self.assertEqual(ec["source_sha256"], ledger["source_sha256"])
        self.assertEqual(ec["translation_status"], ledger["translation_status"])
        self.assertEqual(ec["review_status"], ledger["review_status"])
        names = {field["field"] for field in ec["fields"]}
        self.assertTrue({"equipment_type", "voltage_level", "manufacturer", "model",
                         "operate_date", "manufacture_date"} <= names)
        by_name = {field["field"]: field for field in ec["fields"]}
        for field in ec["fields"]:
            self.assertEqual(field["locator"], ledger["locator"])
            self.assertEqual(field["source_sha256"], ledger["source_sha256"])
            self.assertEqual(field["translation_status"], ledger["translation_status"])
            self.assertEqual(field["review_status"], ledger["review_status"])
        # A field with a recorded value is usable; a field with no value stays
        # unusable and reports why.
        self.assertTrue(by_name["model"]["usable_for_context"])
        self.assertIsNone(by_name["model"]["blocked_reason_code"])
        self.assertFalse(by_name["line"]["usable_for_context"])
        self.assertEqual(by_name["line"]["blocked_reason_code"], "missing_value")
        # The translated construction value is carried and, now reviewed, usable.
        oil = by_name["insulation_medium"]
        self.assertEqual(oil["translated"], "oil-immersed")
        self.assertTrue(oil["usable_for_context"])
        # The reviewed translation opens the context gate, but every dependent
        # conclusion stays unknown until its own register item is verified.
        self.assertTrue(all(u["status"] == "unknown"
                            for u in ec["dependent_unknowns"]))

    def test_reviewed_translation_opens_the_gate_but_no_context_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_ledger_bundle(tmp, translation_status="reviewed",
                                         review_status="approved")
        self.assertTrue(contract.validate_context(bundle))
        ec = bundle["equipment_context"]
        self.assertTrue(ec["context_usable"])
        self.assertIsNone(ec["blocked_reason_code"])
        usable = [f for f in ec["fields"] if f["usable_for_context"]]
        self.assertTrue(usable)
        self.assertTrue(all(f["blocked_reason_code"] is None for f in usable))
        # The reviewed translation opens the context gate, but every dependent
        # conclusion stays unknown until its register item is verified.
        self.assertTrue(all(u["status"] == "unknown"
                            for u in ec["dependent_unknowns"]))
        # A reviewed translation makes fields usable context, but the deterministic
        # draft still never turns carried context into an engineering claim.
        draft = draft_builder.build_deterministic_draft(bundle)
        self.assertTrue(contract.validate_draft(draft, bundle))
        prose = " ".join(c["text"] for c in draft["claims"]).lower()
        for needle in ("oil-immersed", "s11-m-400/10", "006037", "10 kv"):
            self.assertNotIn(needle, prose)

    def test_unreviewed_translation_keeps_every_field_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_ledger_bundle(tmp)
        ec = bundle["equipment_context"]
        self.assertTrue(ec["matched"])
        self.assertFalse(ec["context_usable"])
        self.assertEqual(ec["blocked_reason_code"], "unreviewed_translation")
        self.assertTrue(ec["fields"])
        self.assertTrue(all(not f["usable_for_context"] for f in ec["fields"]))
        self.assertTrue(all(f["blocked_reason_code"] == "unreviewed_translation"
                            for f in ec["fields"]))

    def test_partial_ledger_context_blocks_only_the_missing_fields(self):
        record = dict(LEDGER_RECORD, voltage_level=None, voltage_level_en=None)
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_ledger_bundle(tmp, translation_status="reviewed",
                                         review_status="approved", record=record)
        ec = bundle["equipment_context"]
        by_name = {f["field"]: f for f in ec["fields"]}
        self.assertTrue(by_name["model"]["usable_for_context"])
        self.assertIsNone(by_name["model"]["blocked_reason_code"])
        self.assertFalse(by_name["voltage_level"]["usable_for_context"])
        self.assertEqual(by_name["voltage_level"]["blocked_reason_code"],
                         "missing_value")
        # Some fields are usable, so the overall gate is open.
        self.assertTrue(ec["context_usable"])
        self.assertTrue(contract.validate_context(bundle))

    def test_no_usable_fields_reports_no_usable_context_fields(self):
        record = {key: None for key in LEDGER_RECORD}
        record["psr_id"] = "LEDGER-1"
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_ledger_bundle(tmp, translation_status="reviewed",
                                         review_status="approved", record=record)
        ec = bundle["equipment_context"]
        self.assertTrue(ec["matched"])
        self.assertFalse(ec["context_usable"])
        self.assertEqual(ec["blocked_reason_code"], "no_usable_context_fields")
        self.assertTrue(all(f["blocked_reason_code"] == "missing_value"
                            for f in ec["fields"]))
        self.assertTrue(contract.validate_context(bundle))

    def test_unmatched_ledger_yields_no_context_fields(self):
        rows = [make_row("NO-LEDGER", "A_phsA", 1.0, "2026-01-01T00:00:00")]
        bundle = context.build_context_bundle(
            "NO-LEDGER", rows=rows, catalog=ledger_catalog(None),
            manifest={TELEMETRY_FILE: TELEMETRY_SHA}, chunks=[])
        ec = bundle["equipment_context"]
        self.assertFalse(ec["matched"])
        self.assertIsNone(ec["evidence_id"])
        self.assertIsNone(ec["locator"])
        self.assertIsNone(ec["source_sha256"])
        self.assertEqual(ec["fields"], [])
        self.assertFalse(ec["context_usable"])
        self.assertEqual(ec["blocked_reason_code"], "unmatched_ledger_id")
        self.assertTrue(contract.validate_context(bundle))

    def test_conflicting_records_do_not_unblock_context(self):
        rows = [
            make_row("LEDGER-1", "A_phsA", 1.0, "2026-01-01T00:00:00",
                     dataset="history"),
            make_row("LEDGER-1", "A_phsA", 2.0, "2026-01-01T00:00:00",
                     dataset="history", record=1),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_ledger_bundle(tmp, rows=rows)
        self.assertIn("conflicting_values",
                      {gap["kind"] for gap in bundle["known_gaps"]})
        ec = bundle["equipment_context"]
        self.assertFalse(ec["context_usable"])
        self.assertEqual(ec["blocked_reason_code"], "unreviewed_translation")
        self.assertTrue(contract.validate_context(bundle))

    def test_reviewed_context_does_not_assert_rule_applicability(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_ledger_bundle(tmp, translation_status="reviewed",
                                         review_status="approved")
        # Reviewed context does not create rule applicability: no approved
        # knowledge exists, so no rule-based claim can be made.
        self.assertEqual(bundle["approved_knowledge"], [])
        draft = draft_builder.build_deterministic_draft(bundle)
        self.assertTrue(all(c["kind"] in ("direct", "computation")
                            for c in draft["claims"]))
        self.assertFalse(any("rule" in c["text"].lower() for c in draft["claims"]))

    def test_equipment_context_never_becomes_a_claim(self):
        bundle = context.build_context_bundle(TARGET)
        draft = draft_builder.build_deterministic_draft(bundle)
        prose = " ".join([c["text"] for c in draft["claims"]]
                         + [s["why"] for s in draft["suggestions"]])
        for needle in ("oil-immersed", "S11-M-400/10", "006037", "10 kV AC",
                       "in service", "2015-03-23", "2011-04-12"):
            self.assertNotIn(needle, prose)
        # The context gate is open, yet the deterministic draft never turns a
        # carried ledger value into a claim.
        self.assertTrue(bundle["equipment_context"]["context_usable"])

    def test_current_bundle_labels_context_translation_reviewed(self):
        bundle = context.build_context_bundle(TARGET)
        ec = bundle["equipment_context"]
        self.assertEqual(ec["status_label"], "translation reviewed")
        self.assertIsNone(ec["blocked_reason_code"])
        self.assertEqual(ec["translation_status"], "reviewed")
        self.assertEqual(ec["review_status"], "approved")
        self.assertTrue(ec["context_usable"])

    def test_dependent_unknowns_are_all_unknown_until_reviewed(self):
        bundle = context.build_context_bundle(TARGET)
        unknowns = {u["dimension"]: u
                    for u in bundle["equipment_context"]["dependent_unknowns"]}
        self.assertEqual(set(unknowns), set(contract.CONTEXT_UNKNOWN_DIMENSIONS))
        expected = {
            "criteria_applicability": {"EV-RULES-APPROVED"},
            "physical_asset_identity": {"EV-ASSET-IDENTITY"},
            "time_semantics": {"EV-TIME-TIMEZONE", "EV-TIME-INTERVAL"},
            "outcome_linkage": {"EV-OUTCOMES-LABELED"},
        }
        for dimension, ids in expected.items():
            self.assertEqual(set(unknowns[dimension]["register_ids"]), ids)
        self.assertEqual(len(unknowns["units"]["register_ids"]), 14)
        self.assertIn("EV-CODE-A_phsA", unknowns["units"]["register_ids"])
        # No real register item is verified, so no dependent conclusion is supported.
        for entry in unknowns.values():
            self.assertEqual(entry["status"], "unknown", entry["dimension"])
            self.assertEqual(entry["open_register_ids"], entry["register_ids"])
            self.assertTrue(entry["register_ids"])

    def test_missing_review_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_ledger_bundle(tmp, translation_status="reviewed",
                                         include_review_status=False)
        ec = bundle["equipment_context"]
        self.assertEqual(ec["translation_status"], "unreviewed")
        self.assertEqual(ec["review_status"], "unreviewed")
        self.assertFalse(ec["review_recorded"])
        self.assertFalse(ec["context_usable"])
        self.assertEqual(ec["status_label"], "translation unreviewed")
        self.assertTrue(all(not f["usable_for_context"] for f in ec["fields"]))
        self.assertTrue(contract.validate_context(bundle))

    def test_absent_translation_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "docs"
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "evidence_register.json").write_text(
                (ROOT / "docs/evidence_register.json").read_text(), encoding="utf-8")
            rows = [make_row("LEDGER-1", "A_phsA", 1.0, "2026-01-01T00:00:00")]
            bundle = context.build_context_bundle(
                "LEDGER-1", root=Path(tmp), rows=rows, catalog=ledger_catalog(),
                manifest={TELEMETRY_FILE: TELEMETRY_SHA}, chunks=[])
        ec = bundle["equipment_context"]
        self.assertFalse(ec["review_recorded"])
        self.assertEqual(ec["translation_status"], "unreviewed")
        self.assertEqual(ec["status_label"], "translation unreviewed")
        self.assertFalse(ec["context_usable"])

    def test_synthetic_reviewed_fixture_opens_the_semantic_gate(self):
        register = synthetic_register(verified_ids=("EV-ASSET-IDENTITY",))
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_ledger_bundle(tmp, translation_status="reviewed",
                                         review_status="approved", register=register)
        # The synthetic register is the trusted content for this bundle.
        self.assertTrue(contract.validate_context(bundle, register))
        ec = bundle["equipment_context"]
        self.assertTrue(ec["context_usable"])
        self.assertEqual(ec["status_label"], "translation reviewed")
        binding = ec["register_binding"]
        self.assertEqual(binding["register_digest_sha256"],
                         contract.register_digest(register))
        self.assertEqual(binding["item_count"], len(register["items"]))
        by_dim = {u["dimension"]: u for u in ec["dependent_unknowns"]}
        # The verified register item supports exactly one dependent conclusion.
        self.assertEqual(by_dim["physical_asset_identity"]["status"], "supported")
        self.assertEqual(by_dim["physical_asset_identity"]["open_register_ids"], [])
        self.assertEqual(by_dim["units"]["status"], "unknown")
        # The real register was not changed to make this pass.
        real = json.loads((ROOT / "docs/evidence_register.json").read_text())
        self.assertTrue(all(item["status"] != "verified" for item in real["items"]))
        # Validating the synthetic bundle against the real register fails closed.
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(bundle)
        self.assertEqual(caught.exception.code, "register_mismatch")

    def test_register_binding_matches_the_authoritative_register(self):
        bundle = context.build_context_bundle(TARGET)
        binding = bundle["equipment_context"]["register_binding"]
        register = contract.load_authoritative_register()
        self.assertEqual(binding["register_path"], "docs/evidence_register.json")
        self.assertEqual(binding["digest_algorithm"], "sha256")
        self.assertEqual(binding["register_version"],
                         contract.register_version(register))
        self.assertEqual(binding["register_digest_sha256"],
                         contract.register_digest(register))
        self.assertEqual(binding["item_count"], contract.register_item_count(register))
        self.assertEqual(binding["dimension_count"],
                         len(contract.CONTEXT_UNKNOWN_DIMENSIONS))
        # The carried unknowns are exactly the register-derived dimensions.
        self.assertEqual(bundle["equipment_context"]["dependent_unknowns"],
                         contract.derive_context_dimensions(register))
        self.assertTrue(contract.validate_context(bundle))

    def test_missing_authoritative_register_fails_closed(self):
        bundle = context.build_context_bundle(TARGET)
        original = contract.DEFAULT_REGISTER_PATH
        contract.DEFAULT_REGISTER_PATH = Path(tempfile.mkdtemp()) / "absent.json"
        try:
            with self.assertRaises(contract.AgentContractError) as caught:
                contract.validate_context(bundle)
        finally:
            contract.DEFAULT_REGISTER_PATH = original
        self.assertEqual(caught.exception.code, "unverified_register")

    def test_malformed_authoritative_register_fails_closed(self):
        bundle = context.build_context_bundle(TARGET)
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(bundle, {"items": [{"id": "EV-X"}]})
        self.assertEqual(caught.exception.code, "unverified_register")

    def test_changed_authoritative_register_is_rejected(self):
        bundle = context.build_context_bundle(TARGET)
        changed = json.loads((ROOT / "docs/evidence_register.json").read_text())
        changed["items"] = [item for item in changed["items"]
                            if item["id"] != "EV-ASSET-IDENTITY"]
        with self.assertRaises(contract.AgentContractError) as caught:
            contract.validate_context(bundle, changed)
        self.assertEqual(caught.exception.code, "register_mismatch")

    def test_bundle_without_a_register_fails_closed(self):
        # No docs/evidence_register.json under this root: the bundle binds the empty
        # register, so validating against the real register fails closed.
        with tempfile.TemporaryDirectory() as tmp:
            rows = [make_row("LEDGER-1", "A_phsA", 1.0, "2026-01-01T00:00:00")]
            bundle = context.build_context_bundle(
                "LEDGER-1", root=Path(tmp), rows=rows, catalog=ledger_catalog(),
                manifest={TELEMETRY_FILE: TELEMETRY_SHA}, chunks=[])
            self.assertEqual(
                bundle["equipment_context"]["register_binding"]["item_count"], 0)
            with self.assertRaises(contract.AgentContractError) as caught:
                contract.validate_context(bundle)
        self.assertEqual(caught.exception.code, "register_mismatch")


if __name__ == "__main__":
    unittest.main()
