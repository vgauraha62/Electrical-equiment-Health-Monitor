"""Manual asset-mapping tests (stdlib unittest, local temp store, no cloud calls).

Covers: registry availability from real supplied data, exact-ID vs manual
distinction, create/edit/remove lifecycle with an audit trail, required fields,
unknown records/equipment, duplicate/conflict, ambiguous and class-level
records staying unresolved, tampered hash/locator, missing/unavailable sources,
feature-flag disabled and enabled behaviour, persistence across reloads, and
that source files, frozen artifacts, and the evidence register stay unchanged.
No BigQuery, Cloud Storage, model, or other cloud call is made.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.asset_mapping as am  # noqa: E402
import app.equipment as equipment_directory  # noqa: E402
import app.main as main_module  # noqa: E402

FROZEN = (
    "docs/evidence_register.json",
    "data/output/manifest.json",
    "data/output/equipment_catalog.json",
    "data/output/ledger_review_record.json",
    "data/input/reference/defect_reference_sample.csv",
    "data/input/telemetry/distribution_transformer_snapshot.json",
)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _snapshot():
    return {rel: _sha256(ROOT / rel) for rel in FROZEN}


def _synthetic_registry():
    """A minimal registry with an ambiguous (multi-asset) record for guard tests."""
    record = {
        "source_family": "synthetic_family",
        "source_record_key": "AMB-1",
        "scope": "multi_asset",
        "record_label": "Ambiguous record AMB-1",
        "original_identifiers": {"ids": ["EQ1", "EQ2"]},
        "fields": {"note": "names two assets"},
        "source_locator": {"kind": "synthetic", "key": "AMB-1"},
        "source_sha256": "a" * 64,
        "exact_id_linked_equipment_id": None,
        "scope_note": "names more than one asset",
    }
    return {
        "registry_version": am.REGISTRY_VERSION,
        "families": [{"family_id": "synthetic_family", "label": "Synthetic",
                      "scope": "multi_asset", "availability": "available",
                      "record_count": 1, "source_files": [], "source_sha256": None,
                      "note": ""}],
        "records": {"synthetic_family": {"AMB-1": record}},
        "registry_digest_sha256": "b" * 64,
    }


class AssetMappingUnitTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        cls.registry, cls.equipment_ids = main_module._mapping_context()
        telemetry = equipment_directory.telemetry_assets(main_module._rows())
        catalog = equipment_directory.load_catalog(ROOT)
        items, counts = equipment_directory.directory(catalog["records"], telemetry)
        cls.items = items
        cls.ledger_only = next(i["asset_id"] for i in items
                               if i["coverage"] == "ledger only")
        cls.both = next(i["asset_id"] for i in items if i["coverage"] == "both")
        cls.unmatched = counts["unmatched_telemetry_ids"][0]
        cls.defect_key = sorted(
            cls.registry["records"][am.DEFECT_REFERENCE_FAMILY])[0]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="asset_mappings_")
        os.environ["ASSET_MAPPING_DIR"] = self.tmp
        self.directory = am.store_dir(ROOT)
        self.before = _snapshot()

    def tearDown(self):
        main_module.ENABLE_ASSET_MAPPING = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- registry / availability ------------------------------------------

    def test_registry_reports_available_and_unavailable_families(self):
        families = {f["family_id"]: f for f in self.registry["families"]}
        self.assertEqual(families[am.TELEMETRY_FAMILY]["availability"], "available")
        self.assertEqual(families[am.TELEMETRY_FAMILY]["record_count"], 13)
        self.assertEqual(families[am.DEFECT_REFERENCE_FAMILY]["availability"], "available")
        self.assertEqual(families[am.DEFECT_REFERENCE_FAMILY]["record_count"], 75)
        for family_id in ("defect_record", "test_record", "inspection_record",
                          "maintenance_work_order"):
            self.assertEqual(families[family_id]["availability"], "unavailable")
            self.assertIsNone(families[family_id]["record_count"])
            self.assertTrue(families[family_id]["note"])

    def test_candidates_exclude_linked_and_are_search_clues(self):
        result = am.candidates(self.registry, self.directory, exclude_linked=True,
                               limit=200)
        keys = {item["source_record_key"] for item in result["items"]}
        # 11 unmatched telemetry + 75 class-level defect references.
        self.assertEqual(result["total_candidates"], 86)
        self.assertNotIn(self.both, keys)  # exact-ID linked telemetry excluded
        self.assertIn(self.unmatched, keys)
        self.assertIn("clue", result["search_caveat"].lower())

    def test_exact_id_links_stay_distinct_from_manual_assignments(self):
        links = am.equipment_links(self.registry, [], self.both)
        self.assertEqual(links["counts"]["exact_id"], 1)
        self.assertEqual(links["exact_id_links"][0]["link_kind"], "exact_id")
        self.assertEqual(links["exact_id_links"][0]["verification_status"],
                         "exact_id_string_match")
        self.assertEqual(links["manual_assignments"], [])
        # An exact-ID linked record cannot be turned into a manual assignment.
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id=self.ledger_only,
                              source_family=am.TELEMETRY_FAMILY,
                              source_record_key=self.both, basis="x")
        self.assertEqual(ctx.exception.status, 400)

    # --- lifecycle ---------------------------------------------------------

    def test_create_edit_remove_manual_assignment(self):
        doc, created = am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.TELEMETRY_FAMILY, source_record_key=self.unmatched,
            basis="Confirmed by meter serial on site.", actor="analyst")
        self.assertTrue(created)
        self.assertEqual(doc["status"], "manually_assigned")
        self.assertEqual(doc["method"], "manual_user_assignment")
        self.assertFalse(doc["actor"]["verified"])
        self.assertEqual([h["event"] for h in doc["history"]], ["created"])

        updated = am.update_mapping(self.directory, self.registry, self.equipment_ids,
                                    doc["mapping_id"], doc["version"],
                                    basis="Corrected basis after site visit.",
                                    actor="analyst")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["basis"], "Corrected basis after site visit.")
        self.assertEqual([h["event"] for h in updated["history"]], ["created", "updated"])

        removed = am.remove_mapping(self.directory, updated["mapping_id"],
                                    updated["version"], note="wrong equipment")
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(removed["version"], 3)
        self.assertEqual([h["event"] for h in removed["history"]],
                         ["created", "updated", "removed"])
        # Removed mappings are hidden from listings but the file (and trail) survives.
        self.assertEqual(am.list_mappings(self.directory, equipment=self.ledger_only), [])
        kept = am.list_mappings(self.directory, equipment=self.ledger_only,
                                include_removed=True)
        self.assertTrue(any(d["mapping_id"] == doc["mapping_id"] for d in kept))

    def test_required_source_key_target_and_basis(self):
        base = dict(target_equipment_id=self.ledger_only,
                    source_family=am.TELEMETRY_FAMILY,
                    source_record_key=self.unmatched, basis="ok")
        for override in ({"source_record_key": ""}, {"source_family": ""},
                         {"target_equipment_id": ""}, {"basis": "   "}):
            payload = dict(base)
            payload.update(override)
            with self.assertRaises(am.MappingError) as ctx:
                am.create_mapping(self.directory, self.registry, self.equipment_ids,
                                  **payload)
            self.assertEqual(ctx.exception.status, 400, override)

    def test_unknown_source_record_and_equipment(self):
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id=self.ledger_only,
                              source_family="nope", source_record_key="x", basis="x")
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id=self.ledger_only,
                              source_family=am.TELEMETRY_FAMILY,
                              source_record_key="does-not-exist", basis="x")
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id="no-such-equipment",
                              source_family=am.TELEMETRY_FAMILY,
                              source_record_key=self.unmatched, basis="x")
        self.assertEqual(ctx.exception.status, 404)

    def test_duplicate_and_conflicting_assignments(self):
        doc, _ = am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.TELEMETRY_FAMILY, source_record_key=self.unmatched,
            basis="Same basis.")
        replay, created = am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.TELEMETRY_FAMILY, source_record_key=self.unmatched,
            basis="Same basis.")
        self.assertFalse(created)
        self.assertEqual(replay["mapping_id"], doc["mapping_id"])
        other = next(i["asset_id"] for i in self.items
                     if i["coverage"] == "ledger only"
                     and i["asset_id"] != self.ledger_only)
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id=other,
                              source_family=am.TELEMETRY_FAMILY,
                              source_record_key=self.unmatched, basis="conflict")
        self.assertEqual(ctx.exception.status, 409)

    # --- ambiguity ---------------------------------------------------------

    def test_ambiguous_and_class_level_records_stay_unresolved(self):
        registry = _synthetic_registry()
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, registry, {"EQ1"},
                              target_equipment_id="EQ1",
                              source_family="synthetic_family",
                              source_record_key="AMB-1", basis="looks right",
                              status="manually_assigned")
        self.assertEqual(ctx.exception.status, 400)
        doc, created = am.create_mapping(
            self.directory, registry, {"EQ1"}, target_equipment_id="EQ1",
            source_family="synthetic_family", source_record_key="AMB-1",
            basis="Scope unclear; two assets named.")
        self.assertTrue(created)
        self.assertEqual(doc["status"], "unresolved")

        class_level = self.registry["records"][am.DEFECT_REFERENCE_FAMILY][self.defect_key]
        self.assertEqual(class_level["scope"], "class_level")
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id=self.ledger_only,
                              source_family=am.DEFECT_REFERENCE_FAMILY,
                              source_record_key=self.defect_key, basis="x",
                              status="manually_assigned")
        self.assertEqual(ctx.exception.status, 400)
        doc, _ = am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.DEFECT_REFERENCE_FAMILY,
            source_record_key=self.defect_key, basis="Possibly relevant.")
        self.assertEqual(doc["status"], "unresolved")
        decorated = am.decorate(doc, self.registry)
        self.assertEqual(decorated["link_kind"], "unresolved")

    # --- tampering / missing sources --------------------------------------

    def test_tampered_hash_and_locator_rejected(self):
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id=self.ledger_only,
                              source_family=am.DEFECT_REFERENCE_FAMILY,
                              source_record_key=self.defect_key, basis="x",
                              source_sha256="0" * 64)
        self.assertEqual(ctx.exception.status, 422)
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id=self.ledger_only,
                              source_family=am.DEFECT_REFERENCE_FAMILY,
                              source_record_key=self.defect_key, basis="x",
                              source_locator={"kind": "forged"})
        self.assertEqual(ctx.exception.status, 422)

    def test_unavailable_family_and_missing_record(self):
        with self.assertRaises(am.MappingError) as ctx:
            am.create_mapping(self.directory, self.registry, self.equipment_ids,
                              target_equipment_id=self.ledger_only,
                              source_family="test_record", source_record_key="t1",
                              basis="x")
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("not available", str(ctx.exception))

    def test_decorate_flags_missing_source(self):
        forged = {"mapping_id": "map_forged", "source_family": "test_record",
                  "source_record_key": "t1", "source_sha256": "a" * 64,
                  "status": "manually_assigned", "target_equipment_id": self.ledger_only}
        decorated = am.decorate(forged, self.registry)
        self.assertFalse(decorated["source_available"])
        self.assertFalse(decorated["source_current"])

    # --- persistence / invariants -----------------------------------------

    def test_mapping_persists_across_reload(self):
        doc, _ = am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.DEFECT_REFERENCE_FAMILY,
            source_record_key=self.defect_key, basis="Persisted basis.")
        # A fresh client reads the mapping from disk, not from any in-memory state.
        fresh = main_module.app.test_client()
        response = fresh.get(f"/api/asset-mappings/{doc['mapping_id']}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["basis"], "Persisted basis.")
        listed = fresh.get(
            f"/api/asset-mappings/equipment/{self.ledger_only}").get_json()
        self.assertEqual(listed["counts"]["unresolved"], 1)

    def test_source_files_artifacts_and_register_unchanged(self):
        self.assertEqual(_snapshot(), self.before)
        # Writes go to the temp store, never under the repository data tree.
        self.assertEqual(str(self.directory), self.tmp)

    def test_no_cloud_or_model_dependency(self):
        source = (ROOT / "app/asset_mapping.py").read_text(encoding="utf-8")
        for needle in ("from google", "import google", "genai", "bigquery", "httpx"):
            self.assertNotIn(needle, source)
        for module in ("google.cloud.bigquery", "google.cloud.storage", "google.genai"):
            self.assertNotIn(module, sys.modules)

    def test_snapshot_exports_only_active_manual_relationships(self):
        manual, _ = am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.TELEMETRY_FAMILY,
            source_record_key=self.unmatched,
            basis="Local equipment register crosswalk.",
            basis_origin="source_document",
            supporting_reference="local crosswalk row 4",
            actor="local user")
        am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.DEFECT_REFERENCE_FAMILY,
            source_record_key=self.defect_key,
            basis="Class-level reference; no single asset link.")

        snapshot = am.build_mapping_snapshot(
            self.directory, self.registry, self.equipment_ids)
        self.assertEqual(snapshot["mapping_count"], 1)
        self.assertEqual(snapshot["mappings"][0]["mapping_id"], manual["mapping_id"])
        self.assertEqual(snapshot["mappings"][0]["status"], "manually_assigned")
        self.assertEqual(snapshot["mappings"][0]["basis"],
                         "Local equipment register crosswalk.")
        self.assertEqual(snapshot["mappings"][0]["actor"]["verified"], False)

        output = Path(self.tmp) / "package/data/output/asset_mapping_snapshot.json"
        output.parent.mkdir(parents=True)
        output.write_text(json.dumps(snapshot), encoding="utf-8")
        loaded = am.load_mapping_snapshot(output, self.registry, self.equipment_ids)
        self.assertEqual(loaded, snapshot["mappings"])

    def test_empty_snapshot_is_valid_and_has_no_links(self):
        snapshot = am.build_mapping_snapshot(
            self.directory, self.registry, self.equipment_ids)
        self.assertEqual(snapshot["mapping_count"], 0)
        self.assertEqual(snapshot["mappings"], [])
        output = Path(self.tmp) / "package/empty_snapshot.json"
        output.parent.mkdir(parents=True)
        output.write_text(json.dumps(snapshot), encoding="utf-8")
        self.assertEqual(am.load_mapping_snapshot(
            output, self.registry, self.equipment_ids), [])

    def test_snapshot_rejects_digest_source_target_and_status_drift(self):
        manual, _ = am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.TELEMETRY_FAMILY,
            source_record_key=self.unmatched, basis="Crosswalk entry.")
        valid = am.build_mapping_snapshot(
            self.directory, self.registry, self.equipment_ids)
        output = Path(self.tmp) / "package/mapping_snapshot.json"
        output.parent.mkdir(parents=True)

        for mutate in (
                lambda obj: obj.update(snapshot_digest_sha256="0" * 64),
                lambda obj: obj.update(registry_digest_sha256="0" * 64),
                lambda obj: obj["mappings"][0].update(source_sha256="0" * 64),
                lambda obj: obj["mappings"][0].update(target_equipment_id="missing"),
                lambda obj: obj["mappings"][0].update(status="unresolved")):
            altered = json.loads(json.dumps(valid))
            mutate(altered)
            output.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaises(am.MappingError):
                am.load_mapping_snapshot(output, self.registry, self.equipment_ids)

        # A valid snapshot still loads after all tampering attempts.
        output.write_text(json.dumps(valid), encoding="utf-8")
        self.assertEqual(am.load_mapping_snapshot(
            output, self.registry, self.equipment_ids)[0]["mapping_id"],
            manual["mapping_id"])

    def test_effective_mappings_uses_snapshot_and_local_removal_overlay(self):
        manual, _ = am.create_mapping(
            self.directory, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.TELEMETRY_FAMILY,
            source_record_key=self.unmatched, basis="Crosswalk entry.")
        snapshot = am.build_mapping_snapshot(
            self.directory, self.registry, self.equipment_ids)
        snapshot_path = Path(self.tmp) / "package/data/output/asset_mapping_snapshot.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        self.assertEqual(len(am.effective_mappings(
            Path(self.tmp) / "empty_store", snapshot_path,
            self.registry, self.equipment_ids)), 1)

        am.remove_mapping(self.directory, manual["mapping_id"], 1)
        self.assertEqual(am.effective_mappings(
            self.directory, snapshot_path, self.registry, self.equipment_ids), [])


class AssetMappingRouteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module.ENABLE_GEMINI = False
        main_module._cache.update(at=0.0, rows=None)
        cls.client = main_module.app.test_client()
        cls.registry, cls.equipment_ids = main_module._mapping_context()
        telemetry = equipment_directory.telemetry_assets(main_module._rows())
        catalog = equipment_directory.load_catalog(ROOT)
        items, counts = equipment_directory.directory(catalog["records"], telemetry)
        cls.ledger_only = next(i["asset_id"] for i in items
                               if i["coverage"] == "ledger only")
        cls.both = next(i["asset_id"] for i in items if i["coverage"] == "both")
        cls.unmatched = counts["unmatched_telemetry_ids"][0]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="asset_mappings_routes_")
        os.environ["ASSET_MAPPING_DIR"] = self.tmp
        self.directory = am.store_dir(ROOT)
        main_module.ENABLE_ASSET_MAPPING = False

    def tearDown(self):
        main_module.ENABLE_ASSET_MAPPING = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store_files(self):
        return {p.name for p in Path(self.tmp).glob("*.json")}

    def test_health_reports_flag(self):
        self.assertFalse(self.client.get("/api/health").get_json()[
            "asset_mapping_enabled"])
        main_module.ENABLE_ASSET_MAPPING = True
        self.assertTrue(self.client.get("/api/health").get_json()[
            "asset_mapping_enabled"])

    def test_disabled_reads_work_but_writes_are_refused(self):
        families = self.client.get("/api/asset-mappings/source-families")
        self.assertEqual(families.status_code, 200)
        self.assertFalse(families.get_json()["writes_enabled"])
        candidates = self.client.get("/api/asset-mappings/candidates?limit=5")
        self.assertEqual(candidates.status_code, 200)
        self.assertFalse(candidates.get_json()["writes_enabled"])
        equipment = self.client.get(
            f"/api/asset-mappings/equipment/{self.both}")
        self.assertEqual(equipment.status_code, 200)
        self.assertEqual(equipment.get_json()["counts"]["exact_id"], 1)

        before = self._store_files()
        for path in ("/api/asset-mappings",
                     f"/api/asset-mappings/map_missing",
                     f"/api/asset-mappings/map_missing/remove"):
            response = self.client.post(path, json={})
            self.assertEqual(response.status_code, 404, path)
            self.assertIn("disabled", response.get_json()["error"])
        self.assertEqual(self._store_files(), before)  # nothing written

    def test_unknown_equipment_read_is_404(self):
        self.assertEqual(
            self.client.get("/api/asset-mappings/equipment/no-such").status_code, 404)

    def test_packaged_snapshot_links_are_read_only_and_remove_candidates(self):
        source_store = Path(self.tmp) / "source_store"
        runtime_store = Path(self.tmp) / "runtime_store"
        am.create_mapping(
            source_store, self.registry, self.equipment_ids,
            target_equipment_id=self.ledger_only,
            source_family=am.TELEMETRY_FAMILY,
            source_record_key=self.unmatched,
            basis="Recorded local crosswalk.",
            basis_origin="source_document",
            supporting_reference="crosswalk row 7")
        snapshot = am.build_mapping_snapshot(
            source_store, self.registry, self.equipment_ids)
        snapshot_root = Path(self.tmp) / "packaged_root"
        snapshot_dir = snapshot_root / "data/output"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "asset_mapping_snapshot.json").write_text(
            json.dumps(snapshot), encoding="utf-8")

        os.environ["ASSET_MAPPING_DIR"] = str(runtime_store)
        original_root = main_module.ROOT
        original_context = main_module._mapping_context
        main_module.ROOT = snapshot_root
        main_module._mapping_context = lambda: (self.registry, self.equipment_ids)
        try:
            response = self.client.get(
                f"/api/asset-mappings/equipment/{self.ledger_only}")
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            body = response.get_json()
            self.assertFalse(body["writes_enabled"])
            self.assertEqual(body["counts"]["manually_assigned"], 1)
            link = body["manual_assignments"][0]
            self.assertEqual(link["source_record_key"], self.unmatched)
            self.assertEqual(link["basis"], "Recorded local crosswalk.")
            self.assertEqual(link["verification_status"],
                             "manual_user_assignment_not_source_confirmed")
            candidates = self.client.get(
                "/api/asset-mappings/candidates?limit=200").get_json()
            self.assertNotIn(self.unmatched,
                             {item["source_record_key"] for item in candidates["items"]})
            self.assertFalse(runtime_store.exists())
        finally:
            main_module.ROOT = original_root
            main_module._mapping_context = original_context

    def test_enabled_write_lifecycle_and_grouping(self):
        main_module.ENABLE_ASSET_MAPPING = True
        create = self.client.post("/api/asset-mappings", json={
            "target_equipment_id": self.ledger_only,
            "source_family": am.TELEMETRY_FAMILY,
            "source_record_key": self.unmatched,
            "basis": "Site walkdown confirmed the meter.",
            "actor": "analyst"})
        self.assertEqual(create.status_code, 201, create.get_data(as_text=True))
        body = create.get_json()
        self.assertTrue(body["created"])
        self.assertEqual(body["link_kind"], "manually_assigned")
        self.assertEqual(body["verification_status"],
                         "manual_user_assignment_not_source_confirmed")
        mapping_id = body["mapping_id"]

        equipment = self.client.get(
            f"/api/asset-mappings/equipment/{self.ledger_only}").get_json()
        self.assertTrue(equipment["writes_enabled"])
        self.assertEqual(equipment["counts"]["manually_assigned"], 1)
        self.assertEqual(equipment["groups"][0]["source_family"],
                         am.TELEMETRY_FAMILY)
        self.assertEqual(equipment["unavailable_families"][0]["family_id"],
                         "defect_record")

        # Candidate list no longer offers the now-mapped record.
        candidates = self.client.get(
            "/api/asset-mappings/candidates?limit=200").get_json()
        keys = {item["source_record_key"] for item in candidates["items"]}
        self.assertNotIn(self.unmatched, keys)

        stale = self.client.post(f"/api/asset-mappings/{mapping_id}",
                                 json={"from_version": 99, "basis": "late"})
        self.assertEqual(stale.status_code, 409)
        edited = self.client.post(f"/api/asset-mappings/{mapping_id}",
                                  json={"from_version": 1, "basis": "Revised basis."})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.get_json()["version"], 2)
        removed = self.client.post(f"/api/asset-mappings/{mapping_id}/remove",
                                   json={"from_version": 2, "note": "duplicate"})
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.get_json()["status"], "removed")
        self.assertEqual(
            self.client.get(f"/api/asset-mappings/equipment/{self.ledger_only}"
                            ).get_json()["counts"]["manually_assigned"], 0)

    def test_unresolved_class_level_via_route(self):
        main_module.ENABLE_ASSET_MAPPING = True
        key = sorted(am._defect_reference_records(ROOT))[0]
        response = self.client.post("/api/asset-mappings", json={
            "target_equipment_id": self.ledger_only,
            "source_family": am.DEFECT_REFERENCE_FAMILY,
            "source_record_key": key,
            "basis": "Possibly relevant; class-level scope."})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["link_kind"], "unresolved")
        forced = self.client.post("/api/asset-mappings", json={
            "target_equipment_id": self.ledger_only,
            "source_family": am.DEFECT_REFERENCE_FAMILY,
            "source_record_key": key,
            "basis": "force it",
            "status": "manually_assigned"})
        # The scope rule is enforced before any duplicate check: a class-level
        # reference can never be forced onto one asset.
        self.assertEqual(forced.status_code, 400)
        self.assertIn("not asset-specific", forced.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
