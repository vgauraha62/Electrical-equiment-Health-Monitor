"""Schema/Python parity tests (stdlib unittest, no cloud calls).

Decision 2: runtime validation is self-contained Python because schemas/ is
excluded from the deployment package. These tests prove the Python allowed-key
sets and the declarative JSON schemas cannot drift, including every object that
declares additionalProperties:false.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import agent_context as context  # noqa: E402
from app import agent_contract as contract  # noqa: E402
from app import agent_draft as draft_builder  # noqa: E402
from app.validate import validate  # noqa: E402

SCHEMA_DIR = ROOT / "schemas"
TARGET = "042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294"


def resolve(document, pointer):
    if pointer == "":
        return document
    node = document
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[token]
    return node


def walk_strict(node, pointer=""):
    found = set()
    if isinstance(node, dict):
        if node.get("additionalProperties", "absent") is False:
            found.add(pointer)
        for key, value in node.items():
            found |= walk_strict(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found |= walk_strict(value, f"{pointer}/{index}")
    return found


class SchemaParityTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {
            name: json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
            for name in ("agent_context.schema.json", "agent_draft.schema.json")
        }
        cls.registry = contract.contract_key_registry()

    def test_registered_objects_match_python_sets(self):
        for name, pointer, allowed, required in self.registry:
            with self.subTest(schema=name, pointer=pointer):
                node = resolve(self.schemas[name], pointer)
                self.assertIs(node.get("additionalProperties"), False)
                self.assertEqual(set(node.get("properties", {})), set(allowed))
                self.assertEqual(set(node.get("required", [])), set(required))

    def test_every_strict_schema_object_is_registered(self):
        for name, schema in self.schemas.items():
            declared = walk_strict(schema)
            registered = {pointer for schema_name, pointer, _a, _r in self.registry
                          if schema_name == name}
            self.assertEqual(declared, registered,
                             f"unregistered strict objects in {name}")

    def test_app_validator_accepts_generated_documents(self):
        import app.main as main_module
        main_module.BQ_TABLE = ""
        main_module.KNOWLEDGE_TABLE = ""
        main_module.REVIEW_TABLE = ""
        main_module._cache.update(at=0.0, rows=None)
        bundle = context.build_context_bundle(TARGET)
        draft = draft_builder.build_deterministic_draft(bundle)
        validate(bundle, self.schemas["agent_context.schema.json"])
        validate(draft, self.schemas["agent_draft.schema.json"])


if __name__ == "__main__":
    unittest.main()
