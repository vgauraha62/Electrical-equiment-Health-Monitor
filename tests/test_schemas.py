"""Schema validation tests (stdlib unittest)."""
import json
import unittest
from pathlib import Path

from app.validate import ValidationError, validate

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas" / "v1"
FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


class TestSchemas(unittest.TestCase):
    def test_valid_asset(self):
        validate(json.loads((FIXTURES / "valid_asset.json").read_text()), load("asset.schema.json"))

    def test_valid_rule_prediction_recommendation(self):
        validate(json.loads((FIXTURES / "valid_rule.json").read_text()), load("diagnostic_rule.schema.json"))
        validate(json.loads((FIXTURES / "valid_prediction.json").read_text()), load("prediction.schema.json"))
        validate(json.loads((FIXTURES / "valid_recommendation.json").read_text()), load("recommendation.schema.json"))

    def test_valid_synthetic_measurement(self):
        validate(
            json.loads((FIXTURES / "synthetic" / "valid_measurement_synthetic.json").read_text()),
            load("measurement.schema.json"))

    def test_invalid_measurement_fails(self):
        with self.assertRaises(ValidationError):
            validate(json.loads((FIXTURES / "invalid_measurement.json").read_text()),
                      load("measurement.schema.json"))

    def test_prediction_forbids_failure_probability(self):
        schema = load("prediction.schema.json")
        bad = json.loads((FIXTURES / "valid_prediction.json").read_text())
        bad["failure_probability"] = 0.9
        with self.assertRaises(ValidationError):
            validate(bad, schema)

    def test_missing_required_fails(self):
        with self.assertRaises(ValidationError):
            validate({"asset_id": "x"}, load("measurement.schema.json"))

    def test_dga_absent_is_valid_state(self):
        # No DGA data in exports: unknown/empty record must validate, fabricated gases must not be required.
        validate({"asset_id": "a", "data_origin": "observed",
                  "schema_version": "1.0.0", "source_ref": {}, "quality_status": "unknown"},
                 load("dga_observation.schema.json"))


if __name__ == "__main__":
    unittest.main()
