"""Tests pinning source-to-UI reading traces (stdlib unittest)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FEEDER_EXPECTED = {
    "A_phsA": ("10.000000", 10.0), "A_phsB": ("0.000000", 0.0),
    "A_phsC": ("53.000000", 53.0), "PhV_phsA": ("0.000000", 0.0),
    "PhV_phsB": ("0.000000", 0.0), "PhV_phsC": ("0.000000", 0.0),
    "TotW": ("53.000000", 53.0), "TotVar": ("0.000000", 0.0),
}
FEEDER_POINTS = {"A_phsC": "010100010004", "TotW": "010100030001"}


class ReadingTraceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.feeder = json.loads(
            (ROOT / "data/input/telemetry/feeder_snapshot.json").read_text(encoding="utf-8"))
        cls.breaker = json.loads(
            (ROOT / "data/input/telemetry/distribution_network_circuit_breaker_snapshot.json")
            .read_text(encoding="utf-8"))

    def test_raw_tokens_are_explicit_strings_no_nulls(self):
        for name, payload in (("feeder", self.feeder), ("breaker", self.breaker)):
            for record in payload["data"]["result"]["records"]:
                for point in record["telemetryValue"]:
                    self.assertIsInstance(point["measValue"], str, (name, record["measTypeCode"]))
                    self.assertNotIn(point["measValue"], (None, "", "null"))

    def test_feeder_53s_are_distinct_source_fields(self):
        by_code = {}
        for record in self.feeder["data"]["result"]["records"]:
            for point in record["telemetryValue"]:
                by_code[record["measTypeCode"]] = (record["measPointId"],
                                                   point["measValue"])
        for code, (token, number) in FEEDER_EXPECTED.items():
            self.assertEqual(by_code[code][1], token)
            self.assertEqual(float(token), number)
        # Same numeric value, different points: not a duplicated row.
        self.assertNotEqual(by_code["A_phsC"][0], by_code["TotW"][0])
        for code, suffix in FEEDER_POINTS.items():
            self.assertTrue(by_code[code][0].endswith(suffix), code)

    def test_breaker_zeros_are_explicit_not_missing(self):
        target = [r for r in self.breaker["data"]["result"]["records"]
                  if r["psrId"] == "182d2b68-8127-48c3-baa9-555e2532ca01"]
        self.assertEqual(len(target), 8)
        for record in target:
            for point in record["telemetryValue"]:
                self.assertEqual(point["measValue"], "0.000000")

    def test_name_code_mapping_is_one_to_one(self):
        import glob
        forward, backward = {}, {}
        for path in glob.glob(str(ROOT / "data/input/telemetry/*.json")):
            for record in json.load(open(path))["data"]["result"]["records"]:
                forward.setdefault(record["measTypeCode"], set()).add(record["measTypeName"])
                backward.setdefault(record["measTypeName"], set()).add(record["measTypeCode"])
        self.assertTrue(all(len(v) == 1 for v in forward.values()))
        self.assertTrue(all(len(v) == 1 for v in backward.values()))

    def test_null_renders_as_not_recorded(self):
        app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
        self.assertGreaterEqual(app_js.count("Not recorded"), 2)


if __name__ == "__main__":
    unittest.main()
