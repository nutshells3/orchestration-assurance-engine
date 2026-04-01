"""E2E runner: process all 5 documents from ../docs through the full pipeline.

For each document:
  1. Create project
  2. Ingest document text
  3. Run claim structuring
  4. Run dual formalization + audit per claim
  5. Export trace (trace.json + transition_log.jsonl + sidecar_meta.json)
  6. Export prefix slices (PrefixSliceTextV1)
  7. Validate all outputs (schema, no domain leak, gate checks)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap imports
# ---------------------------------------------------------------------------

def resolve_roots() -> tuple[Path, Path, Path]:
    current = Path(__file__).resolve()
    for parent in current.parents:
        engine_src = parent / "services" / "engine" / "src"
        contracts_src = parent / "packages" / "contracts-py" / "src"
        schema_dir = parent / "packages" / "contracts" / "schemas"
        if engine_src.exists():
            return engine_src, contracts_src, schema_dir
    raise RuntimeError("Could not locate monorepo root.")


ENGINE_SRC, CONTRACTS_SRC, SCHEMA_DIR = resolve_roots()
for p in (str(ENGINE_SRC), str(CONTRACTS_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from formal_claim_engine.engine_api import FormalClaimEngineAPI
from formal_claim_engine.config import PipelineConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOCS_DIR = Path(__file__).resolve().parents[2].parent / "docs"
OUTPUT_ROOT = Path(__file__).resolve().parents[2].parent / "_push" / "e2e-all-docs"

DOC_META = {
    "doc1.txt": {"domain": "academic",  "name": "remote_work_productivity"},
    "doc2.txt": {"domain": "academic",  "name": "ai_medical_diagnosis"},
    "doc3.txt": {"domain": "academic",  "name": "carbon_offset_markets"},
    "doc4.txt": {"domain": "academic",  "name": "university_rankings"},
    "doc5.txt": {"domain": "legal",     "name": "korean_court_ruling"},
}

BANNED_FIELDS = frozenset({
    "source_domain", "prompt_id", "api_key", "api_key_env",
    "api_base", "provider", "raw_llm_response", "raw_text",
    "usage", "model", "temperature", "max_tokens", "reasoning_effort",
})


def check_no_domain_leak(data: Any, path: str = "$") -> list[str]:
    violations: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in BANNED_FIELDS:
                violations.append(f"{path}.{key}")
            violations.extend(check_no_domain_leak(value, f"{path}.{key}"))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            violations.extend(check_no_domain_leak(item, f"{path}[{idx}]"))
    return violations


# ---------------------------------------------------------------------------
# Single document e2e
# ---------------------------------------------------------------------------

def run_single_doc(doc_filename: str) -> dict[str, Any]:
    meta = DOC_META[doc_filename]
    input_path = DOCS_DIR / doc_filename
    assert input_path.exists(), f"Document not found: {input_path}"

    text = input_path.read_text(encoding="utf-8")
    project_name = meta["name"]
    domain = meta["domain"]

    persist_dir = OUTPUT_ROOT / project_name
    persist_dir.mkdir(parents=True, exist_ok=True)
    data_dir = persist_dir / "pipeline_data"
    export_dir = persist_dir / "export"
    data_dir.mkdir(exist_ok=True)
    export_dir.mkdir(exist_ok=True)

    result: dict[str, Any] = {
        "doc": doc_filename,
        "domain": domain,
        "status": "pending",
        "checks": {},
    }

    print(f"\n{'='*70}")
    print(f"  [{doc_filename}] domain={domain}  name={project_name}")
    print(f"  text length: {len(text)} chars")
    print(f"{'='*70}")

    t0 = time.time()

    # ----- 1. Build engine API -----
    api = FormalClaimEngineAPI(
        config=PipelineConfig(data_dir=str(data_dir)),
        data_dir=str(data_dir),
    )
    print(f"  [1/7] Engine API created.")

    # ----- 2. Create project -----
    project = api.create_project(
        name=project_name,
        domain=domain,
        description=f"E2E test: {doc_filename}",
    )
    project_id = project.project_id
    print(f"  [2/7] Project created: {project_id}")

    # ----- 3. Ingest document -----
    ingest = asyncio.run(api.ingest_document(project_id, text))
    print(f"  [3/7] Ingested: {ingest.claims_added} claims, {ingest.relations_added} relations")
    result["claims_ingested"] = ingest.claims_added
    result["relations_ingested"] = ingest.relations_added

    # ----- 4. Run claim structuring -----
    try:
        structuring = asyncio.run(api.run_claim_structuring(project_id, text))
        claim_graph = structuring.model_dump(mode="json", exclude_none=True).get("claim_graph", {})
        print(f"  [4/7] Claim structuring OK: {len(claim_graph.get('claims', []))} claims")
    except Exception as exc:
        claim_graph = api.get_graph(project_id)
        print(f"  [4/7] Claim structuring fallback: {exc}")

    # Collect all claim IDs
    full_graph = api.get_graph(project_id)
    all_claims = full_graph.get("claims", []) if isinstance(full_graph, dict) else []
    if isinstance(all_claims, dict):
        all_claims = list(all_claims.values())
    struct_claims = claim_graph.get("claims", [])
    if isinstance(struct_claims, dict):
        struct_claims = list(struct_claims.values())
    seen_ids: set[str] = set()
    claim_ids: list[str] = []
    for c in struct_claims + all_claims:
        cid = str(c.get("claim_id") or c.get("id", ""))
        if cid and cid not in seen_ids:
            seen_ids.add(cid)
            claim_ids.append(cid)
    print(f"  [4/7] Selected {len(claim_ids)} claims for audit")
    result["claims_selected"] = len(claim_ids)

    # ----- 5. Formalization + Audit -----
    formalized = 0
    audited = 0
    for cid in claim_ids:
        try:
            asyncio.run(api.run_dual_formalization(project_id, cid))
            formalized += 1
        except Exception as exc:
            print(f"    formalize({cid}) skip: {exc}")

        try:
            audit = asyncio.run(api.run_audit(project_id, cid))
            audit_data = audit.model_dump(mode="json", exclude_none=True)
            api.recompute_profile(
                project_id, cid,
                audit_result=audit_data.get("audit_output", {}),
            )
            audited += 1
        except Exception as exc:
            print(f"    audit({cid}) skip: {exc}")

    print(f"  [5/7] Formalized: {formalized}/{len(claim_ids)}, Audited: {audited}/{len(claim_ids)}")
    result["formalized"] = formalized
    result["audited"] = audited

    # ----- 6. Trace export -----
    print(f"  [6/7] Exporting trace...")
    trace_result = api.export_trace(project_id, str(export_dir))
    trace_path = Path(trace_result.trace_path)
    log_path = Path(trace_result.transition_log_path)
    sidecar_path = Path(trace_result.sidecar_meta_path)

    checks = result["checks"]

    # Check: 3 files exist
    files_exist = trace_path.exists() and log_path.exists() and sidecar_path.exists()
    checks["export_files_exist"] = "PASS" if files_exist else "FAIL"

    # Check: trace.json no domain leak
    trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    violations = check_no_domain_leak(trace_data)
    checks["trace_no_domain_leak"] = "PASS" if not violations else f"FAIL: {violations[:3]}"

    # Check: transition_log.jsonl valid
    log_text = log_path.read_text(encoding="utf-8").strip()
    log_events = []
    log_valid = True
    if log_text:
        for line in log_text.splitlines():
            if line.strip():
                event = json.loads(line)
                log_events.append(event)
                if "step_id" not in event or "event_type" not in event:
                    log_valid = False
    checks["transition_log_valid"] = "PASS" if log_valid else "FAIL"
    checks["transition_log_events"] = len(log_events)

    # Check: sidecar_meta.json
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar_ok = "source_domain" in sidecar and "trace_id" in sidecar
    checks["sidecar_valid"] = "PASS" if sidecar_ok else "FAIL"

    # Check: redaction violations
    checks["redaction_violations"] = trace_result.redaction_violations
    checks["trace_bytes"] = trace_path.stat().st_size

    print(f"    trace.json: {checks['trace_bytes']} bytes, leak={checks['trace_no_domain_leak']}")
    print(f"    transition_log: {len(log_events)} events, valid={checks['transition_log_valid']}")
    print(f"    sidecar: domain={sidecar.get('source_domain')}, valid={checks['sidecar_valid']}")

    # ----- 7. Prefix slice export -----
    print(f"  [7/7] Exporting prefix slices...")
    prefix_result = api.export_prefix_slices(
        project_id,
        output_path=str(export_dir / "prefix_slices.jsonl"),
    )
    prefix_path = Path(prefix_result.output_path)
    prefix_ok = prefix_path.exists()
    slice_count = 0
    slices_valid = True
    if prefix_ok:
        slice_text = prefix_path.read_text(encoding="utf-8").strip()
        if slice_text:
            slices = [json.loads(line) for line in slice_text.splitlines() if line.strip()]
            slice_count = len(slices)
            for s in slices:
                if "schema_version" not in s or "state_text" not in s:
                    slices_valid = False
                if "source_domain" in (s.get("state_text") or ""):
                    slices_valid = False

    checks["prefix_slices_exist"] = "PASS" if prefix_ok else "FAIL"
    checks["prefix_slice_count"] = slice_count
    checks["prefix_slices_valid"] = "PASS" if slices_valid else "FAIL"

    print(f"    prefix slices: {slice_count} slices, valid={checks['prefix_slices_valid']}")

    # ----- Gate checks (B60 style) -----
    # Gate 1: prefix ordering follows event_seq
    if log_events and slice_count > 0:
        slices_data = [json.loads(line) for line in prefix_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        seq_map = {e["step_id"]: e.get("event_seq", 0) for e in log_events}
        seqs = [seq_map.get(s["step_id"], 0) for s in slices_data]
        ordering_ok = all(seqs[i] <= seqs[i+1] for i in range(len(seqs)-1))
        checks["gate1_prefix_ordering"] = "PASS" if ordering_ok else "FAIL"
    else:
        checks["gate1_prefix_ordering"] = "SKIP (no events or slices)"

    # Gate 6: controllable proposals canonical
    gate6_ok = True
    for e in log_events:
        if e.get("event_class") != "controllable_action":
            continue
        proposal = e.get("proposal")
        if not proposal:
            continue
        et = e.get("event_type", "")
        args = proposal.get("arguments", proposal.get("args", proposal))
        if et == "select_formalization" and "attempts" in args:
            gate6_ok = False
        if et == "propose_relation":
            strength = args.get("strength") or proposal.get("strength")
            if strength is None:
                gate6_ok = False
    checks["gate6_canonical_proposals"] = "PASS" if gate6_ok else "FAIL"

    # Gate 9: verifier_delta never {}
    empty_vd = sum(1 for e in log_events if e.get("verifier_delta") == {})
    if log_events:
        checks["gate9_verifier_delta"] = "PASS" if empty_vd == 0 else f"DRIFT ({empty_vd}/{len(log_events)} empty)"
    else:
        checks["gate9_verifier_delta"] = "SKIP (no events)"

    # Gate 11: no provider leaks
    leak_tokens = {"openai", "anthropic", "gpt-", "codex", "api_key", "sk-"}
    gate11_ok = True
    for e in log_events:
        reason = e.get("reject_reason")
        if not reason:
            continue
        reason_lower = str(reason).lower()
        for token in leak_tokens:
            if token in reason_lower:
                gate11_ok = False
    checks["gate11_no_provider_leaks"] = "PASS" if gate11_ok else "FAIL"

    elapsed = time.time() - t0
    result["elapsed_s"] = round(elapsed, 1)
    result["status"] = "DONE"

    print(f"\n  [{doc_filename}] DONE in {elapsed:.1f}s")
    return result


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  E2E ALL-DOCS RUNNER")
    print(f"  docs dir:   {DOCS_DIR}")
    print(f"  output dir: {OUTPUT_ROOT}")
    print("=" * 70)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    doc_files = sorted(DOC_META.keys())
    all_results: list[dict[str, Any]] = []

    for doc_file in doc_files:
        try:
            result = run_single_doc(doc_file)
            all_results.append(result)
        except Exception as exc:
            print(f"\n  [{doc_file}] FAILED: {exc}")
            traceback.print_exc()
            all_results.append({
                "doc": doc_file,
                "status": "ERROR",
                "error": str(exc),
            })

    # ----- Summary -----
    print("\n\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)

    total_pass = 0
    total_fail = 0
    total_drift = 0

    for r in all_results:
        doc = r["doc"]
        status = r["status"]
        checks = r.get("checks", {})

        pass_count = sum(1 for v in checks.values() if v == "PASS")
        fail_count = sum(1 for v in checks.values() if isinstance(v, str) and v.startswith("FAIL"))
        drift_count = sum(1 for v in checks.values() if isinstance(v, str) and "DRIFT" in v)

        total_pass += pass_count
        total_fail += fail_count
        total_drift += drift_count

        elapsed = r.get("elapsed_s", "?")
        claims = r.get("claims_selected", "?")
        slices = checks.get("prefix_slice_count", "?")
        events = checks.get("transition_log_events", "?")

        icon = "OK" if status == "DONE" and fail_count == 0 else "!!"
        print(f"  [{icon}] {doc:12s} | {status:5s} | {str(elapsed):>6s}s | claims={claims} events={events} slices={slices} | pass={pass_count} fail={fail_count} drift={drift_count}")

        # Print individual check results
        for k, v in checks.items():
            if isinstance(v, str) and (v.startswith("FAIL") or "DRIFT" in v):
                print(f"        {k}: {v}")

    print(f"\n  TOTAL: pass={total_pass}  fail={total_fail}  drift={total_drift}")
    print("=" * 70)

    # Save summary JSON
    summary_path = OUTPUT_ROOT / "e2e_summary.json"
    summary_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n  Summary saved to: {summary_path}")

    return all_results


if __name__ == "__main__":
    main()
