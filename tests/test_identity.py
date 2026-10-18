"""Tests for identifier normalization and join boundaries (stdlib unittest)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_telemetry_sources import normalize_asset_id  # noqa: E402


def join(left, right):
    """Exact-match join on normalized IDs; None never joins."""
    right_index = {}
    for record in right:
        key = normalize_asset_id(record.get("id"))
        if key is not None:
            right_index.setdefault(key, []).append(record)
    matches, unmatched = [], []
    for record in left:
        key = normalize_asset_id(record.get("id"))
        hits = right_index.get(key, []) if key is not None else []
        if hits:
            matches.append((record, hits))
        else:
            unmatched.append(record)
    return matches, unmatched


class IdentityTestCase(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize_asset_id("  28DKX-3681  "), "28DKX-3681")
        self.assertIsNone(normalize_asset_id(""))
        self.assertIsNone(normalize_asset_id("   "))
        self.assertIsNone(normalize_asset_id(None))
        self.assertIsNone(normalize_asset_id(42))

    def test_case_sensitive_no_folding(self):
        self.assertNotEqual(normalize_asset_id("28DKX-3681"),
                            normalize_asset_id("28dkx-3681").lower())
        matches, unmatched = join([{"id": "28DKX-3681"}],
                                  [{"id": "28dkx-3681"}])
        self.assertEqual(matches, [])
        self.assertEqual(len(unmatched), 1)

    def test_whitespace_variant_joins_after_normalization(self):
        matches, unmatched = join([{"id": "28DKX-3681 "}],
                                  [{"id": "28DKX-3681"}])
        self.assertEqual(len(matches), 1)
        self.assertEqual(unmatched, [])

    def test_none_never_joins(self):
        matches, unmatched = join([{"id": None}, {}],
                                  [{"id": None}])
        self.assertEqual(matches, [])
        self.assertEqual(len(unmatched), 2)

    def test_candidate_not_verified_link(self):
        # String equality is a candidate predicate, not a verified link:
        # the join reports the match; verification status lives elsewhere.
        matches, _ = join([{"id": "28DKX-3681"}],
                          [{"id": "28DKX-3681", "severity": None}])
        self.assertEqual(len(matches), 1)
        self.assertIsNone(matches[0][1][0]["severity"])


if __name__ == "__main__":
    unittest.main()
