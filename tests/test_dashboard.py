"""Dashboard static-contract tests (stdlib unittest, no browser needed).

Verifies element IDs, safety wording, absence of positive health/prediction
claims, and that every API path referenced by app.js exists in the Flask app.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REQUIRED_IDS = (
    "monitoring-button", "case-button", "monitoring-error", "monitoring-result",
    "monitoring-metrics", "monitoring-codes", "monitoring-candidates",
    "monitoring-model-status",
    "monitoring-unknowns", "monitoring-unsupported", "monitoring-case-line",
    "queue-count", "queue-asset", "queue-status", "queue-reload", "queue-error",
    "queue-list", "case-detail", "case-title", "case-meta", "case-status",
    "case-monitoring", "note-text", "note-reviewer", "note-button",
    "case-actions", "case-history", "case-error", "dir-sort", "dir-type",
    "dir-coverage", "dir-more", "directory-detail", "shared-view-note",
)
REQUIRED_SAFETY_PHRASES = (
    "descriptive monitoring, not transformer diagnosis",
    "never that equipment is healthy or faulty",
    "self-asserted",
    "Explicitly unsupported conclusions",
)
class DashboardTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "static/index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "static/app.js").read_text(encoding="utf-8")

    def test_element_ids_present(self):
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        for required in REQUIRED_IDS:
            self.assertIn(required, ids)
            self.assertIn(required, self.js)

    def test_professional_product_branding(self):
        self.assertIn("Evidence-Grounded Electrical Equipment Review", self.html)
        self.assertIn("Equipment health monitoring \u2014 the smart way", self.html)
        self.assertIn("Distribution Asset Review", self.html)
        combined = self.html + self.js
        self.assertNotIn("Gridline", combined)
        self.assertNotIn("Bundled sample data", combined)

    def test_shared_identity_in_header_and_tab_title(self):
        heading = "Evidence-Grounded Electrical Equipment Review"
        subtitle = "Equipment health monitoring \u2014 the smart way"
        title = re.search(r"<title>(.*?)</title>", self.html, re.S).group(1)
        self.assertIn(heading, title)
        self.assertIn(subtitle, title)
        # The shared heading/subtitle stay constant; screens no longer overwrite them.
        self.assertNotIn("page-title", self.js)
        self.assertIn("page-context", self.js)

    def test_development_facing_labels_removed(self):
        combined = self.html + self.js
        self.assertNotIn("Demo dataset", combined)
        self.assertNotIn("Demonstration environment", combined)
        self.assertNotIn("Equipment Health Review", combined)
        # A truthful, non-production source label remains.
        self.assertIn("Supplied equipment records", self.js)
        self.assertNotIn("Sample dataset", combined)
        self.assertIn("Read-only evidence workspace", self.html)

    def test_safety_wording(self):
        for phrase in REQUIRED_SAFETY_PHRASES:
            self.assertIn(phrase, self.html)
        lowered = (self.html + self.js).lower()
        chunks = re.split(r"[.!?;…\n]+", lowered)
        keywords = ("healthy", "faulty", "diagnosed", "health score",
                    "will fail", "failure probability")
        negations = ("never", "not ", "n't", "no ", "unverified",
                     "review only", "review candidate", "blocked", "unknown")
        for chunk in chunks:
            if any(keyword in chunk for keyword in keywords):
                self.assertTrue(any(neg in chunk for neg in negations),
                                f"positive health/prediction claim without negation: {chunk[:120]}")

    def test_workspace_navigation(self):
        screens = set(re.findall(r'data-screen="([^"]+)"', self.html))
        for required in ("overview", "equipment", "reviews", "evidence",
                         "reference"):
            self.assertIn(required, screens)
        navs = set(re.findall(r'data-nav="([^"]+)"', self.html))
        for required in ("overview", "equipment", "reviews", "evidence",
                         "reference"):
            self.assertIn(required, navs)
        # Navigation is hash-driven; an empty or unknown hash falls back to overview.
        self.assertIn("function screenFromHash()", self.js)
        self.assertIn("showScreen(screenFromHash())", self.js)
        self.assertIn("'hashchange'", self.js)
        self.assertIn("location.hash", self.js)
        self.assertIn(":'overview'", self.js)
        self.assertIn("ephemeral-notice", self.html)
        self.assertIn("may disappear on restart", self.html)

    def test_detail_improvement_strings(self):
        for phrase in ("shared IDs are not proof of the same physical asset",
                       "both · exact ID match",
                       "Single observation — no trend to display",
                       "Not in source",
                       "meanings are unverified",
                       "Equipment profile",
                       "No exact-ID ledger match",
                       "sources matched",
                       "Measurement (code)"):
            self.assertIn(phrase, self.js)

    def test_preview_screens_stay_disconnected(self):
        for screen in ("agent", "knowledge", "reports"):
            self.assertIn(f'data-screen="{screen}"', self.html)
            self.assertIn("NOT CONNECTED", self.html)
        self.assertIn('data-screen="chat"', self.html)
        self.assertIn("AI Smart Assistant", self.html)
        self.assertIn("assistant-form", self.html)
        for label in ("Open configuration", "Choose source", "Create report"):
            self.assertIn(label, self.html)
        fetch_calls = re.findall(r"fetch\((['\"])(/api/[^'\"`+?]+)\1", self.js)
        preview_paths = [p for _, p in fetch_calls
                         if any(k in p for k in ("report", "chat", "upload"))]
        self.assertEqual(preview_paths, [])
        self.assertIn("/api/agent/context/", self.js)
        self.assertIn("/api/agent/draft/", self.js)

    def test_states_and_conflict_handling(self):
        for marker in ("Loading", "No review cases yet", "Terminal state",
                       "nothing overwritten", "replayed", "Existing case",
                       "duplicate prevented", "idempotency_key"):
            self.assertIn(marker, self.js)
        self.assertIn("b.disabled=true", self.js)

    def test_single_review_path_no_monitoring_nav(self):
        self.assertNotIn('data-nav="monitoring"', self.html)
        for destination in ("overview", "equipment", "reviews", "evidence", "reference"):
            self.assertIn(f'data-nav="{destination}"', self.html)
        self.assertNotIn("All incl. ledger-only", self.html)
        self.assertNotIn('value="ledger only"', self.html.split('id="dir-coverage"')[1].split("</select>")[0])
        self.assertIn("Telemetry-backed equipment", self.html)

    def test_unified_detail_strings(self):
        for phrase in ("Equipment profile", "Observed readings",
                       "No exact-ID ledger match for this equipment",
                       "sources matched", "Shared view loaded",
                       "Measurement (code)"):
            self.assertIn(phrase, self.js)

    def test_directory_modes_referenced(self):
        for marker in ("dir-summary", "dir-table", "Single shared snapshot",
                       "shortTick", "dir-prev", "dir-next",
                       "Single observation — no trend to display",
                       "All recorded values are 0"):
            self.assertIn(marker, self.js)

    def test_shared_asset_url_state(self):
        for marker in ("location.search", "assetLoaded", "Shared view loaded",
                       "shared asset unavailable", "shared-view-note"):
            self.assertIn(marker, self.js)
        self.assertIn("/api/equipment?per_page=1", self.js)
        self.assertNotIn("fetch('/api/assets')", self.js)
        self.assertIn("/api/assets/'+", self.js.replace('"', "'"))

    def test_js_block_structure(self):
        import re as regex_module
        tries = len(regex_module.findall(r"\btry\s*\{", self.js))
        catches = len(regex_module.findall(r"\}\s*catch\s*\(", self.js))
        self.assertGreater(tries, 0)
        self.assertEqual(tries, catches)
        self.assertEqual(self.js.count("`") % 2, 0)

    def test_js_api_paths_exist_in_flask(self):
        import app.main as main_module
        rules = []
        for rule in main_module.app.url_map.iter_rules():
            pattern = re.sub(r"<[^>]+>", "[^/]+", str(rule.rule)) + "$"
            rules.append(re.compile(pattern))
        literals = set()
        for match in re.finditer(r"fetch\((['\"])(/api/[^'\"`+?]+)\1", self.js):
            if not match.group(2).endswith("/"):
                literals.add(match.group(2))
        dynamic = set()
        for match in re.finditer(r"fetch\((['\"])(/api/[^'\"`]+)\1\s*\+", self.js):
            prefix = match.group(2).split("?")[0]
            if prefix.endswith("/"):
                dynamic.add(prefix)
        self.assertGreater(len(literals) + len(dynamic), 0)
        for path in sorted(literals):
            self.assertTrue(any(r.match(path) for r in rules),
                            f"no Flask route for {path}")
        for prefix in sorted(dynamic):
            probe = prefix + "probe-id" + ("/notes" if prefix.endswith("/") else "")
            candidates = [prefix + "probe-id",
                          prefix + "probe-id/notes",
                          prefix + "probe-id/transitions",
                          prefix + "probe-id/evidence"]
            self.assertTrue(
                any(any(r.match(c) for r in rules) for c in candidates),
                f"no Flask route for dynamic path {prefix}")


if __name__ == "__main__":
    unittest.main()
