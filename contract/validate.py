"""Validate every example message in contract/examples against its JSON Schema.

The schema is chosen by filename prefix: state*.json -> schemas/state.schema.json,
signal_header*.json -> schemas/signal_header.schema.json, and so on.
Exits non-zero if any schema is invalid, any example fails, or a schema has no example.

Usage: python contract/validate.py
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE / "schemas"
EXAMPLES = HERE / "examples"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    schemas = {p.name[: -len(".schema.json")]: load(p) for p in sorted(SCHEMAS.glob("*.schema.json"))}
    failures = 0

    for name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as e:
            print(f"FAIL  schemas/{name}.schema.json is not a valid draft 2020-12 schema: {e}")
            failures += 1

    covered = set()
    for example in sorted(EXAMPLES.glob("*.json")):
        # longest matching prefix wins, so signal_header*.json never matches a shorter schema name
        matches = [n for n in schemas if example.stem == n or example.stem.startswith(n + "_")]
        if not matches:
            print(f"FAIL  examples/{example.name}: no schema matches its filename prefix")
            failures += 1
            continue
        name = max(matches, key=len)
        covered.add(name)

        validator = Draft202012Validator(schemas[name])
        errors = sorted(validator.iter_errors(load(example)), key=lambda e: list(e.path))
        if errors:
            failures += 1
            print(f"FAIL  examples/{example.name} vs {name}.schema.json")
            for e in errors:
                loc = "/".join(str(p) for p in e.path) or "(root)"
                print(f"        {loc}: {e.message}")
        else:
            print(f"ok    examples/{example.name} vs {name}.schema.json")

    for name in sorted(set(schemas) - covered):
        print(f"FAIL  schemas/{name}.schema.json has no example")
        failures += 1

    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(f"\nall {len(covered)} schemas valid; all examples pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
