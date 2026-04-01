# @formal-claim/contracts

Canonical JSON Schema package for the Formal Claim Workbench.

## Scope

This package is the only normative location for the repository contracts.

### v1 schemas (legacy, still valid)

- `schemas/claim-graph.schema.json`
- `schemas/assurance-graph.schema.json`
- `schemas/assurance-profile.schema.json`
- `schemas/pipeline-trace.schema.json`
- `schemas/pipeline-event.schema.json`
- `schemas/trace-sidecar-meta.schema.json`
- `schemas/prefix-slice.schema.json` (text projection -- `PrefixSliceTextV1`)
- `schemas/prefix-slice-graph.schema.json` (graph projection -- `PrefixSliceGraphV1`)

### v2 schemas (frozen dataset contract)

- `schemas/pipeline-trace-v2.schema.json` -- `PipelineTraceV2` (`schema_version: "2.0.0"`)
- `schemas/pipeline-event-v2.schema.json` -- `PipelineEventV2` (`schema_version: "2.0.0"`)
- `schemas/trace-sidecar-meta-v2.schema.json` -- `TraceSidecarMetaV2` (`schema_version: "2.0.0"`)
- `schemas/prefix-slice-text-v1.schema.json` -- `PrefixSliceTextV1Frozen` (`schema_version: "1.0.0"`)
- `schemas/prefix-slice-graph-v1.schema.json` -- `PrefixSliceGraphV1Frozen` (`schema_version: "1.0.0"`)

All other packages and donor code must reference these schemas instead of carrying local copies.

Generated consumers live alongside this package:

- `packages/contracts-py/` for canonical Python bindings
- `examples/theorem-audit/` for golden valid fixtures
- `tests/schema/fixtures/` for invalid and legacy migration samples

## Version Literal Policy

Every schema instance **must** carry a `schema_version` field with a frozen version literal:

| Schema file | `schema_version` literal | Pydantic type |
|---|---|---|
| `pipeline-trace-v2.schema.json` | `"2.0.0"` | `Literal['2.0.0']` |
| `pipeline-event-v2.schema.json` | `"2.0.0"` | `Literal['2.0.0']` |
| `trace-sidecar-meta-v2.schema.json` | `"2.0.0"` | `Literal['2.0.0']` |
| `prefix-slice-text-v1.schema.json` | `"1.0.0"` | `Literal['1.0.0']` |
| `prefix-slice-graph-v1.schema.json` | `"1.0.0"` | `Literal['1.0.0']` |

Wire literals must be stable and explicit. Do **not** emit semantic type names where the schema expects version literals. Bindings enforce these literals via `Literal[...]` type annotations.

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
