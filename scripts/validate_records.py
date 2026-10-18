"""Validate NDJSON records against schemas/v1 using app.validate (stdlib only)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.validate import ValidationError, validate  # noqa: E402


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", required=True)
    parser.add_argument("--records", required=True)
    args = parser.parse_args()
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    failures = 0
    total = 0
    for line in Path(args.records).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            validate(json.loads(line), schema)
        except ValidationError as exc:
            failures += 1
            print(f"INVALID line {total}: {exc}")
    print(f"checked={total} invalid={failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
