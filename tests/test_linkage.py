"""Tests for source linkage and duplication (stdlib unittest)."""
import hashlib
import json
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AUTHORITATIVE = Path("/home/vg/my-projects/ai-builder-cup/programs/data/相关资料数据")
ARCHIVE = next((ROOT / "data/source_archive/originals").glob("*.zip"))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LinkageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(ARCHIVE) as z:
            cls.members = {name.split("/")[-1]: z.read(name) for name in z.namelist()
                           if name.endswith(".txt")}

    def test_authoritative_files_duplicate_archive(self):
        checked = 0
        for path in sorted(AUTHORITATIVE.rglob("*.txt")):
            digest = sha256_file(path)
            match = [blob for name, blob in self.members.items()
                     if name == path.name]
            self.assertEqual(len(match), 1, path.name)
            self.assertEqual(hashlib.sha256(match[0]).hexdigest(), digest,
                             path.name)
            checked += 1
        self.assertEqual(checked, 8)

    def test_workspace_inputs_match_raw_points(self):
        raw = json.loads(self.members["0408001-配电变压器历史.txt"].decode("utf-8"))
        local = json.loads((ROOT / "data/input/telemetry/distribution_transformer_history.json")
                           .read_text(encoding="utf-8"))
        raw_records = raw["data"]["result"]["records"]
        local_records = local["data"]["result"]["records"]
        self.assertEqual(len(raw_records), len(local_records))
        for raw_record, local_record in zip(raw_records, local_records):
            self.assertEqual(raw_record["telemetryValue"],
                             local_record["telemetryValue"])
            self.assertEqual(raw_record["psrId"], local_record["psrId"])

    def test_candidate_link_count_bounds(self):
        canonical = [json.loads(line) for line in
                     (ROOT / "data/output/canonical_measurements.ndjson")
                     .read_text(encoding="utf-8").splitlines() if line.strip()]
        tele_ids = {r["asset_id"] for r in canonical}
        # The single documented candidate link resolves in canonical data.
        self.assertIn("28DKX-3681", tele_ids)
        self.assertIn("042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294", tele_ids)
        # Target asset has no defect-container identity beyond itself here.
        self.assertEqual(len(tele_ids), 13)

    def test_ambiguous_codes_undecoded(self):
        # container_type and insulation codebooks are not in the repository;
        # tests pin the observed values so silent reinterpretation is caught.
        self.assertEqual(
            {r["measurement_type_code"] for r in
             [json.loads(line) for line in
              (ROOT / "data/output/canonical_measurements.ndjson")
              .read_text(encoding="utf-8").splitlines() if line.strip()
              and json.loads(line)["asset_id"] == "28DKX-3681"]},
            {"A_phsA", "A_phsB", "A_phsC", "PhV_phsA", "PhV_phsB",
             "PhV_phsC", "TotW", "TotVar"})


if __name__ == "__main__":
    unittest.main()
