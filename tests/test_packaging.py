"""Tests for Cloud Build source packaging (stdlib unittest, no gcloud needed).

Implements the subset of .gcloudignore semantics the file actually uses
(directory prefixes, basename wildcards, last-match-wins with ! negation)
and proves the runtime-required files survive filtering while dev-only
material is excluded. This checks packaging intent locally; it does not
build or deploy anything.
"""
import fnmatch
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MUST_SHIP = (
    "app/main.py",
    "app/agent_context.py",
    "app/agent_contract.py",
    "app/agent_draft.py",
    "app/asset_evidence.py",
    "app/gemini_review.py",
    "static/app.js",
    "static/index.html",
    "requirements.txt",
    "Procfile",
    "data/input/reference/defect_reference_sample.csv",
    "data/input/telemetry/distribution_network_circuit_breaker_snapshot.json",
    "data/input/telemetry/distribution_transformer_history.json",
    "data/input/telemetry/distribution_transformer_snapshot.json",
    "data/input/telemetry/feeder_snapshot.json",
    "data/input/telemetry/pole_mounted_circuit_breaker_history.json",
    "data/input/telemetry/pole_mounted_circuit_breaker_snapshot.json",
    "data/input/telemetry/pole_mounted_transformer_history.json",
    "data/input/telemetry/pole_mounted_transformer_snapshot.json",
    "data/output/statistical_baseline.json",
    "data/output/equipment_catalog.json",
    "data/output/ledger_review_record.json",
    # Per-file sha256 provenance for the eight telemetry exports above.
    "data/output/manifest.json",
    "data/output/asset_mapping_snapshot.json",
    "docs/evidence_register.json",
)
MUST_EXCLUDE = (
    "tests/test_api.py",
    "scripts/ingest.py",
    "data/source_archive/originals/related_materials.zip",
    "data/output/telemetry_observations.ndjson",
    # The full lineage inventory is a development artifact; the deployed runtime
    # carries only the small purpose-built ledger review record instead.
    "data/output/lineage_inventory.json",
    "data/output/asset_mappings/map_example.json",
    "data/en/menu.md",
    "docs/m1_6_statistical_baseline.md",
    ".venv/bin/python",
    # The agent runtime reads these nowhere; the acquisition package's only
    # runtime-relevant field (requests[].evidence_id) equals the register id, and
    # the schemas are documentation/parity only (validation is pure Python).
    "docs/evidence_acquisition_package.json",
    "docs/evidence_resolution_requests.json",
    "schemas/agent_context.schema.json",
    "schemas/agent_draft.schema.json",
    # Credential-like names are denied even if future allowlists broaden.
    ".env",
    ".env.production",
    "app/.env.local",
    "secrets/vertex.json",
    "app/secrets/private.json",
    "private.pem",
    "app/tls.key",
    "client.p12",
    "keystore.jks",
    "id_rsa",
    "service-account.json",
    "app/google_service_account.json",
    "nested/credentials-prod.json",
)


def load_patterns():
    patterns = []
    for line in (ROOT / ".gcloudignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_ignored(path, patterns):
    ignored = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        body = pattern[1:] if negated else pattern
        match = False
        if body.endswith("/"):
            match = path == body[:-1] or path.startswith(body)
        elif "/" in body:
            match = fnmatch.fnmatchcase(path, body)
        else:
            match = fnmatch.fnmatchcase(path.split("/")[-1], body)
        if match:
            ignored = not negated
    return ignored


class PackagingTestCase(unittest.TestCase):
    def test_runtime_files_ship(self):
        patterns = load_patterns()
        for path in MUST_SHIP:
            self.assertFalse(is_ignored(path, patterns), f"excluded: {path}")

    def test_dev_material_excluded(self):
        patterns = load_patterns()
        for path in MUST_EXCLUDE:
            self.assertTrue(is_ignored(path, patterns), f"shipped: {path}")

    def test_ledger_review_record_ships_without_full_inventory(self):
        patterns = load_patterns()
        # The small review record and the catalog it is bound to ship together.
        for path in ("data/output/ledger_review_record.json",
                     "data/output/equipment_catalog.json"):
            self.assertFalse(is_ignored(path, patterns), f"excluded: {path}")
        # The full lineage inventory (which also records the review) does not.
        self.assertTrue(is_ignored("data/output/lineage_inventory.json", patterns))

    def test_telemetry_provenance_ships_without_full_inventory(self):
        patterns = load_patterns()
        # The manifest is the per-file sha256 provenance for the telemetry
        # exports; it and every export it covers ship together.
        self.assertFalse(is_ignored("data/output/manifest.json", patterns))
        for path in MUST_SHIP:
            if path.startswith("data/input/telemetry/"):
                self.assertFalse(is_ignored(path, patterns), f"excluded: {path}")
        # The full lineage inventory (the source-family inventory) does not.
        self.assertTrue(is_ignored("data/output/lineage_inventory.json", patterns))

    def test_agent_runtime_has_no_acquisition_package_dependency(self):
        source = (ROOT / "app/agent_context.py").read_text(encoding="utf-8")
        self.assertNotIn("evidence_acquisition_package", source)

    def test_acquisition_ids_are_register_ids(self):
        import json
        register = json.loads((ROOT / "docs/evidence_register.json").read_text())
        acquisition = json.loads(
            (ROOT / "docs/evidence_acquisition_package.json").read_text())
        self.assertEqual({item["id"] for item in register["items"]},
                         {request["evidence_id"] for request in acquisition["requests"]})


if __name__ == "__main__":
    unittest.main()
