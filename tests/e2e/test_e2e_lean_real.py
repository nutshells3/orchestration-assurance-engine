"""End-to-end test: 판례.txt → full pipeline → REAL Lean compilation via FWP → proof-assistant.

This test exercises the real proof backend chain:
  formal-claim engine
    → FwpProofAdapter
      → ProofProtocolClient (LocalHubTransport)
        → formal-hub (build_reference_hub)
          → proof-assistant (ProofAssistantHub)
            → LeanAdapter
              → lake build (real Lean 4 compiler)
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Bootstrap imports
# ---------------------------------------------------------------------------

def resolve_roots() -> tuple[Path, Path]:
    current = Path(__file__).resolve()
    for parent in current.parents:
        engine_src = parent / "services" / "engine" / "src"
        if engine_src.exists():
            return parent, engine_src
    raise RuntimeError("Could not locate monorepo root.")


REPO_ROOT, ENGINE_SRC = resolve_roots()

# Add engine, contracts, fwp, proof-assistant to sys.path
paths_to_add = [
    str(ENGINE_SRC),
    str(REPO_ROOT / "packages" / "contracts-py" / "src"),
]
for p in paths_to_add:
    if p not in sys.path:
        sys.path.insert(0, p)


from formal_claim_engine.engine_api import FormalClaimEngineAPI  # noqa: E402
from formal_claim_engine.config import PipelineConfig, ProofProtocolConfig  # noqa: E402

INPUT_FILE = REPO_ROOT.parent / "backups" / "판례.txt"
OUTPUT_ROOT = REPO_ROOT.parent / "_push" / "e2e-run-판례-lean-real"


def run_e2e():
    assert INPUT_FILE.exists(), f"Input file not found: {INPUT_FILE}"

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    data_dir = OUTPUT_ROOT / "pipeline_data"
    export_dir = OUTPUT_ROOT / "export"
    data_dir.mkdir(exist_ok=True)
    export_dir.mkdir(exist_ok=True)

    # ----- 1. Configure with REAL proof backend -----
    proof_config = ProofProtocolConfig(
        backend="fwp",
        transport="local_hub",
        target_backend_id="lean-local",
        fwp_repo_root=str(REPO_ROOT.parent / "fwp"),
    )
    config = PipelineConfig(
        data_dir=str(data_dir),
        proof_protocol=proof_config,
    )

    api = FormalClaimEngineAPI(config=config, data_dir=str(data_dir))
    print("[lean-e2e] Engine API created with FWP real backend.")

    # ----- 2. Create project -----
    project = api.create_project(
        name="판례_lean_real",
        domain="legal",
        description="E2E with real Lean compilation",
    )
    project_id = project.project_id
    print(f"[lean-e2e] Project: {project_id}")

    # ----- 3. Ingest -----
    text = INPUT_FILE.read_text(encoding="utf-8")
    ingest = asyncio.run(api.ingest_document(project_id, text))
    print(f"[lean-e2e] Ingested: {ingest.claims_added} claims, {ingest.relations_added} relations")

    # ----- 4. Structure -----
    try:
        structuring = asyncio.run(api.run_claim_structuring(project_id, text))
        claim_graph = structuring.model_dump(mode="json", exclude_none=True).get("claim_graph", {})
        print(f"[lean-e2e] Structured: {len(claim_graph.get('claims', []))} claims")
    except Exception as exc:
        claim_graph = api.get_graph(project_id)
        print(f"[lean-e2e] Structure fallback: {exc}")

    # Select ONE claim for lean verification (to keep it fast)
    claims = claim_graph.get("claims", [])
    if isinstance(claims, dict):
        claims = list(claims.values())
    claim_ids = [
        str(c.get("claim_id") or c.get("id", ""))
        for c in claims
        if c.get("claim_id") or c.get("id")
    ][:1]
    print(f"[lean-e2e] Selected claim for Lean verification: {claim_ids}")

    # ----- 5. Formalize + Build (REAL LEAN) -----
    formalization_results = {}
    for cid in claim_ids:
        print(f"\n[lean-e2e] === Processing {cid} ===")

        # Formalize (LLM generates .lean code)
        try:
            dual = asyncio.run(api.run_dual_formalization(project_id, cid))
            dual_data = dual.model_dump(mode="json", exclude_none=True)
            formalization_results[cid] = dual_data
            print(f"[lean-e2e]   formalize.dual OK")

            # Check what was generated
            build_results = dual_data.get("build_results", {})
            workflow = dual_data.get("dual_formalization_workflow", {})
            attempts = workflow.get("attempts", [])
            for att in attempts:
                label = att.get("formalizer_label", "?")
                output = att.get("output", {}) or {}
                code = output.get("proof_source", "")
                br = build_results.get(label, {})
                print(f"[lean-e2e]   attempt {label}: build_success={br.get('success', 'n/a')}, sorry_count={br.get('sorry_count', 'n/a')}")
                if br.get("stderr"):
                    stderr_preview = br["stderr"][:300]
                    print(f"[lean-e2e]   build stderr: {stderr_preview}")
                if code:
                    print(f"[lean-e2e]   code preview: {code[:300]}...")

        except Exception as exc:
            print(f"[lean-e2e]   formalize.dual FAILED: {exc}")
            formalization_results[cid] = {"error": str(exc)}

        # Audit
        try:
            audit = asyncio.run(api.run_audit(project_id, cid))
            audit_data = audit.model_dump(mode="json", exclude_none=True)
            print(f"[lean-e2e]   audit OK")

            # Profile
            api.recompute_profile(
                project_id, cid,
                audit_result=audit_data.get("audit_output", {}),
            )
            print(f"[lean-e2e]   profile recomputed")
        except Exception as exc:
            print(f"[lean-e2e]   audit/profile: {exc}")

    # ----- 6. Export trace -----
    print(f"\n[lean-e2e] === TRACE EXPORT ===")
    trace_result = api.export_trace(project_id, str(export_dir))
    print(f"[lean-e2e] trace.json: {Path(trace_result.trace_path).stat().st_size} bytes")
    print(f"[lean-e2e] transition_log: {Path(trace_result.transition_log_path).stat().st_size} bytes")
    print(f"[lean-e2e] sidecar_meta: {Path(trace_result.sidecar_meta_path).stat().st_size} bytes")
    print(f"[lean-e2e] violations: {trace_result.redaction_violations}")

    # ----- 7. Check for real build artifacts -----
    print(f"\n[lean-e2e] === BUILD ARTIFACTS ===")
    theories_dir = data_dir / "theories"
    if theories_dir.exists():
        for lean_file in sorted(theories_dir.rglob("*.lean")):
            print(f"[lean-e2e]   {lean_file.relative_to(data_dir)}")
            content = lean_file.read_text(encoding="utf-8")
            print(f"             {len(content)} chars, sorry_count={content.count('sorry')}")
    else:
        print("[lean-e2e]   No theories directory found")

    # Also check proof_audit_requests
    audit_dir = data_dir / "proof_audit_requests"
    if audit_dir.exists():
        for f in sorted(audit_dir.rglob("*.json")):
            print(f"[lean-e2e]   {f.relative_to(data_dir)}")

    # ----- Summary -----
    print(f"\n{'='*60}")
    print(f"LEAN REAL BACKEND E2E: COMPLETE")
    print(f"{'='*60}")

    result = {
        "project_id": project_id,
        "claims_ingested": ingest.claims_added,
        "claims_verified": len(claim_ids),
        "formalization_results": {
            cid: "error" if "error" in v else "ok"
            for cid, v in formalization_results.items()
        },
        "trace_bytes": Path(trace_result.trace_path).stat().st_size,
        "redaction_violations": trace_result.redaction_violations,
    }

    # Save result
    result_path = OUTPUT_ROOT / "e2e_result.json"
    result_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nResult saved to: {result_path}")
    return result


if __name__ == "__main__":
    result = run_e2e()
    print("\n" + json.dumps(result, indent=2, ensure_ascii=False))
