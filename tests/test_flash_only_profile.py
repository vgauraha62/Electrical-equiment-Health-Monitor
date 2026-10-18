"""Focused tests for the no-paid-Gemini `flash-only` deployment profile.

These run entirely offline: the model adapter is exercised with a fake client or
with ``_call_interactions`` patched, and the deployment script is run in its
package-only dry-run mode. No Gemini call, cloud call, build, or deploy happens.

They prove two things the paid profiles cannot:
1. `flash-only` selects only `gemini-3.8-flash` with the fallback disabled.
2. A Flash failure never reaches `gemini-4-argon`; the route returns the existing
   safe model-error response instead.
"""
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.main as main_module  # noqa: E402
import app.gemini_review as review  # noqa: E402

ASSET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"
DEPLOY = ROOT / "scripts" / "deploy_demo.sh"
FLASH = "gemini-3.8-flash"
ARGON = "gemini-4-argon"


class FlashOnlyRoutingTestCase(unittest.TestCase):
    def setUp(self):
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module._cache.update(at=0.0, rows=None)

    # --- environment selection ------------------------------------------------

    def test_env_resolves_flash_only_without_fallback(self):
        for sentinel in ("none", "NONE", "None", "", "disabled", "off"):
            primary, fallback = review.model_route_from_env(
                {"GEMINI_PRIMARY_MODEL": FLASH, "GEMINI_FALLBACK_MODEL": sentinel})
            self.assertEqual(primary, FLASH, sentinel)
            self.assertIsNone(fallback, sentinel)

    def test_env_keeps_explicit_paid_fallback(self):
        primary, fallback = review.model_route_from_env(
            {"GEMINI_PRIMARY_MODEL": ARGON, "GEMINI_FALLBACK_MODEL": FLASH})
        self.assertEqual((primary, fallback), (ARGON, FLASH))

    def test_env_rejects_equal_models_or_missing_primary(self):
        with self.assertRaises(review.ModelRouteError):
            review.model_route_from_env(
                {"GEMINI_PRIMARY_MODEL": FLASH, "GEMINI_FALLBACK_MODEL": FLASH})
        with self.assertRaises(review.ModelRouteError):
            review.model_route_from_env(
                {"GEMINI_PRIMARY_MODEL": "   ", "GEMINI_FALLBACK_MODEL": "none"})

    # --- no-fallback call path -------------------------------------------------

    def test_no_fallback_profile_never_calls_a_second_model(self):
        calls = []

        class _Interactions:
            @staticmethod
            def create(*, model, input, response_format, generation_config):
                calls.append(model)
                raise TimeoutError("flash unavailable")

        class _Client:
            interactions = _Interactions

        with self.assertRaises(review.ModelRouteError):
            review._generate_with_route(_Client, FLASH, None, "prompt")
        self.assertEqual(calls, [FLASH])
        self.assertNotIn(ARGON, calls)

    def test_no_fallback_profile_returns_output_on_success(self):
        calls = []

        class _Interactions:
            @staticmethod
            def create(*, model, input, response_format, generation_config):
                calls.append(model)
                return types.SimpleNamespace(output_text='{"ok":true}')

        class _Client:
            interactions = _Interactions

        result = review._generate_with_route(_Client, FLASH, None, "prompt")
        self.assertEqual(calls, [FLASH])
        self.assertEqual(result.model, FLASH)
        self.assertEqual(result.route, "primary")

    def _fake_google(self):
        module = types.ModuleType("google")
        module.genai = types.SimpleNamespace(Client=mock.Mock(return_value=object()))
        return mock.patch.dict(sys.modules, {"google": module})

    def test_default_generate_flash_only_failure_does_not_reach_argon(self):
        env = {"GEMINI_API_KEY": "k", "GEMINI_PRIMARY_MODEL": FLASH,
               "GEMINI_FALLBACK_MODEL": "none"}
        with mock.patch.dict(os.environ, env), self._fake_google(), \
                mock.patch.object(review, "_call_interactions",
                                  side_effect=TimeoutError()) as call:
            with self.assertRaises(review.ModelRouteError):
                review.default_generate("prompt")
        models = [c.args[1] for c in call.call_args_list]
        self.assertEqual(models, [FLASH])
        self.assertNotIn(ARGON, models)

    def test_explain_returns_safe_model_error_and_no_argon_call(self):
        env = {"GEMINI_API_KEY": "k", "GEMINI_PRIMARY_MODEL": FLASH,
               "GEMINI_FALLBACK_MODEL": "none"}
        with mock.patch.dict(os.environ, env), self._fake_google(), \
                mock.patch.object(review, "_call_interactions",
                                  side_effect=TimeoutError()) as call:
            result = review.explain(ASSET, generate=review.default_generate)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], "model_error")
        self.assertEqual(result["sections"]["supporting_points"], [])
        self.assertIsNone(result["direct_answer"])
        self.assertEqual(result["model"], FLASH)
        # A failed Flash call is reported as a failure, not as model output.
        self.assertFalse(result["model_generated"])
        self.assertFalse(result["explanation_accepted"])
        self.assertTrue(result["model_call"])
        self.assertEqual(result["model_route"], "failed")
        self.assertEqual(result["diagnostics"]["category"], "timeout")
        models = [c.args[1] for c in call.call_args_list]
        self.assertEqual(models, [FLASH])
        self.assertNotIn(ARGON, models)

    def test_paid_profile_still_uses_argon_as_configured(self):
        # Guard rail: the no-Argon guarantee belongs to flash-only, not to every
        # profile. An explicit paid profile must still be able to call Argon.
        env = {"GEMINI_API_KEY": "k", "GEMINI_PRIMARY_MODEL": ARGON,
               "GEMINI_FALLBACK_MODEL": FLASH}
        with mock.patch.dict(os.environ, env), self._fake_google(), \
                mock.patch.object(review, "_call_interactions",
                                  return_value='{"ok":true}') as call:
            result = review.default_generate("prompt")
        self.assertEqual([c.args[1] for c in call.call_args_list], [ARGON])
        self.assertEqual(result.model, ARGON)


class FlashOnlyDeployProfileTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DEPLOY.read_text(encoding="utf-8")

    def test_default_profile_is_flash_only(self):
        self.assertIn('MODEL_PROFILE="${MODEL_PROFILE:-flash-only}"', self.source)

    def test_flash_only_case_pins_flash_and_disables_fallback(self):
        self.assertIn("flash-only)", self.source)
        block = self.source.split("flash-only)", 1)[1].split(";;", 1)[0]
        self.assertIn('GEMINI_PRIMARY_MODEL="gemini-3.8-flash"', block)
        self.assertIn('GEMINI_FALLBACK_MODEL="none"', block)
        self.assertNotIn(ARGON, block)

    def test_paid_profiles_remain_explicit_choices(self):
        self.assertIn("argon-first)", self.source)
        self.assertIn("flash-first)", self.source)
        self.assertIn("Use flash-only, argon-first, or flash-first", self.source)

    def _dry_run(self, profile):
        env = dict(os.environ, PYTHON=sys.executable)
        if profile is None:
            env.pop("MODEL_PROFILE", None)
        else:
            env["MODEL_PROFILE"] = profile
        proc = subprocess.run(["bash", str(DEPLOY), "--dry-run"], cwd=ROOT,
                              capture_output=True, text=True, env=env, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_default_dry_run_uses_flash_only_and_never_names_argon(self):
        output = self._dry_run(None)
        self.assertIn("model profile:  flash-only", output)
        self.assertIn("GEMINI_PRIMARY_MODEL=gemini-3.8-flash", output)
        self.assertIn("GEMINI_FALLBACK_MODEL=none", output)
        self.assertNotIn(ARGON, output)

    def test_flash_only_dry_run_explicit(self):
        output = self._dry_run("flash-only")
        self.assertIn("GEMINI_PRIMARY_MODEL=gemini-3.8-flash", output)
        self.assertIn("GEMINI_FALLBACK_MODEL=none", output)
        self.assertNotIn(ARGON, output)

    def test_paid_profile_is_opt_in(self):
        output = self._dry_run("argon-first")
        self.assertIn("GEMINI_PRIMARY_MODEL=gemini-4-argon", output)

    def test_unknown_profile_is_refused(self):
        env = dict(os.environ, PYTHON=sys.executable, MODEL_PROFILE="free-lunch")
        proc = subprocess.run(["bash", str(DEPLOY), "--dry-run"], cwd=ROOT,
                              capture_output=True, text=True, env=env, timeout=180)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unsupported MODEL_PROFILE", proc.stderr)


if __name__ == "__main__":
    unittest.main()
