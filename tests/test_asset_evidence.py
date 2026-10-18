"""Asset evidence profile tests (stdlib unittest, no cloud calls).

Proves the reviewed read-only asset-context-to-evidence flow for the target
asset, and that every fail-closed path holds: a wrong ID, an altered ledger or
telemetry endpoint, missing provenance, an unreviewed ledger field, and an
attempted unsupported conclusion.
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
from app import asset_evidence as evidence  # noqa: E402

TARGET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
TELEMETRY_FILE = "distribution_transformer_snapshot.json"
TELEMETRY_SHA = "d" * 64
LEDGER_SHA = "c" * 64
LEDGER_RECORD = {
    "psr_id": "LEDGER-1", "ast_id": "LEDGER-1", "ast_type": "0302",
    "ast_type_meaning": None, "voltage_level": "22", "voltage_level_en": "10 kV AC",
    "psr_state": "20", "psr_state_en": "in service",
    "insulation_medium": "2", "insulation_medium_en": "oil-immersed",
    "manufacturer": "006037", "model": "S11-M-400/10",
    "station": "ST-1", "line": None, "pole": None,
    "operate_date": "42086", "operate_date_iso": "2015-03-23",
    "manufacture_date": "40645", "manufacture_date_iso": "2011-04-12",
}
FORBIDDEN_KEYS = {
    "failure_probability", "fault_state", "fault_classification", "fault_diagnosis",
    "health_index", "remaining_life", "remaining_useful_life", "maintenance_action",
    "work_order", "severity", "probability", "condition", "risk", "recommendation",
}
TELEMETRY_ROW_CODES = ("A_phsA", "PhV_phsA", "TotW")


def setUpModule():
    import app.main as main_module
    main_module.BQ_TABLE = ""
    main_module.KNOWLEDGE_TABLE = ""
    main_module.REVIEW_TABLE = ""
    main_module.ENABLE_GEMINI = False
    main_module._cache.update(at=0.0, rows=None)


def make_row(asset, code, value, observed, *, point="P1",
             file=TELEMETRY_FILE, dataset="snapshot", record=0):
    import app.main as main_module
    row = {
        "source_file_id": file, "source_record_index": record, "psr_id": asset,
        "measurement_point_id": point, "equipment_type_code": "0408001",
        "measurement_type_code": code, "measurement_name": code,
        "measurement_value": value, "measurement_unit": None,
        "observed_at": observed, "dataset_type": dataset, "data_origin": "observed",
        "source_uri": file, "quality_flags": "unit not confirmed",
    }
    row["evidence_id"] = main_module._evidence_id(row)
    return row


def ledger_catalog(record=LEDGER_RECORD):
    return {
        "source": {"path": "/tmp/equipment_ledger.xlsx",
                   "sheet": "hlt_device_ledger_pool", "sha256": LEDGER_SHA},
        "records": [record] if record is not None else [],
    }


_AUTO = evidence._AUTO


def default_rows(asset="LEDGER-1"):
    return [make_row(asset, code, float(index + 1), f"2026-01-0{index + 1}T00:00:00",
                     record=index, point=f"P{index + 1}")
            for index, code in enumerate(TELEMETRY_ROW_CODES)]


def prepare(tmp, *, translation_status="reviewed", review_status="approved",
            record=LEDGER_RECORD, rows=None, asset="LEDGER-1", manifest=None,
            catalog=None):
    """Write the temp root files and return the shared bundle inputs.

    The catalog is written to disk and the small runtime review record is bound
    to it, so the equipment-context gate is exercised through the same source
    local and deployed runtime use (never the full lineage inventory).
    """
    out = Path(tmp) / "data/output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "equipment_catalog.json").write_text(
        json.dumps(catalog if catalog is not None else ledger_catalog(record)),
        encoding="utf-8")
    review_record = {
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
    (out / "ledger_review_record.json").write_text(
        json.dumps(review_record), encoding="utf-8")
    docs = Path(tmp) / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "evidence_register.json").write_text(
        (ROOT / "docs/evidence_register.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    return {"root": Path(tmp), "asset": asset,
            "rows": rows if rows is not None else default_rows(asset),
            "catalog": None,
            "manifest": manifest if manifest is not None else {TELEMETRY_FILE: TELEMETRY_SHA}}


def build_bundle(tmp, **kwargs):
    inputs = prepare(tmp, **kwargs)
    return context.build_context_bundle(
        inputs["asset"], root=inputs["root"], rows=inputs["rows"],
        catalog=inputs["catalog"], manifest=inputs["manifest"], chunks=[])


def build_profile(tmp, *, defect_records=_AUTO, source_families=_AUTO, **kwargs):
    """Build a profile over a temp root that records the ledger review status."""
    inputs = prepare(tmp, **kwargs)
    return evidence.build_asset_evidence(
        inputs["asset"], root=inputs["root"], rows=inputs["rows"],
        catalog=inputs["catalog"], manifest=inputs["manifest"], chunks=[],
        defect_records=defect_records, source_families=source_families)


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


class TargetProfileTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = evidence.build_asset_evidence(TARGET)
        cls.bundle = context.build_context_bundle(TARGET)

    def test_profile_is_valid_and_bound_to_the_bundle(self):
        self.assertTrue(evidence.validate_asset_evidence(self.profile, self.bundle,
                                                         root=ROOT))
        self.assertEqual(self.profile["asset_id"], TARGET)
        self.assertEqual(self.profile["bundle_digest_sha256"],
                         self.bundle["bundle_digest_sha256"])
        # Deterministic: the same inputs reproduce the same profile.
        self.assertEqual(evidence.build_asset_evidence(TARGET), self.profile)

    def test_nine_recorded_fields_and_absent_fields_unavailable(self):
        block = self.profile["context"]
        self.assertTrue(block["context_usable"])
        self.assertIsNone(block["blocked_reason_code"])
        self.assertEqual(block["status_label"], "translation reviewed")
        self.assertEqual(block["translation_status"], "reviewed")
        self.assertEqual(block["review_status"], "approved")
        self.assertEqual(block["recorded_count"], 9)
        self.assertEqual(sorted(block["absent_fields"]), ["line", "pole"])
        self.assertEqual(len(block["fields"]), 11)
        self.assertTrue(all(field["recorded"] for field in block["fields"]
                            if field["field"] not in ("line", "pole")))
        # Every field keeps its ledger locator, source hash, and review status.
        for field in block["fields"]:
            self.assertEqual(field["locator"], block["record"]["locator"])
            self.assertEqual(field["source_sha256"], block["record"]["source_sha256"])
            self.assertEqual(field["review_status"], "approved")
        # The reviewed translation scope is carried verbatim.
        self.assertEqual(block["translation_review"]["reviewer_role"],
                         "equipment ledger data owner")
        self.assertEqual(block["translation_review"]["reviewed_at"], "2026-10-17")

    def test_link_uses_only_the_exact_id_string_with_both_endpoints(self):
        link = self.profile["link"]
        self.assertEqual(link["status"], "linked")
        self.assertEqual(link["matching_method"], "exact_id_string")
        self.assertEqual(link["key"], "psr_id")
        self.assertEqual(link["verification_status"], "id_string_only")
        self.assertIn("physical identity", link["caveat"].lower())
        self.assertIn(f"psr_id={TARGET}", link["ledger_endpoint"]["locator"]["key"])
        self.assertTrue(link["ledger_endpoint"]["evidence_id"])
        self.assertTrue(link["telemetry_endpoint"]["evidence_id"])
        self.assertEqual(link["telemetry_endpoint"]["provenance_status"], "complete")
        self.assertEqual(link["ledger_endpoint"]["provenance_status"], "complete")

    def test_ledger_context_and_telemetry_are_distinct_evidence_types(self):
        block = self.profile["context"]
        self.assertEqual(block["record"]["locator"]["kind"], "ledger")
        self.assertTrue(all(field["locator"]["kind"] == "ledger"
                            for field in block["fields"]))
        telemetry = self.profile["telemetry"]
        self.assertEqual(telemetry["metrics_scope"]["evidence_type"], "telemetry")
        telemetry_ids = sorted(item["evidence_id"] for item in self.bundle["evidence_items"]
                               if item["evidence_type"] == "telemetry")
        self.assertEqual(telemetry["metrics_scope"]["evidence_count"], len(telemetry_ids))
        self.assertEqual(telemetry["metrics_scope"]["inventory_digest_sha256"],
                         contract.content_digest(telemetry_ids))
        self.assertEqual(telemetry["observation_count"], len(telemetry_ids))
        self.assertTrue(telemetry["descriptive_metrics"])

    def test_unavailable_evidence_is_honest(self):
        by_type = {item["evidence_type"]: item
                   for item in self.profile["unsupported_evidence"]}
        defect = by_type["defect_history"]
        self.assertEqual(defect["status"], "no_confirmed_link")
        self.assertEqual(defect.get("candidate_links", []), [])
        self.assertEqual(defect["scope"]["status"], "enumerated")
        self.assertEqual(defect["scope"]["record_count"], 1011)
        self.assertEqual(defect["scope"]["inventory_digest_sha256"],
                         evidence._defect_identity_digest(
                             evidence._load_defect_records(
                                 ROOT / evidence.DEFECT_RECORDS_PATH)))
        for evidence_type in ("test_record", "inspection_record", "maintenance_work_order"):
            item = by_type[evidence_type]
            self.assertEqual(item["status"], "source_unavailable")
            self.assertEqual(item["scope"]["status"], "enumerated")
            self.assertTrue(item["scope"]["record_count"])

    def test_unresolved_conclusions_are_the_five_dimensions(self):
        dimensions = {entry["dimension"] for entry in self.profile["unresolved_conclusions"]}
        self.assertEqual(dimensions, set(contract.CONTEXT_UNKNOWN_DIMENSIONS))
        self.assertTrue(all(entry["status"] == "unknown"
                            for entry in self.profile["unresolved_conclusions"]))

    def test_no_conclusion_keys_or_phrases(self):
        self.assertEqual(forbidden_keys_in(self.profile), set())
        # The closed prohibition list legitimately names what is prohibited; the
        # presentation prose must not assert any of those conclusions.
        presented = {key: value for key, value in self.profile.items()
                     if key != "prohibitions"}
        blob = json.dumps(presented, ensure_ascii=True).lower()
        for phrase in ("failure probability", "health index", "remaining life",
                       "imminent failure", "risk of failure"):
            self.assertNotIn(phrase, blob)

    def test_route_carries_the_profile(self):
        import app.main as main_module
        client = main_module.app.test_client()
        body = client.get(f"/api/lineage/equipment/{TARGET}").get_json()
        self.assertEqual(body["evidence_profile"]["asset_id"], TARGET)
        self.assertEqual(body["evidence_profile"]["link"]["matching_method"],
                         "exact_id_string")


class FailClosedTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_wrong_id_has_no_profile(self):
        self.assertIsNone(evidence.build_asset_evidence("no-such-asset"))
        self.assertIsNone(build_profile(str(self.tmp), asset="LEDGER-1",
                                        rows=[make_row("OTHER", "A_phsA", 1.0,
                                                       "2026-01-01T00:00:00")]))

    def test_altered_ledger_endpoint_fails_closed(self):
        profile = build_profile(str(self.tmp), defect_records=[], source_families=[])
        bundle = build_bundle(str(self.tmp))
        forged = json.loads(json.dumps(profile))
        forged["link"]["ledger_endpoint"]["locator"]["key"] = "psr_id=FORGED"
        with self.assertRaises(contract.AgentContractError) as ctx:
            evidence.validate_asset_evidence(forged, bundle, root=self.tmp)
        self.assertIn(ctx.exception.code, ("unsupported_context", "locator_mismatch"))
        forged = json.loads(json.dumps(profile))
        forged["link"]["ledger_endpoint"]["source_sha256"] = "a" * 64
        with self.assertRaises(contract.AgentContractError):
            evidence.validate_asset_evidence(forged, bundle, root=self.tmp)

    def test_altered_telemetry_summary_fails_closed(self):
        profile = build_profile(str(self.tmp), defect_records=[], source_families=[])
        bundle = build_bundle(str(self.tmp))
        forged = json.loads(json.dumps(profile))
        forged["telemetry"]["metrics_scope"]["inventory_digest_sha256"] = "b" * 64
        with self.assertRaises(contract.AgentContractError) as ctx:
            evidence.validate_asset_evidence(forged, bundle, root=self.tmp)
        self.assertEqual(ctx.exception.code, "unsupported_context")
        forged = json.loads(json.dumps(profile))
        forged["link"]["telemetry_endpoint"]["record_count"] += 1
        with self.assertRaises(contract.AgentContractError):
            evidence.validate_asset_evidence(forged, bundle, root=self.tmp)

    def test_missing_provenance_blocks_the_link(self):
        profile = build_profile(str(self.tmp), manifest={}, defect_records=[],
                                source_families=[])
        self.assertEqual(profile["link"]["status"], "blocked")
        self.assertEqual(profile["link"]["blocked_reason_code"],
                         "incomplete_provenance")
        self.assertIsNone(profile["link"]["ledger_endpoint"])
        self.assertIsNone(profile["link"]["telemetry_endpoint"])
        self.assertIn("physical identity", profile["link"]["caveat"].lower())

    def test_unreviewed_ledger_field_keeps_every_field_blocked(self):
        profile = build_profile(str(self.tmp), translation_status="translated_draft",
                                review_status="unreviewed", defect_records=[],
                                source_families=[])
        block = profile["context"]
        self.assertFalse(block["context_usable"])
        self.assertEqual(block["blocked_reason_code"], "unreviewed_translation")
        self.assertEqual(block["status_label"], "translation unreviewed")
        self.assertTrue(all(not field["usable_for_context"] for field in block["fields"]))
        self.assertTrue(all(field["blocked_reason_code"] == "unreviewed_translation"
                            for field in block["fields"]))
        # The ID-string link is independent of the translation review.
        self.assertEqual(profile["link"]["status"], "linked")
        # The reviewed values are still carried, but they are not usable context.
        self.assertEqual(block["recorded_count"], 9)

    def test_forged_profile_is_rejected(self):
        profile = build_profile(str(self.tmp), defect_records=[], source_families=[])
        bundle = build_bundle(str(self.tmp))
        for mutate, code in (
                (lambda p: p.update({"condition": "healthy"}), "unknown_key"),
                (lambda p: p.update({"fault_state": "normal"}), "unknown_key"),
                (lambda p: p["caveats"].append("This asset has a high failure probability."),
                 "prohibited_text"),
                (lambda p: p["unresolved_conclusions"].append(
                    {"dimension": "criteria_applicability", "status": "resolved",
                     "open_register_ids": []}), "unsupported_conclusion"),
                (lambda p: p["context"]["fields"][0].update(
                    {"usable_for_context": not p["context"]["fields"][0]["usable_for_context"]}),
                 "unsupported_context")):
            forged = json.loads(json.dumps(profile))
            mutate(forged)
            with self.assertRaises(contract.AgentContractError) as ctx:
                evidence.validate_asset_evidence(forged, bundle, root=self.tmp)
            self.assertEqual(ctx.exception.code, code)

    def test_defect_candidate_link_stays_candidate_only(self):
        record = {"container_id": {"missing": False, "value": "LEDGER-1"},
                  "obj_id": {"missing": True, "value": None}}
        profile = build_profile(str(self.tmp), defect_records=[record],
                                source_families=[])
        defect = profile["unsupported_evidence"][0]
        self.assertEqual(defect["status"], "candidate_only")
        self.assertEqual(len(defect["candidate_links"]), 1)
        candidate = defect["candidate_links"][0]
        self.assertEqual(candidate["matching_method"], "exact_id_string")
        self.assertEqual(candidate["verification_status"], "unconfirmed_namespace")
        self.assertEqual(candidate["locator"]["value"], "LEDGER-1")

    def test_absent_defect_source_reports_unavailable(self):
        profile = build_profile(str(self.tmp), defect_records=None,
                                source_families=None)
        defect = profile["unsupported_evidence"][0]
        self.assertEqual(defect["status"], "source_unavailable")
        self.assertEqual(defect["scope"]["status"], "unavailable")
        self.assertIsNone(defect["scope"]["record_count"])
        self.assertIsNone(defect["scope"]["inventory_digest_sha256"])

    def test_bundle_digest_mismatch_rejected(self):
        profile = build_profile(str(self.tmp), defect_records=[], source_families=[])
        bundle = build_bundle(str(self.tmp))
        forged = json.loads(json.dumps(profile))
        forged["bundle_digest_sha256"] = "e" * 64
        with self.assertRaises(contract.AgentContractError) as ctx:
            evidence.validate_asset_evidence(forged, bundle, root=self.tmp)
        self.assertEqual(ctx.exception.code, "bundle_digest_mismatch")


if __name__ == "__main__":
    unittest.main()
