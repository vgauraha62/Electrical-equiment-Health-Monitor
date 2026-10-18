"""Runtime ledger review record tests (stdlib unittest, no cloud calls).

Proves the small purpose-built review record is the single source of the
equipment-ledger translation review for both local development and the deployed
package, and that missing, malformed, stale-source-hash, altered-scope, and
unapproved records all fail closed to unreviewed. Recording the translation
review never resolves an evidence-register item.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import agent_context as context  # noqa: E402
from app import agent_contract as contract  # noqa: E402
import build_ledger_review_record as generator  # noqa: E402

TARGET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
RECORD_PATH = "data/output/ledger_review_record.json"
CATALOG_PATH = "data/output/equipment_catalog.json"
LINEAGE_PATH = "data/output/lineage_inventory.json"


def setUpModule():
    import app.main as main_module
    main_module.BQ_TABLE = ""
    main_module.KNOWLEDGE_TABLE = ""
    main_module.REVIEW_TABLE = ""
    main_module.ENABLE_GEMINI = False
    main_module._cache.update(at=0.0, rows=None)


def make_row(asset, code, value, observed, *, point="P1", record=0):
    import app.main as main_module
    row = {
        "source_file_id": "distribution_transformer_snapshot.json",
        "source_record_index": record, "psr_id": asset,
        "measurement_point_id": point, "equipment_type_code": "0408001",
        "measurement_type_code": code, "measurement_name": code,
        "measurement_value": value, "measurement_unit": None,
        "observed_at": observed, "dataset_type": "snapshot",
        "data_origin": "observed",
        "source_uri": "distribution_transformer_snapshot.json",
        "quality_flags": "unit not confirmed",
    }
    row["evidence_id"] = main_module._evidence_id(row)
    return row


def package_root(tmp):
    """A temp root with only the shipped runtime inputs (no lineage inventory)."""
    root = Path(tmp)
    (root / "data/output").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    for relative in (CATALOG_PATH, RECORD_PATH):
        shutil.copy2(ROOT / relative, root / relative)
    shutil.copy2(ROOT / "docs/evidence_register.json",
                 root / "docs/evidence_register.json")
    return root


def load_record():
    return json.loads((ROOT / RECORD_PATH).read_text(encoding="utf-8"))


def write_record(root, record):
    (Path(root) / RECORD_PATH).write_text(json.dumps(record), encoding="utf-8")


def package_bundle(root):
    rows = [make_row(TARGET, "A_phsA", 1.0, "2026-01-01T00:00:00")]
    return context.build_context_bundle(
        TARGET, root=Path(root), rows=rows, catalog=None, manifest={}, chunks=[])


class ValidReviewTestCase(unittest.TestCase):
    def test_local_record_is_the_reviewed_source(self):
        review = context.load_ledger_review(ROOT)
        self.assertIsNotNone(review)
        self.assertEqual(review["translation_status"], "reviewed")
        self.assertEqual(review["review_status"], "approved")
        self.assertTrue(review["recorded"])
        self.assertEqual(review["review"]["reviewer_role"],
                         "equipment ledger data owner")
        self.assertEqual(review["review"]["reviewed_at"], "2026-10-17")
        self.assertEqual(tuple(review["excludes"]), contract.LEDGER_REVIEW_EXCLUDES)
        self.assertEqual(context.load_ledger_translation(ROOT),
                         {"translation_status": "reviewed",
                          "review_status": "approved", "recorded": True})

    def test_record_is_small_and_purpose_built(self):
        record = load_record()
        self.assertEqual(set(record), set(contract.LEDGER_REVIEW_RECORD_KEYS))
        self.assertEqual(record["record_version"],
                         contract.LEDGER_REVIEW_RECORD_VERSION)
        self.assertEqual(record["source_id"], contract.LEDGER_SOURCE_ID)
        # It is only the review, never the full inventory.
        self.assertNotIn("sources", record)
        self.assertNotIn("equipment", record)
        self.assertLess((ROOT / RECORD_PATH).stat().st_size,
                        (ROOT / LINEAGE_PATH).stat().st_size)

    def test_record_matches_the_recorded_metadata_and_catalog(self):
        record = load_record()
        catalog_sha = hashlib.sha256((ROOT / CATALOG_PATH).read_bytes()).hexdigest()
        self.assertEqual(record["catalog_sha256"], catalog_sha)
        self.assertIsNotNone(contract.ledger_review_record(record, catalog_sha))
        lineage = json.loads((ROOT / LINEAGE_PATH).read_text(encoding="utf-8"))
        entry = next(source for source in lineage["sources"]
                     if "equipment ledger" in str(source.get("family", "")).lower())
        recorded = entry["translation_review"]
        self.assertEqual(record["translation_status"], entry["translation_status"])
        self.assertEqual(record["review_status"], entry["review_status"])
        for key in ("decision", "reviewed_at", "reviewed_by", "reviewer_role", "scope"):
            self.assertEqual(record["review"][key], recorded[key])
        self.assertEqual(record["excludes"], list(recorded["excludes"]))

    def test_local_bundle_context_is_reviewed(self):
        bundle = context.build_context_bundle(TARGET)
        self.assertTrue(contract.validate_context(bundle))
        ec = bundle["equipment_context"]
        self.assertTrue(ec["review_recorded"])
        self.assertEqual(ec["translation_status"], "reviewed")
        self.assertEqual(ec["review_status"], "approved")
        self.assertTrue(ec["context_usable"])
        self.assertEqual(ec["status_label"], "translation reviewed")


class PackagedReviewTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = package_root(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_package_has_no_lineage_inventory(self):
        self.assertFalse((self.root / LINEAGE_PATH).exists())
        self.assertTrue((self.root / RECORD_PATH).exists())
        self.assertTrue((self.root / CATALOG_PATH).exists())

    def test_packaged_review_matches_local(self):
        local = context.load_ledger_translation(ROOT)
        packaged = context.load_ledger_translation(self.root)
        self.assertEqual(packaged, local)
        # The deployed ledger context is reviewed and usable without the full
        # lineage inventory, exactly as local development.
        bundle = package_bundle(self.root)
        ec = bundle["equipment_context"]
        self.assertTrue(ec["context_usable"])
        self.assertEqual(ec["status_label"], "translation reviewed")
        self.assertEqual(ec["translation_status"], "reviewed")
        self.assertEqual(ec["review_status"], "approved")
        self.assertEqual(sum(1 for f in ec["fields"] if f["usable_for_context"]),
                         9)
        self.assertTrue(contract.validate_context(bundle))


class FailClosedReviewTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = package_root(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def assert_unreviewed(self, label="translation unreviewed"):
        self.assertIsNone(context.load_ledger_review(self.root))
        self.assertEqual(context.load_ledger_translation(self.root),
                         {"translation_status": "unreviewed",
                          "review_status": "unreviewed", "recorded": False})
        bundle = package_bundle(self.root)
        ec = bundle["equipment_context"]
        self.assertFalse(ec["review_recorded"])
        self.assertFalse(ec["context_usable"])
        self.assertEqual(ec["status_label"], label)
        self.assertTrue(all(not f["usable_for_context"] for f in ec["fields"]))
        self.assertTrue(contract.validate_context(bundle))

    def test_missing_record_fails_closed(self):
        (self.root / RECORD_PATH).unlink()
        self.assert_unreviewed()

    def test_missing_catalog_fails_closed(self):
        (self.root / CATALOG_PATH).unlink()
        # Without the catalog there is no ledger match at all, so no context.
        self.assert_unreviewed(label="ledger not matched")

    def test_malformed_record_fails_closed(self):
        for mutate in (
                lambda r: r.pop("review_status"),
                lambda r: r.update({"record_version": "ledger-review-v2"}),
                lambda r: r.update({"source_id": "SRC-OTHER"}),
                lambda r: r.update({"catalog_sha256": "not-a-hash"}),
                lambda r: r.update({"review_status": 7}),
                lambda r: r["review"].pop("scope"),
                lambda r: r["review"].update({"reviewed_by": ""}),
                lambda r: r.update({"unexpected": True})):
            record = load_record()
            mutate(record)
            write_record(self.root, record)
            self.assert_unreviewed()

    def test_stale_source_hash_fails_closed(self):
        # The catalog no longer matches the hash the review covers.
        catalog = self.root / CATALOG_PATH
        catalog.write_text(catalog.read_text(encoding="utf-8") + "\n",
                           encoding="utf-8")
        self.assert_unreviewed()

    def test_altered_scope_fails_closed(self):
        for mutate in (
                lambda r: r["review"].update(
                    {"scope": r["review"]["scope"] + " and physical asset identity"}),
                lambda r: r.update({"excludes": r["excludes"][:-1]}),
                lambda r: r.update({"excludes": r["excludes"] + ["time semantics"]})):
            record = load_record()
            mutate(record)
            write_record(self.root, record)
            self.assert_unreviewed()

    def test_unapproved_record_fails_closed(self):
        for translation_status, review_status in (
                ("translated_draft", "approved"), ("reviewed", "pending"),
                ("reviewed", "rejected"), ("not_started", "not_started")):
            record = load_record()
            record["translation_status"] = translation_status
            record["review_status"] = review_status
            write_record(self.root, record)
            self.assert_unreviewed()


class RegisterUntouchedTestCase(unittest.TestCase):
    def test_review_does_not_resolve_any_register_item(self):
        import tempfile
        register_before = (ROOT / "docs/evidence_register.json").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = package_root(tmp)
            bundle = package_bundle(root)
        self.assertTrue(bundle["equipment_context"]["context_usable"])
        self.assertEqual((ROOT / "docs/evidence_register.json").read_bytes(),
                         register_before)
        for entry in bundle["equipment_context"]["dependent_unknowns"]:
            self.assertEqual(entry["status"], "unknown", entry["dimension"])
        # The review opens the context gate but approves no engineering knowledge.
        self.assertEqual(bundle["approved_knowledge"], [])


def lineage_payload():
    return json.loads((ROOT / LINEAGE_PATH).read_text(encoding="utf-8"))


def ledger_entry(payload):
    return next(source for source in payload["sources"]
                if "equipment ledger" in str(source.get("family", "")).lower())


class GeneratorTestCase(unittest.TestCase):
    def catalog_sha(self):
        return hashlib.sha256((ROOT / CATALOG_PATH).read_bytes()).hexdigest()

    def test_generates_the_shipped_record(self):
        record = generator.build_record(lineage_payload(), self.catalog_sha())
        self.assertEqual(record, load_record())
        self.assertIsNotNone(
            contract.ledger_review_record(record, self.catalog_sha()))

    def test_refuses_absent_broadened_or_unapproved_reviews(self):
        mutations = (
            lambda payload: payload["sources"].remove(ledger_entry(payload)),
            lambda payload: ledger_entry(payload).update(
                {"translation_status": "translated_draft"}),
            lambda payload: ledger_entry(payload).update(
                {"review_status": "pending"}),
            lambda payload: ledger_entry(payload)["translation_review"].update(
                {"decision": "proposed"}),
            lambda payload: ledger_entry(payload)["translation_review"].update(
                {"scope": ledger_entry(payload)["translation_review"]["scope"]
                          + " and physical asset identity"}),
            lambda payload: ledger_entry(payload)["translation_review"].update(
                {"excludes":
                 ledger_entry(payload)["translation_review"]["excludes"][:-1]}),
        )
        for mutate in mutations:
            payload = deepcopy(lineage_payload())
            mutate(payload)
            with self.assertRaises(SystemExit):
                generator.build_record(payload, self.catalog_sha())


if __name__ == "__main__":
    unittest.main()
