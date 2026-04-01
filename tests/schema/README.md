# Schema Tests

`test_schema_conformance.py` is the canonical schema gate for the M0 foundation
wave.

- Valid fixtures live under `examples/theorem-audit/`.
- Invalid fixtures live under `tests/schema/fixtures/invalid/`.
- Legacy migration samples live under `tests/schema/fixtures/legacy/`.

The test script validates:

- JSON Schema conformance for valid fixtures
- readable failures for invalid fixtures
- round-trip validation through generated Python bindings
- TypeScript fixture type-checking through the generated bindings package
