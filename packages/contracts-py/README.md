# formal-claim-contracts-py

Canonical generated Python bindings for the frozen JSON Schemas in
`packages/contracts/schemas/`.

This package is generated, not hand-maintained. Regenerate it from the monorepo
root with:

```powershell
python -m uv run --python 3.12 --group dev python scripts/contracts/generate_bindings.py
```

The donor `formal_claim_pipeline.models` module consumes this package as a thin
compatibility layer until the M1 migration wave moves the engine into
`services/engine/`.
