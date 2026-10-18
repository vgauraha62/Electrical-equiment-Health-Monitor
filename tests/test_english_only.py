"""English-only boundary test (stdlib unittest).

Fails if CJK characters appear in active user-facing resources. Excludes the
immutable source archive and the provenance manifest (which must carry original
filenames for linkage). Unreviewed translations in data/en are English-only by
construction: they cite sources by locator, never by pasted source text.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")

SCAN_DIRS = ("app", "static", "schemas", "data/en")
SCAN_GLOBS = ("docs/*.md", "data/output/examples/*.json")
EXCLUDED = ("docs/english_source_manifest.json",
            "data/en/glossary_technical.json")
# Exclusions are instruments/provenance, never user-facing content: the manifest
# must carry original filenames for linkage, and the glossary must carry source
# fragments as translation keys. Both are documented in english_source_manifest.json.


class TestEnglishOnly(unittest.TestCase):
    def test_no_cjk_in_active_resources(self):
        offenders = []
        for dirname in SCAN_DIRS:
            for path in (ROOT / dirname).rglob("*"):
                if str(path.relative_to(ROOT)) in EXCLUDED:
                    continue
                if path.is_file() and path.suffix in {
                        ".py", ".html", ".js", ".css", ".json", ".md"}:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    if CJK.search(text):
                        offenders.append(str(path.relative_to(ROOT)))
        for pattern in SCAN_GLOBS:
            for path in ROOT.glob(pattern):
                if str(path.relative_to(ROOT)) in EXCLUDED:
                    continue
                if CJK.search(path.read_text(encoding="utf-8", errors="replace")):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_source_archive_excluded_and_present(self):
        archive = ROOT / "data/source_archive/originals"
        self.assertTrue(archive.is_dir())
        self.assertGreater(len(list(archive.iterdir())), 0)


if __name__ == "__main__":
    unittest.main()
