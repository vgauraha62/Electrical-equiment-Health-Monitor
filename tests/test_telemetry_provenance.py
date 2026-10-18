"""Packaged telemetry provenance for the evidence profile (stdlib unittest).

The ledger-to-telemetry evidence link is gated on every telemetry source file
carrying a manifest sha256 that matches the actual shipped file. This proves the
deployed package ships the provenance it needs (and not the full lineage
inventory), that the packaged profile matches local development, and that
missing, malformed, stale, or mismatched provenance keeps the link blocked with a
clear reason. No model, cloud, or register change is involved.
"""
import hashlib
import json
import shutil
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
NON_TARGET = "28DKX-3681"
MANIFEST = "data/output/manifest.json"
CATALOG = "data/output/equipment_catalog.json"
RECORD = "data/output/ledger_review_record.json"
REGISTER = "docs/evidence_register.json"
LINEAGE = "data/output/lineage_inventory.json"
TELEMETRY = "data/input/telemetry"
# Exactly the shipped runtime inputs relevant here: telemetry exports + their
# manifest provenance + the ledger catalog/review + the register.
TELEMETRY_FILES = tuple(sorted(
    str(path.relative_to(ROOT)) for path in (ROOT / TELEMETRY).glob("*.json")))
SHIPPED = (CATALOG, RECORD, MANIFEST, REGISTER) + TELEMETRY_FILES


def setUpModule():
    import app.main as main_module
    main_module.BQ_TABLE = ""
    main_module.KNOWLEDGE_TABLE = ""
    main_module.REVIEW_TABLE = ""
    main_module.ENABLE_GEMINI = False
    main_module._cache.update(at=0.0, rows=None)


def target_rows():
    import app.main as main_module
    return [row for row in main_module._target_rows()
            if row.get("psr_id") == TARGET]


def dev_inputs():
    """The deliberately dev-only inputs, injected to isolate provenance.

    The unreviewed defect draft (``data/en/``) and the lineage source-family
    inventory stay excluded from the package; injecting them keeps the
    local-versus-packaged comparison focused on telemetry provenance.
    """
    return {
        "defect_records":
            evidence._load_defect_records(ROOT / evidence.DEFECT_RECORDS_PATH),
        "source_families": evidence._source_family_rows(ROOT),
    }


def package_root(tmp, *, manifest=True, telemetry=True):
    """A temp root with only the shipped runtime inputs (no lineage inventory)."""
    root = Path(tmp)
    (root / "data/output").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / TELEMETRY).mkdir(parents=True)
    for relative in (CATALOG, RECORD, REGISTER):
        shutil.copy2(ROOT / relative, root / relative)
    if manifest:
        shutil.copy2(ROOT / MANIFEST, root / MANIFEST)
    if telemetry:
        for relative in TELEMETRY_FILES:
            shutil.copy2(ROOT / relative, root / relative)
    return root


def build(root, *, rows=None, catalog=None, **overrides):
    inputs = dev_inputs()
    inputs.update(overrides)
    return evidence.build_asset_evidence(
        TARGET, root=root, rows=rows if rows is not None else target_rows(),
        manifest=None, catalog=catalog, **inputs)


class PackagedProvenanceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = package_root(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_package_ships_provenance_without_lineage(self):
        self.assertFalse((self.root / LINEAGE).exists())
        self.assertTrue((self.root / MANIFEST).exists())
        self.assertTrue((self.root / CATALOG).exists())
        self.assertTrue((self.root / RECORD).exists())
        self.assertEqual(len(list((self.root / TELEMETRY).glob("*.json"))), 8)

    def test_packaged_manifest_is_verified_against_files(self):
        verified = context.load_manifest(self.root)
        expected = {Path(relative).name: hashlib.sha256(
            (self.root / relative).read_bytes()).hexdigest()
            for relative in TELEMETRY_FILES}
        self.assertEqual(verified, expected)
        self.assertEqual(len(verified), 8)

    def test_packaged_profile_matches_local(self):
        local = evidence.build_asset_evidence(
            TARGET, rows=target_rows(), **dev_inputs())
        packaged = build(self.root)
        self.assertEqual(packaged, local)
        link = packaged["link"]
        self.assertEqual(link["status"], "linked")
        self.assertIsNone(link["blocked_reason_code"])
        self.assertEqual(link["matching_method"], "exact_id_string")
        self.assertEqual(link["key"], "psr_id")
        self.assertEqual(link["verification_status"], "id_string_only")
        self.assertIn("physical identity", link["caveat"])
        self.assertIsNotNone(link["ledger_endpoint"])
        self.assertEqual(link["telemetry_endpoint"]["source_files"],
                         ["distribution_transformer_history.json",
                          "distribution_transformer_snapshot.json"])


class FailClosedProvenanceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = package_root(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def assert_link_blocked(self, reason="incomplete_provenance"):
        profile = build(self.root)
        self.assertIsNotNone(profile)
        link = profile["link"]
        self.assertEqual(link["status"], "blocked")
        self.assertEqual(link["blocked_reason_code"], reason)
        self.assertIsNone(link["ledger_endpoint"])
        self.assertIsNone(link["telemetry_endpoint"])
        # Exact-ID matching and its physical-identity caveat survive the block.
        self.assertEqual(link["matching_method"], "exact_id_string")
        self.assertIn("physical identity", link["caveat"])
        # The blocked link is still a valid, self-consistent profile.
        bundle = context.build_context_bundle(
            TARGET, root=self.root, rows=target_rows(), manifest=None)
        self.assertTrue(contract.validate_context(bundle))
        self.assertTrue(evidence.validate_asset_evidence(profile, bundle,
                                                         root=self.root))
        return profile

    def test_missing_manifest_blocks_link(self):
        (self.root / MANIFEST).unlink()
        self.assert_link_blocked()

    def test_malformed_manifest_blocks_link(self):
        for payload in (b"{not json", b'[1, 2, 3]', b'"text"',
                        b'{"sources": "nope"}',
                        b'{"sources": [{"file": "x.json"}]}',
                        b'{"sources": [{"file": "x.json", "sha256": "xyz"}]}',
                        b'{"sources": [7]}'):
            (self.root / MANIFEST).write_bytes(payload)
            self.assert_link_blocked()

    def test_stale_source_hash_blocks_link(self):
        payload = json.loads((self.root / MANIFEST).read_text(encoding="utf-8"))
        for source in payload["sources"]:
            source["sha256"] = "0" * 64
        (self.root / MANIFEST).write_text(json.dumps(payload), encoding="utf-8")
        self.assert_link_blocked()

    def test_mismatched_file_bytes_blocks_link(self):
        for name in ("distribution_transformer_history.json",
                     "distribution_transformer_snapshot.json"):
            path = self.root / TELEMETRY / name
            path.write_bytes(path.read_bytes() + b"\n")
        self.assert_link_blocked()

    def test_partially_mismatched_hash_still_links_on_the_valid_file(self):
        # A stale hash for one source does not invent a link failure for the
        # other: the representative telemetry endpoint stays complete.
        payload = json.loads((self.root / MANIFEST).read_text(encoding="utf-8"))
        for source in payload["sources"]:
            if source["file"] == "distribution_transformer_history.json":
                source["sha256"] = "0" * 64
        (self.root / MANIFEST).write_text(json.dumps(payload), encoding="utf-8")
        profile = build(self.root)
        self.assertEqual(profile["link"]["status"], "linked")
        self.assertEqual(profile["link"]["telemetry_endpoint"]["source_files"],
                         ["distribution_transformer_history.json",
                          "distribution_transformer_snapshot.json"])

    def test_mismatched_asset_id_blocks_link(self):
        # The ledger record's psr_id no longer equals the telemetry asset id.
        catalog = json.loads((ROOT / CATALOG).read_text(encoding="utf-8"))
        for record in catalog["records"]:
            if record.get("psr_id") == TARGET:
                record["psr_id"] = "OTHER-ASSET-ID"
        profile = build(self.root, catalog=catalog)
        self.assertEqual(profile["link"]["status"], "blocked")
        self.assertEqual(profile["link"]["blocked_reason_code"],
                         "unmatched_ledger_id")
        self.assertIsNone(profile["link"]["ledger_endpoint"])


class UnchangedAssetBehaviourTestCase(unittest.TestCase):
    def test_non_target_class_has_no_profile(self):
        self.assertIsNone(evidence.build_asset_evidence(NON_TARGET))

    def test_unknown_asset_has_no_profile(self):
        self.assertIsNone(evidence.build_asset_evidence("no-such-asset"))

    def test_packaged_non_target_and_unknown_have_no_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = package_root(tmp)
            self.assertIsNone(evidence.build_asset_evidence(
                NON_TARGET, root=root, manifest=None))
            self.assertIsNone(evidence.build_asset_evidence(
                "no-such-asset", root=root, manifest=None))


if __name__ == "__main__":
    unittest.main()
