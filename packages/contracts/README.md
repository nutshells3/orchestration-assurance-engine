# @formal-claim/contracts

Canonical JSON Schema package for the Formal Claim Workbench.

## Scope

This package is the only normative location for the repository contracts:

- `schemas/claim-graph.schema.json`
- `schemas/assurance-graph.schema.json`
- `schemas/assurance-profile.schema.json`

All other packages and donor code must reference these schemas instead of carrying local copies.

Generated consumers live alongside this package:

- `packages/contracts-py/` for canonical Python bindings
- `examples/theorem-audit/` for golden valid fixtures
- `tests/schema/fixtures/` for invalid and legacy migration samples

## Version Policy

The schema package follows semantic versioning at the contract layer.

- Patch: typo fixes, descriptions, examples, or non-semantic metadata that do not change validation behavior.
- Minor: backward-compatible additions such as optional fields, broader enums only when old instances remain valid, or new example fixtures.
- Major: any validation change that can cause previously valid instances to fail, previously invalid instances to become valid in a way that changes policy semantics, renamed fields, removed fields, tightened constraints, or changed enum meaning.

## Migration Rules

- Every schema instance must carry `schema_version`.
- Contract changes must update fixtures and conformance tests in the same change.
- Donor or service packages may expose adapters, but they may not maintain forked schema copies.
- Any future schema migration must ship with an explicit upgrader or documented manual migration path.

## Release Rule

Until the M1 migration wave completes, donor packages may depend on these schemas by path, but they do so as consumers only. The schema files in this package remain the single source of truth.
