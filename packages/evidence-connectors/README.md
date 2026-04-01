# Evidence Connectors

Canonical adapters that normalize external evidence and document-analysis
artifacts into Formal Claim contracts.

The first adapter in this package accepts claim-tracer style extracted
claims and relations, emits source-mapping bundles, and returns a mapping
report from source-domain roles to canonical claim classes and kinds.

The adapter now also normalizes local-document identity and citation anchors.
It preserves:

- stable `document_id` / `source_ref` for repeated local-file ingest
- exact offsets when available
- fallback anchors for ambiguous or unresolved excerpt matches
- provenance fields needed for engine-owned source-mapping bundle revisions
- structured evaluation-evidence DTOs for metrics, comparisons, anchors, and uncertainty

The connector remains an ingestion adapter only. Canonical `ClaimGraph`
revision creation and admission stay engine-owned.

`extract_evaluation_evidence()` turns source-mapping bundles into structured
measurement candidates such as dataset/metric/comparison/baseline/reported
value records. The engine owns the persisted `evaluation_evidence_bundle`
artifact and decides how those extracted items link to claims, references, and
assurance artifacts.
