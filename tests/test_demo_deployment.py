"""Tests for the demo deployment procedure and package inspector.

These run only local, read-only checks: the package inspector, its ignore /
forbidden logic, and the static content of the demo deploy script. No gcloud
command is executed and nothing is uploaded or deployed.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_demo_package as package  # noqa: E402

DEPLOY = ROOT / "scripts" / "deploy_demo.sh"


class PackageInspectorTestCase(unittest.TestCase):
    def test_ignore_semantics(self):
        patterns = package.load_patterns()
        for excluded in ("tests/test_api.py", "scripts/deploy.sh",
                         "data/output/telemetry_observations.ndjson",
                         "data/en/menu.md", ".env", "secrets/vertex.json",
                         "docs/m1_25_gemini_review.md", ".venv/bin/python"):
            self.assertTrue(package.is_ignored(excluded, patterns), excluded)
        for shipped in ("app/main.py", "app/gemini_review.py", "static/app.js",
                        "requirements.txt", "Procfile",
                        "data/output/asset_mapping_snapshot.json",
                        "data/output/manifest.json", "docs/evidence_register.json",
                        "data/input/telemetry/distribution_transformer_history.json"):
            self.assertFalse(package.is_ignored(shipped, patterns), shipped)

    def test_forbidden_reason_classification(self):
        expected = {
            ".env": "secret",
            "secrets/vertex.json": "secret",
            "service-account.json": "credential",
            "app/credentials-prod.json": "credential",
            "backup.zip": "archive",
            "tests/test_api.py": "development",
            "docs/m1_25_gemini_review.md": "development",
            "data/en/menu.md": "development_data",
            "data/output/telemetry_observations.ndjson": "unexpected_data",
        }
        for rel, reason in expected.items():
            self.assertEqual(package.forbidden_reason(rel), reason, rel)
        for rel in package.OUTPUT_ALLOWLIST + ("app/main.py",):
            self.assertIsNone(package.forbidden_reason(rel), rel)

    def test_report_is_clean(self):
        report = package.build_report(check_snapshot=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["missing_required"], [])
        self.assertEqual(report["forbidden_included"], [])
        self.assertTrue(report["snapshot"]["checked"])
        self.assertEqual(report["snapshot"]["returncode"], 0)
        self.assertIn("app/gemini_review.py", report["files"])
        self.assertNotIn("scripts/deploy_demo.sh", report["files"])

    def test_json_cli_runs_and_is_ok(self):
        proc = subprocess.run(
            [sys.executable, "scripts/check_demo_package.py", "--json",
             "--check-snapshot"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertGreater(report["file_count"], 20)


class DeployScriptTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DEPLOY.read_text(encoding="utf-8")

    def test_has_guard_and_dry_run(self):
        self.assertIn("ALLOW_DEMO_DEPLOYMENT", self.source)
        self.assertIn("--dry-run", self.source)
        self.assertIn("check_demo_package.py --check-snapshot", self.source)

    def test_target_and_flags(self):
        self.assertIn("keen-phalanx-495303-j0", self.source)
        self.assertIn("asia-south1", self.source)
        self.assertIn("ENABLE_GEMINI_REVIEW=true", self.source)
        self.assertIn("ENABLE_GEMINI=false", self.source)
        self.assertIn("ENABLE_ASSET_MAPPING=false", self.source)
        self.assertIn("ENABLE_AGENT_DRAFT=true", self.source)
        self.assertIn("--no-allow-unauthenticated", self.source)
        self.assertIn('MODEL_PROFILE="${MODEL_PROFILE:-flash-only}"', self.source)
        self.assertIn("GEMINI_PRIMARY_MODEL=", self.source)
        self.assertIn("gemini-4-argon", self.source)
        self.assertIn("gemini-3.8-flash", self.source)
        self.assertIn("--set-secrets", self.source)
        self.assertIn("GEMINI_API_KEY=${GEMINI_API_KEY_SECRET}:latest", self.source)
        self.assertNotIn("BQ_TABLE=", self.source.split("Refusing")[0])

    def test_low_cost_scaling_and_private_flags(self):
        # Inspect only the deploy command, not the explanatory header comment.
        command = self.source.split("DEPLOY_CMD=(", 1)[1].split("--quiet", 1)[0]
        # Request-based CPU, scale to zero, service-level max 1, modest resources.
        self.assertIn("--min-instances 0", command)
        self.assertIn("--max 1", command)
        self.assertIn("--cpu 1", command)
        self.assertIn("--memory 512Mi", command)
        self.assertIn("--concurrency 8", command)
        self.assertIn("--cpu-boost", command)
        # Service-level --max, never the per-revision --max-instances.
        self.assertNotIn("--max-instances", command)
        # Never always-allocated CPU or a pinned warm instance.
        self.assertNotIn("--no-cpu-throttling", command)
        self.assertNotIn("--min-instances 1", command)
        self.assertNotIn("--min 1", command)
        # Private: never a bare allow-unauthenticated.
        self.assertIn("--no-allow-unauthenticated", command)
        self.assertNotIn(" --allow-unauthenticated", command)

    def test_deploy_command_pins_project(self):
        # The deploy command must pin the project explicitly instead of relying
        # on the active gcloud configuration; the dry run must display it.
        command = self.source.split("DEPLOY_CMD=(", 1)[1].split("--quiet", 1)[0]
        self.assertIn('--project "$PROJECT_ID"', command)
        self.assertNotIn("--project keen-phalanx-495303-j0", command)
        self.assertIn('PROJECT_ID="${PROJECT_ID:-keen-phalanx-495303-j0}"', self.source)

    def test_no_mutating_prerequisites(self):
        for forbidden in ("services enable", "add-iam-policy-binding",
                          "iam service-accounts create",
                          "--allow-unauthenticated",
                          "bq load", "storage cp", "storage buckets create",
                          "gcloud iap", "actAs"):
            self.assertNotIn(forbidden, self.source, forbidden)

    def test_refuses_preflight_gaps_instead_of_modifying(self):
        for phrase in ("Refusing: required APIs are not enabled",
                       "does not exist",
                       "roles/secretmanager.secretAccessor",
                       "no enabled version",
                       "will not"):
            self.assertIn(phrase, self.source, phrase)

    def test_dry_run_executes_with_no_gcloud(self):
        env = dict(os.environ, PYTHON=sys.executable)
        proc = subprocess.run(["bash", str(DEPLOY), "--dry-run"], cwd=ROOT,
                              capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Dry run complete", proc.stdout)
        self.assertIn("PACKAGE OK", proc.stdout)
        self.assertIn("No gcloud command was executed", proc.stdout)
        for flag in ("--min-instances 0", "--max 1", "--cpu 1", "--memory 512Mi",
                     "--concurrency 8", "--cpu-boost", "--no-allow-unauthenticated"):
            self.assertIn(flag, proc.stdout, flag)
        self.assertNotIn("--max-instances", proc.stdout)
        self.assertNotIn("gemini-4-argon", proc.stdout)
        # The project is pinned in the command and displayed by the dry run.
        self.assertIn("--project keen-phalanx-495303-j0", proc.stdout)
        self.assertIn("project:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
