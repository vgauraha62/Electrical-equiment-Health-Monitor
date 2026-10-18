"""Real browser end-to-end validation (Firefox + geckodriver, stdlib HTTP driver).

No selenium/playwright installed or needed: this module speaks the WebDriver
protocol with urllib and runs the full operator workflow in headless Firefox
against a live Flask server with isolated temporary review storage.

Skips cleanly when the browser binaries are absent.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GECKO = shutil.which("geckodriver")
FIREFOX = shutil.which("firefox")
FLASK_PORT = 8137
DRIVER_PORT = 14447


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Driver:
    """Minimal synchronous WebDriver client over stdlib HTTP."""

    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        payload = json.dumps({"capabilities": {"alwaysMatch": {
            "browserName": "firefox",
            "moz:firefoxOptions": {"args": ["-headless"]}}}}).encode()
        response = self._call("POST", "/session", payload)
        self.session = response["value"]["sessionId"]

    def _call(self, method, path, payload=None):
        request = urllib.request.Request(
            self.base + path, data=payload, method=method,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())

    def navigate(self, url):
        self._call("POST", f"/session/{self.session}/url",
                   json.dumps({"url": url}).encode())

    def script(self, source):
        response = self._call(
            "POST", f"/session/{self.session}/execute/sync",
            json.dumps({"script": source, "args": []}).encode())
        return response["value"]

    def screenshot(self, path):
        response = self._call("GET", f"/session/{self.session}/screenshot")
        Path(path).write_bytes(base64.b64decode(response["value"]))

    def quit(self):
        try:
            self._call("DELETE", f"/session/{self.session}")
        except Exception:
            pass


def wait_until(driver, expression, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if driver.script(f"return !!({expression})"):
            return True
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for: {expression}")


@unittest.skipUnless(GECKO and FIREFOX, "firefox/geckodriver unavailable")
class BrowserWorkflowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="browser_cases_")
        cls.mapping_tmp = tempfile.mkdtemp(prefix="browser_mappings_")
        cls.shots = Path("/tmp/opencode/browser_shots")
        cls.shots.mkdir(parents=True, exist_ok=True)
        os.environ["REVIEW_CASES_DIR"] = cls.tmp
        os.environ["ASSET_MAPPING_DIR"] = cls.mapping_tmp
        sys_path = str(ROOT)
        env = dict(os.environ, PYTHONPATH=sys_path)
        cls.flask_port = free_port()
        cls.server = subprocess.Popen(
            [sys.executable, "app/main.py"], cwd=ROOT, env={
                **env, "PORT": str(cls.flask_port), "REVIEW_CASES_DIR": cls.tmp,
                "ASSET_MAPPING_DIR": cls.mapping_tmp},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.flask_port}/api/health", timeout=5)
                break
            except Exception:
                time.sleep(0.5)
        cls.driver_port = free_port()
        cls.gecko = subprocess.Popen(
            ["geckodriver", "--port", str(cls.driver_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        cls.driver = Driver(cls.driver_port)
        cls.base = f"http://127.0.0.1:{cls.flask_port}"

    @classmethod
    def tearDownClass(cls):
        try:
            cls.driver.quit()
        finally:
            cls.gecko.terminate()
            cls.server.terminate()
            for process in (cls.gecko, cls.server):
                try:
                    process.wait(timeout=10)
                except Exception:
                    process.kill()
        os.environ.pop("REVIEW_CASES_DIR", None)
        os.environ.pop("ASSET_MAPPING_DIR", None)

    def test_full_operator_workflow(self):
        driver = self.driver
        driver.navigate(self.base + "/")
        wait_until(driver, "document.querySelector('#asset-list .asset-button')")
        driver.screenshot(str(self.shots / "01_assets.png"))

        # 2-3. Locate target asset, open it, generate monitoring.
        driver.script(
            "const search=document.querySelector('#search');search.value='042983E8';"
            "search.dispatchEvent(new Event('input'))")
        wait_until(driver, "document.querySelectorAll('#asset-list .asset-button').length===1")
        driver.script(
            "document.querySelector('#asset-list .asset-button').click()")
        wait_until(driver, "!document.querySelector('#asset-detail').hidden")
        driver.script("document.querySelector('#monitoring-button').click()")
        wait_until(driver, "!document.querySelector('#monitoring-result').hidden")
        metrics = driver.script(
            "return document.querySelector('#monitoring-metrics').innerText")
        self.assertIn("human_review", metrics)
        self.assertIn("not_assessed", metrics)
        self.assertIn("1372", metrics)
        codes = driver.script(
            "return document.querySelectorAll('#monitoring-codes tbody tr').length")
        self.assertEqual(codes, 14)
        unsupported = driver.script(
            "return document.querySelector('#monitoring-unsupported').innerText")
        self.assertIn("failure_probability", unsupported)
        self.assertIn("blocked", unsupported)
        # Mock the Gemini route in-browser: both controls use the same
        # context-grounded endpoint, and a successful response is reused for
        # this asset instead of making a duplicate model request.
        patch_result = driver.script("""
          try {
            window.GEMINI_REVIEW_ENABLED = true;
            window.__geminiCalls = 0;
            window.__realFetch = window.fetch.bind(window);
            window.fetch = async (url, options) => {
              if (String(url).includes('/gemini-review')) {
                window.__geminiCalls++;
                return {ok: true, json: async () => ({
                  status: 'explained', model_generated: true,
                  explanation_accepted: true,
                  model: 'gemini-3.8-flash', model_route: 'primary',
                  reason_code: null, reason_message: null,
                  no_approved_knowledge: true, no_relevant_reference: true,
                  knowledge_state: {},
                  direct_answer: {text: 'No approved engineering reference applies; the missing evidence is the unit/scaling documentation and the timestamp timezone.',
                    citations: [], quotes: []},
                  supporting_points: [],
                  supported_context: {cited_claims: [], link: {
                    status: 'linked', matching_method: 'exact_id_string',
                    verification_status: 'id_string_only', caveat: 'ID match only'
                  }}, sections: {supporting_points: [], unknowns: [],
                    human_checks: []}, omitted: []
                })};
              }
              return window.__realFetch(url, options);
            };
            mountGeminiReview('042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294',
              document.querySelector('#asset-detail'));
            return 'mock installed';
          } catch (e) { return 'mock setup failed: ' + e + ' ' + e.stack; }
        """)
        self.assertEqual(patch_result, "mock installed")
        driver.script("document.querySelector('#gemini-review-button').click()")
        wait_until(driver, "document.querySelector('#gemini-model-review')")
        model_status = driver.script(
            "return document.querySelector('#gemini-review-status').innerText")
        self.assertIn("generated by gemini-3.8-flash", model_status)
        direct = driver.script(
            "return document.querySelector('#gemini-direct-answer').innerText")
        self.assertIn("No approved engineering reference applies", direct)
        driver.script("document.querySelector('#monitoring-button').click()")
        wait_until(driver,
                   "document.querySelector('#monitoring-model-status').innerText.includes('shown in AI summary preview')")
        self.assertEqual(driver.script("return window.__geminiCalls"), 1)
        driver.screenshot(str(self.shots / "02_monitoring.png"))

        # 5-6. Create case; duplicate click replays the same case.
        driver.script("document.querySelector('#case-button').click()")
        wait_until(driver, "document.querySelector('#monitoring-case-line').innerText")
        line = driver.script(
            "return document.querySelector('#monitoring-case-line').innerText")
        self.assertIn("New case:", line)
        case_id = line.split("New case:")[1].split("·")[0].strip()
        self.assertTrue(case_id)
        driver.script("document.querySelector('#case-button').click()")
        wait_until(driver,
                   "document.querySelector('#monitoring-case-line').innerText.includes('duplicate')")
        again = driver.script(
            "return document.querySelector('#monitoring-case-line').innerText")
        self.assertIn(case_id, again)

        # 7-8. Queue shows the case; open it; enter review; hostile note is escaped.
        wait_until(driver, "document.querySelector('#queue-list [data-case]')")
        driver.script(
            f"document.querySelector('#queue-list [data-case=\"{case_id}\"]').click()")
        wait_until(driver, "!document.querySelector('#case-detail').hidden")
        driver.script(
            "document.querySelector('#case-actions [data-t=\"in_review\"]').click()")
        wait_until(driver,
                   "document.querySelector('#case-status').innerText==='in_review'")
        driver.script(
            "document.querySelector('#note-text').value='<b>probe</b>';"
            "document.querySelector('#note-reviewer').value='operator';"
            "document.querySelector('#note-button').click()")
        wait_until(driver,
                   "document.querySelector('#case-history').innerText.includes('&lt;b&gt;') "
                   "|| document.querySelector('#case-history').innerText.includes('<b>probe</b>')")
        history_html = driver.script(
            "return document.querySelector('#case-history').innerHTML")
        self.assertNotIn("<b>probe</b>", history_html)
        self.assertIn("&lt;b&gt;probe&lt;/b&gt;", history_html)
        driver.screenshot(str(self.shots / "03_case.png"))

        # 9-11. Evidence required, resume, complete, terminal state.
        driver.script(
            "document.querySelector('#case-actions [data-t=\"evidence_required\"]').click()")
        wait_until(driver,
                   "document.querySelector('#case-status').innerText==='evidence_required'")
        driver.script(
            "document.querySelector('#case-actions [data-t=\"in_review\"]').click()")
        wait_until(driver,
                   "document.querySelector('#case-status').innerText==='in_review'")
        driver.script(
            "document.querySelector('#case-actions [data-t=\"completed\"]').click()")
        wait_until(driver,
                   "document.querySelector('#case-status').innerText==='completed'")
        wait_until(driver,
                   "document.querySelector('#case-actions').innerText.includes('Terminal')")
        events = driver.script(
            "return document.querySelectorAll('#case-history p').length")
        self.assertGreaterEqual(events, 5)

        # 12. Refresh: case persists.
        driver.navigate(self.base + "/")
        wait_until(driver, "document.querySelector('#queue-list [data-case]')")
        count = driver.script(
            "return document.querySelectorAll('#queue-list [data-case]').length")
        self.assertGreaterEqual(count, 1)

        # 13. Stale-version conflict recovers without overwriting.
        conflict = driver.script(
            f"return fetch('/api/review-cases/{case_id}/transitions', {{"
            "method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({from_version:1,to_status:'in_review'})})"
            ".then(r=>r.json().then(j=>({status:r.status,error:j.error})))")
        self.assertIn(conflict["status"], (409, 422))
        self.assertTrue(conflict["error"])
        state = driver.script(
            f"return fetch('/api/review-cases/{case_id}').then(r=>r.json())"
            ".then(j=>({status:j.status,version:j.version,history:j.history.length}))")
        self.assertEqual(state["status"], "completed")
        self.assertGreaterEqual(state["history"], events)
        driver.screenshot(str(self.shots / "04_completed.png"))

    def test_directory_detail_in_main_panel(self):
        driver = self.driver
        driver.navigate(self.base + "/")
        wait_until(driver, "document.querySelectorAll('#asset-list .asset-button').length")

        def search(text):
            driver.script(
                "const s=document.querySelector('#search');s.value="
                + json.dumps(text) +
                ";s.dispatchEvent(new Event('input'))")
            time.sleep(2)

        def detail_location():
            return driver.script(
                "const d=document.querySelector('#directory-detail');"
                "const panel=document.querySelector('.detail-panel');"
                "const sidebar=document.querySelector('.asset-panel');"
                "return {visible:!d.hidden, inPanel:panel.contains(d), "
                "inSidebar:sidebar.contains(d)}")

        # Telemetry-only record: readings + chart in the main panel.
        search("28DKX-3681")
        wait_until(driver, "document.querySelectorAll('#asset-list .asset-button').length===1")
        driver.script("document.querySelector('#asset-list .asset-button').click()")
        wait_until(driver, "!document.querySelector('#directory-detail').hidden")
        location = detail_location()
        self.assertTrue(location["visible"])
        self.assertTrue(location["inPanel"])
        self.assertFalse(location["inSidebar"])
        text = driver.script("return document.querySelector('#directory-detail').innerText")
        self.assertIn("28DKX-3681", text)
        self.assertIn("readings", text)
        circles = driver.script(
            "return [...document.querySelectorAll('#directory-detail .stat-circle')].map(e=>e.innerText)")
        self.assertIn("8", circles)
        self.assertIn("Single shared snapshot timestamp: 2026-08-25 16:35:00", text)
        self.assertIsNone(driver.script("return document.querySelector('#dir-code-select')"))
        self.assertIsNone(driver.script("return document.querySelector('#dir-chart svg')"))
        rows = driver.script(
            "return document.querySelectorAll('#directory-detail table tbody tr').length")
        self.assertEqual(rows, 8)
        self.assertIsNone(driver.script("return document.querySelector('#dir-code-select')"))
        self.assertIn("Single shared snapshot timestamp", text)
        rows = driver.script(
            "return document.querySelectorAll('#directory-detail table tbody tr').length")
        self.assertEqual(rows, 8)
        self.assertIn("telemetry only", driver.script(
            "return document.querySelector('#directory-detail').innerText").lower())
        driver.screenshot(str(self.shots / "05_directory_telemetry.png"))

        # Network-breaker snapshot: all eight codes, no dropdown, no chart.
        search("182d2b68")
        wait_until(driver, "document.querySelectorAll('#asset-list .asset-button').length===1")
        driver.script("document.querySelector('#asset-list .asset-button').click()")
        wait_until(driver, "document.querySelector('#directory-detail').innerText.includes('182d2b68')")
        text = driver.script("return document.querySelector('#directory-detail').innerText")
        rows = driver.script(
            "return document.querySelectorAll('#directory-detail table tbody tr').length")
        self.assertEqual(rows, 8)
        self.assertIsNone(driver.script("return document.querySelector('#dir-code-select')"))
        self.assertIsNone(driver.script("return document.querySelector('#dir-chart svg')"))

        # Multi-point telemetry record: chart renders.
        search("3812ac8d")
        wait_until(driver, "document.querySelectorAll('#asset-list .asset-button').length===1")
        driver.script("document.querySelector('#asset-list .asset-button').click()")
        wait_until(driver, "document.querySelector('#directory-detail').innerText.includes('3812ac8d')")
        self.assertTrue(driver.script("return !!document.querySelector('#dir-chart svg')"))
        ticks = driver.script(
            "return [...document.querySelectorAll('#dir-chart svg text')].map(t=>t.textContent)")
        self.assertGreaterEqual(len(ticks), 3)
        self.assertTrue(all(len(t) <= 12 for t in ticks))
        summary = driver.script("return document.querySelector('#dir-summary').innerText")
        self.assertIn("observations", summary)
        self.assertIn("min", summary)
        self.assertIn("max", summary)
        pages = driver.script("return document.querySelector('#dir-table').innerText")
        self.assertIn("page 1 of", pages)
        table_rows = driver.script(
            "return document.querySelectorAll('#dir-table tbody tr').length")
        self.assertLessEqual(table_rows, 25)
        self.assertGreater(table_rows, 0)
        # Constant-zero series renders flat with a neutral note.
        driver.script(
            "const sel=document.querySelector('#dir-code-select');"
            "sel.value='TotVar';sel.dispatchEvent(new Event('change'))")
        time.sleep(2)
        zero_summary = driver.script("return document.querySelector('#dir-summary').innerText")
        self.assertIn("All recorded values are 0", zero_summary)
        self.assertNotIn("missing", driver.script(
            "return document.querySelector('#directory-detail').innerText").lower())
        self.assertNotIn("fault", driver.script(
            "return document.querySelector('#directory-detail').innerText").lower())

        # Matched transformer: ledger facts plus the exact-ID caveat.
        search("042983E8")
        wait_until(driver, "document.querySelectorAll('#asset-list .asset-button').length===1")
        driver.script("document.querySelector('#asset-list .asset-button').click()")
        wait_until(driver, "document.querySelector('#ledger-facts')")
        facts = driver.script("return document.querySelector('#ledger-facts').innerText")
        self.assertIn("oil-immersed", facts)
        self.assertIn("shared IDs are not proof of the same physical asset", facts)

        # Ledger-only records are not reachable through any frontend control:
        # the coverage select offers telemetry-backed options only.
        options = driver.script(
            "return [...document.querySelectorAll('#dir-coverage option')].map(o=>o.value)")
        self.assertEqual(sorted(options), ["", "both", "telemetry only"])
        driver.script(
            "document.querySelector('#dir-coverage').value='ledger only';"
            "document.querySelector('#dir-coverage').dispatchEvent(new Event('change'))")
        time.sleep(2)
        visible = driver.script(
            "return [...document.querySelectorAll('#asset-list .asset-button')].length")
        self.assertGreater(visible, 0)
        badges = driver.script(
            "return [...document.querySelectorAll('#asset-list .asset-button')].map(b=>b.innerText)")
        self.assertTrue(all("ledger only" not in b for b in badges))

    def test_coverage_evidence_profile(self):
        driver = self.driver
        driver.navigate(self.base + "/#coverage")
        wait_until(driver, "document.querySelector('#lineage-asset')")
        driver.script(
            "const i=document.querySelector('#lineage-asset');"
            "i.value='042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294';"
            "document.querySelector('#lineage-load').click()")
        wait_until(driver, "document.querySelector('#evidence-profile')")
        text = driver.script("return document.querySelector('#evidence-profile').innerText")
        # The nine recorded ledger fields with their reviewed translation.
        rows = driver.script(
            "return document.querySelectorAll('#evidence-context tbody tr').length")
        self.assertEqual(rows, 11)
        self.assertIn("oil-immersed", text)
        self.assertIn("not in source", text)
        # The exact-ID link keeps both endpoints and the physical-identity caveat.
        link = driver.script("return document.querySelector('#evidence-link').innerText")
        self.assertIn("exact_id_string", link)
        self.assertIn("id_string_only", link)
        caveat = driver.script(
            "return document.querySelector('#evidence-link-caveat').innerText")
        self.assertIn("does not prove physical identity", caveat)
        # Unavailable material and unresolved conclusions are shown honestly.
        unsupported = driver.script(
            "return document.querySelector('#evidence-unsupported').innerText")
        self.assertIn("defect_history", unsupported)
        self.assertIn("test_record", unsupported)
        self.assertIn("maintenance_work_order", unsupported)
        unresolved = driver.script(
            "return document.querySelector('#evidence-unresolved').innerText")
        self.assertIn("physical_asset_identity", unresolved)
        self.assertIn("units", unresolved)
        self.assertIn("outcome_linkage", unresolved)
        # No diagnosis/risk/maintenance conclusion is presented.
        lowered = text.lower()
        for forbidden in ("fault", "failure probability", "remaining life",
                          "predictive maintenance"):
            self.assertNotIn(forbidden, lowered)
        driver.screenshot(str(self.shots / "06_evidence_profile.png"))

    def test_coverage_evidence_review(self):
        driver = self.driver
        driver.navigate(self.base + "/#coverage")
        wait_until(driver, "document.querySelector('#lineage-asset')")
        driver.script(
            "const i=document.querySelector('#lineage-asset');"
            "i.value='042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294';"
            "document.querySelector('#lineage-load').click()")
        wait_until(driver, "document.querySelector('#evidence-review')")
        # The supported facts and calculations are listed with their citations.
        supported = driver.script(
            "return document.querySelector('#review-supported').innerText")
        self.assertIn("reproducible", supported)
        self.assertIn("unconfirmed", supported)
        claims = driver.script(
            "return document.querySelectorAll('#review-claims tbody tr').length")
        self.assertGreater(claims, 0)
        # No approved reference exists: the review states it plainly and stays useful.
        references = driver.script(
            "return document.querySelector('#review-references').innerText")
        self.assertIn("No approved relevant knowledge", references)
        # Open questions limit only the dependent conclusions.
        limitations = driver.script(
            "return document.querySelector('#review-limitations').innerText")
        self.assertIn("units", limitations)
        self.assertIn("time_semantics", limitations)
        checks = driver.script(
            "return document.querySelector('#review-checks').innerText")
        self.assertIn("non-operational", checks)
        # No diagnosis/risk/maintenance conclusion is presented anywhere.
        lowered = driver.script(
            "return document.querySelector('#evidence-review').innerText").lower()
        for forbidden in ("fault", "failure probability", "remaining life",
                          "predictive maintenance"):
            self.assertNotIn(forbidden, lowered)
        driver.screenshot(str(self.shots / "07_evidence_review.png"))

    def test_coverage_asset_mapping(self):
        driver = self.driver
        driver.navigate(
            self.base + "/?asset=042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294")
        wait_until(
            driver,
            "document.querySelector('.asset-mapping') && "
            "/Exact-ID link/.test(document.querySelector('.asset-mapping').innerText)")
        text = driver.script(
            "return document.querySelector('.asset-mapping').innerText")
        self.assertIn("Linked source records", text)
        # Exact-ID links stay labelled as exact-ID, not as manual assignments.
        self.assertIn("Exact-ID link", text)
        self.assertIn("exact_id_string_match", text)
        self.assertIn("does not prove physical identity", text)
        # Manual assignment is off in this runtime, and the panel says so plainly.
        self.assertIn("Manual assignment is disabled in this deployment", text)
        # Unavailable source families are reported, not fabricated.
        unavailable = driver.script(
            "return document.querySelector('#mapping-unavailable').innerText")
        self.assertIn("Test records", unavailable)
        self.assertIn("unavailable", unavailable.lower())
        lowered = text.lower()
        for forbidden in ("fault", "failure probability", "remaining life",
                          "predictive maintenance"):
            self.assertNotIn(forbidden, lowered)
        driver.screenshot(str(self.shots / "08_asset_mapping.png"))


if __name__ == "__main__":
    unittest.main()
