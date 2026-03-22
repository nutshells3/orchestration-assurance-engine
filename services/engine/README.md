# formal-claim-engine

Canonical assurance engine for `formal-claim`.

Current scope:

- claim-structuring orchestration with explicit admission states and retry checkpoints
- dual-formalization workflow with divergence capture and persisted claim-linked attempt lineage
- FWP-backed audit workflow with deterministic AssuranceProfile generation across trust, probe, and robustness signals
- SQLite-authoritative repository and artifact store with JSON export mirrors
- deterministic assurance-profile computation via canonical audit rules
- legacy artifact migration into the canonical store
- agent adapters and LLM client
- thin internal CLI surfaces for operator and test flows

This package no longer owns a prover runtime. Proof execution must cross the
FWP seam and terminate inside `proof-assistant`.
