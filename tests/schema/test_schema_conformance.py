"""Schema conformance checks for the canonical contracts and generated bindings."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "packages" / "contracts" / "schemas"
PYTHON_BINDINGS_SRC = ROOT / "packages" / "contracts-py" / "src"

if str(PYTHON_BINDINGS_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_BINDINGS_SRC))

from formal_claim_contracts import AssuranceGraph, AssuranceProfile, ClaimGraph  # noqa: E402


VALID_FIXTURES = [
    (
        "claim-graph.schema.json",
        ROOT / "examples" / "theorem-audit" / "claim-graph.json",
        ClaimGraph,
    ),
    (
        "assurance-graph.schema.json",
        ROOT / "examples" / "theorem-audit" / "assurance-graph.json",
        AssuranceGraph,
    ),
    (
        "assurance-profile.schema.json",
        ROOT / "examples" / "theorem-audit" / "assurance-profile.json",
        AssuranceProfile,
    ),
]

INVALID_FIXTURES = [
    (
        "claim-graph.schema.json",
        ROOT / "tests" / "schema" / "fixtures" / "invalid" / "claim-graph.invalid.json",
        ["[] is too short"],
    ),
    (
        "assurance-graph.schema.json",
        ROOT / "tests" / "schema" / "fixtures" / "invalid" / "assurance-graph.invalid.json",
        ["[] is too short"],
    ),
    (
        "assurance-profile.schema.json",
        ROOT / "tests" / "schema" / "fixtures" / "invalid" / "assurance-profile.invalid.json",
        ["'ship' is not one of"],
    ),
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_errors(schema_name: str, data: dict) -> list[str]:
    schema = load_json(SCHEMA_DIR / schema_name)
    validator = jsonschema.Draft202012Validator(schema)
    return sorted(error.message for error in validator.iter_errors(data))


def assert_valid_fixture(schema_name: str, fixture_path: Path, model_cls: type) -> None:
    data = load_json(fixture_path)
    errors = schema_errors(schema_name, data)
    if errors:
        raise AssertionError(f"{fixture_path} should be valid but failed: {errors}")

    instance = model_cls.model_validate(data)
    roundtrip_errors = schema_errors(
        schema_name, instance.model_dump(mode="json", exclude_none=True)
    )
    if roundtrip_errors:
        raise AssertionError(
            f"{fixture_path} failed round-trip validation: {roundtrip_errors}"
        )


def assert_invalid_fixture(
    schema_name: str, fixture_path: Path, expected_substrings: list[str]
) -> None:
    data = load_json(fixture_path)
    errors = schema_errors(schema_name, data)
    if not errors:
        raise AssertionError(f"{fixture_path} should be invalid but passed validation.")

    for expected in expected_substrings:
        if not any(expected in error for error in errors):
            raise AssertionError(
                f"{fixture_path} did not include expected error {expected!r}. "
                f"Actual errors: {errors}"
            )


def main() -> None:
    for schema_name, fixture_path, model_cls in VALID_FIXTURES:
        assert_valid_fixture(schema_name, fixture_path, model_cls)

    for schema_name, fixture_path, expected in INVALID_FIXTURES:
        assert_invalid_fixture(schema_name, fixture_path, expected)


if __name__ == "__main__":
    main()
