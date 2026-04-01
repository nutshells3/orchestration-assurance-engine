"""SFE-001: Trace Export Builder.

Builds PipelineTraceV1 from OAE runtime state and writes the canonical
three-file export bundle:

  trace.json           - model-safe pipeline trace (no domain fields)
  transition_log.jsonl - append-only event journal (PipelineEventV1)
  sidecar_meta.json    - operator-only metadata (source_domain lives here)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .store import ArtifactStore, canonical_artifact_id, now_utc

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

PIPELINE_TRACE_SCHEMA_VERSION = "PipelineTraceV1"
PIPELINE_TRACE_V2_SCHEMA_VERSION = "2.0.0"
PIPELINE_EVENT_SCHEMA_VERSION = "PipelineEventV1"
PIPELINE_EVENT_V2_SCHEMA_VERSION = "2.0.0"
SIDECAR_META_SCHEMA_VERSION = "2.0.0"

TRACE_ROLE_PREFIX = "tracer_role:"
TRACE_STATUS_PREFIX = "tracer_status:"
TRACE_DEPTH_PREFIX = "tracer_depth:"
TRACE_DOMAIN_PREFIX = "tracer_domain:"
SEMANTIC_TYPE_PREFIX = "semantic_type:"


def _json_text(data: Any, *, indent: int | None = None) -> str:
    return json.dumps(data, indent=indent, default=str, sort_keys=indent is None)


def _sha256(data: Any) -> str:
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# B40/SAFE-001: verifier_delta must never be {} in model-visible output.
# When detailed delta is unavailable, write explicit null fields plus
# unavailable_reason.
_UNAVAILABLE_VERIFIER_DELTA: dict[str, Any] = {
    "legality": None,
    "vector_score_delta": None,
    "gate_before": None,
    "gate_after": None,
    "contradiction_delta": None,
    "hidden_assumptions_added": None,
    "profile_recomputed": None,
    "unavailable_reason": "runtime_not_captured",
}


def _normalize_verifier_delta(delta: dict[str, Any] | None) -> dict[str, Any]:
    """Return a structurally complete verifier_delta, never an empty dict.

    If *delta* is None or an empty dict, returns explicit null fields
    with ``unavailable_reason: runtime_not_captured``.
    """
    if delta is not None and delta != {}:
        result = dict(delta)
        # Ensure all spec-required keys exist
        result.setdefault("contradiction_delta", None)
        result.setdefault("hidden_assumptions_added", None)
        return result
    return dict(_UNAVAILABLE_VERIFIER_DELTA)


def reconstruct_source_text(source_units: list[dict[str, Any]]) -> str:
    """Reconstruct source_text from ordered source_units.

    B40/SAFE-001: When source_text is empty but source_units exist,
    join unit texts by start_char order to deterministically rebuild
    the document text.
    """
    if not source_units:
        return ""
    sorted_units = sorted(source_units, key=lambda u: int(u.get("start_char", 0)))
    return "\n\n".join(str(u.get("text", "")) for u in sorted_units if u.get("text"))


def _sanitize_reject_reason_for_log(reason: str | None) -> str | None:
    """Defense-in-depth sanitization for reject_reason in TransitionLogWriter.

    Uses event_normalizer.sanitize_reject_reason if available; otherwise
    returns the reason as-is.  This ensures that even direct callers of
    TransitionLogWriter.record_event() do not write raw runtime diagnostics.
    """
    if reason is None:
        return None
    reason_str = str(reason).strip()
    if not reason_str:
        return None
    try:
        from .event_normalizer import sanitize_reject_reason
        safe, _raw = sanitize_reject_reason(reason_str)
        return safe
    except ImportError:
        return reason_str


def build_source_units_from_text(source_text: str) -> list[dict[str, Any]]:
    if not source_text or not source_text.strip():
        return []
    units: list[dict[str, Any]] = []
    paragraphs = source_text.split("\n\n")
    offset = 0
    counter = 0
    for para in paragraphs:
        if not para.strip():
            offset += len(para) + 2
            continue
        start = source_text.find(para, offset)
        if start == -1:
            start = offset
        end = start + len(para)
        counter += 1
        units.append({
            "unit_id": f"su-{counter:04d}",
            "start_char": start,
            "end_char": end,
            "text": para,
        })
        offset = end
    return units


def _extract_tag_value(tags: Any, prefix: str) -> str:
    for tag in list(tags or []):
        text = str(tag)
        if text.startswith(prefix):
            return text[len(prefix):]
    return ""


def _filter_export_tags(tags: Any) -> list[str]:
    filtered: list[str] = []
    for tag in list(tags or []):
        text = str(tag)
        if text.startswith(
            (
                TRACE_ROLE_PREFIX,
                TRACE_STATUS_PREFIX,
                TRACE_DEPTH_PREFIX,
                TRACE_DOMAIN_PREFIX,
                SEMANTIC_TYPE_PREFIX,
            )
        ):
            continue
        filtered.append(text)
    return filtered


def _semantic_node_type(claim: dict[str, Any]) -> str:
    explicit = str(claim.get("semantic_type") or "").strip()
    if explicit:
        return explicit
    tagged = _extract_tag_value(claim.get("tags"), SEMANTIC_TYPE_PREFIX)
    if tagged:
        return tagged
    role = _extract_tag_value(claim.get("tags"), TRACE_ROLE_PREFIX)
    if role == "hidden_assumption":
        return "hidden_assumption"
    if role in {"observation", "statute", "precedent"}:
        return "evidence"
    if role in {"definition", "theorem", "lemma", "corollary"}:
        return "formal_artifact"
    return "claim"


def _parse_relation_rationale_value(rationale: Any, key: str) -> str | None:
    text = str(rationale or "")
    marker = f"{key}="
    if marker not in text:
        return None
    tail = text.split(marker, 1)[1]
    return tail.split("|", 1)[0].strip() or None


def _semantic_relation_alias(value: str) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "assumes": "depends_on",
        "blocks": "challenges",
        "challenges": "challenges",
        "cites": "derived_from",
        "conflicts_with": "refutes",
        "contradicts": "refutes",
        "decomposes_into": "scoped_by",
        "depends_on": "depends_on",
        "derives": "derived_from",
        "derived_from": "derived_from",
        "formalizes": "formalizes",
        "generalizes": "reviews",
        "interprets": "reviews",
        "motivates": "supports",
        "refines": "formalizes",
        "requires": "depends_on",
        "reviews": "reviews",
        "scoped_by": "scoped_by",
        "specializes": "scoped_by",
        "strengthens": "supports",
        "supports": "supports",
        "supersedes": "supersedes",
        "weakens": "challenges",
    }
    if normalized in {
        "depends_on",
        "supports",
        "challenges",
        "refutes",
        "formalizes",
        "derived_from",
        "scoped_by",
        "reviews",
        "supersedes",
    }:
        return normalized
    return aliases.get(normalized, "depends_on")


def _semantic_relation_type(rel: dict[str, Any]) -> str:
    explicit = rel.get("semantic_relation_type")
    if explicit:
        return _semantic_relation_alias(str(explicit))
    rationale_semantic = _parse_relation_rationale_value(rel.get("rationale"), "semantic_relation_type")
    if rationale_semantic:
        return _semantic_relation_alias(rationale_semantic)
    rationale_trace = _parse_relation_rationale_value(rel.get("rationale"), "trace_relation")
    if rationale_trace:
        return _semantic_relation_alias(rationale_trace)
    raw = rel.get("relation_type")
    raw_value = raw.value if hasattr(raw, "value") else raw
    return _semantic_relation_alias(str(raw_value or ""))


def _normalize_dataset_strength_value(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value.value if hasattr(value, "value") else value).strip().lower()
    if not normalized:
        return None
    aliases = {
        "logical": "deductive",
        "precedential": "authoritative",
        "textual": "authoritative",
        "probabilistic": "statistical",
        "statistic": "statistical",
        "witness": "testimonial",
        "stipulative": "unknown",
        "weak": "unknown",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {
        "deductive",
        "inductive",
        "abductive",
        "analogical",
        "authoritative",
        "testimonial",
        "statistical",
        "unknown",
    }:
        return normalized
    return "unknown"


def _dataset_strength(rel: dict[str, Any]) -> str | None:
    direct = _normalize_dataset_strength_value(rel.get("strength"))
    if direct is not None:
        return direct
    semantic_strength = _parse_relation_rationale_value(rel.get("rationale"), "semantic_strength")
    if semantic_strength is not None:
        return _normalize_dataset_strength_value(semantic_strength)
    rationale_strength = _parse_relation_rationale_value(rel.get("rationale"), "strength")
    if rationale_strength is not None:
        return _normalize_dataset_strength_value(rationale_strength)
    return None


def _extract_source_unit_refs(claim: dict[str, Any], available_unit_ids: list[str]) -> list[str]:
    refs = claim.get("source_unit_refs")
    if refs is not None:
        return [str(item) for item in list(refs)]
    if available_unit_ids:
        return [available_unit_ids[0]]
    return []


def build_v2_claim_nodes(claims: list[dict[str, Any]], source_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unit_ids = [u["unit_id"] for u in source_units]
    nodes: list[dict[str, Any]] = []
    for claim in claims:
        node_id = str(canonical_artifact_id(claim.get("claim_id") or claim.get("node_id") or ""))
        nodes.append({
            "node_id": node_id,
            "type": _semantic_node_type(claim),
            "text": str(claim.get("text") or claim.get("nl_statement") or claim.get("title") or ""),
            "canonical_text": (
                str(claim.get("canonical_text") or claim.get("normalized_statement"))
                if claim.get("canonical_text") is not None or claim.get("normalized_statement") is not None
                else None
            ),
            "status": (
                str(claim.get("status").value if hasattr(claim.get("status"), "value") else claim.get("status"))
                if claim.get("status") is not None
                else None
            ),
            "lifecycle": str(claim.get("lifecycle")) if claim.get("lifecycle") is not None else None,
            "confidence": float(claim.get("confidence")) if claim.get("confidence") is not None else None,
            "source_unit_refs": _extract_source_unit_refs(claim, unit_ids),
            "tags": _filter_export_tags(claim.get("tags") or []),
        })
    return nodes


def build_v2_claim_edges(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for rel in relations:
        edges.append({
            "edge_id": str(canonical_artifact_id(rel.get("edge_id") or rel.get("relation_id") or "")),
            "src": str(canonical_artifact_id(rel.get("src") or rel.get("from_claim_id") or "")),
            "tgt": str(canonical_artifact_id(rel.get("tgt") or rel.get("to_claim_id") or "")),
            "relation_type": _semantic_relation_type(rel),
            "strength": _dataset_strength(rel),
            "weight": float(rel.get("weight")) if rel.get("weight") is not None else None,
        })
    return edges


def build_v2_claim_graph(claim_graph_data: dict[str, Any], source_units: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "claims": build_v2_claim_nodes(list(claim_graph_data.get("claims") or []), source_units),
        "relations": build_v2_claim_edges(list(claim_graph_data.get("relations") or [])),
    }


def build_v2_candidate_ledger(
    claim_graph_data: dict[str, Any],
    source_units: list[dict[str, Any]],
    legacy_ledger: list[dict[str, Any]] | None = None,
    candidate_registry: Any | None = None,
    runtime_candidate_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .candidate_registry import CandidateRegistry

    if runtime_candidate_ledger:
        # B30/ACT-002: Ensure every ledger entry carries is_hard_negative
        # and accepted_as fields.  Rejected entries are hard negatives.
        # Accepted entries must have accepted_as mapping to final edge IDs.
        # Build claim lookup for source_unit_refs backfill
        # Use build_v2_claim_nodes to get heuristic refs when originals are empty
        _v2_nodes = build_v2_claim_nodes(
            list(claim_graph_data.get("claims") or []),
            source_units,
        )
        _claim_refs_lookup: dict[str, list[str]] = {}
        for node in _v2_nodes:
            refs = node.get("source_unit_refs") or []
            if not refs:
                continue
            for key in ("claim_id", "node_id"):
                cid = str(node.get(key) or "")
                if cid:
                    _claim_refs_lookup[cid] = list(refs)

        # Build relation lookup from claim_graph for enrichment
        _rel_lookup: dict[str, dict[str, str]] = {}
        for rel in list(claim_graph_data.get("relations") or []):
            rid = str(rel.get("relation_id") or rel.get("edge_id") or "")
            if rid:
                _rel_lookup[rid] = {
                    "from_claim_id": str(rel.get("from_claim_id") or ""),
                    "to_claim_id": str(rel.get("to_claim_id") or ""),
                    "relation_type": str(
                        rel.get("relation_type").value if hasattr(rel.get("relation_type"), "value")
                        else (rel.get("relation_type") or "")
                    ),
                    "strength": str(rel.get("strength") or "unknown"),
                }

        def _enrich_ledger_entries(
            entries: list[dict[str, Any]],
            is_rejected_bucket: bool = False,
        ) -> list[dict[str, Any]]:
            enriched: list[dict[str, Any]] = []
            for entry in entries:
                e = dict(entry)
                if is_rejected_bucket:
                    e.setdefault("is_hard_negative", True)
                    e.setdefault("accepted", False)
                else:
                    e.setdefault("is_hard_negative", bool(not e.get("accepted", True)))
                # Ensure strength is never null on relation entries
                if "relation_type" in e or "src_id" in e or "from_claim_id" in e:
                    if not e.get("strength"):
                        e["strength"] = "unknown"
                # Fill missing src/tgt from claim_graph relations via accepted_as
                src = e.get("src_id") or e.get("from_claim_id") or ""
                tgt = e.get("tgt_id") or e.get("to_claim_id") or ""
                if (not src or not tgt) and e.get("accepted_as"):
                    final_rel = _rel_lookup.get(str(e["accepted_as"]), {})
                    if not src:
                        src = final_rel.get("from_claim_id", "")
                    if not tgt:
                        tgt = final_rel.get("to_claim_id", "")
                    if not e.get("strength") or e["strength"] == "unknown":
                        e["strength"] = final_rel.get("strength", "unknown")
                if src:
                    e["src_id"] = src
                    e["from_claim_id"] = src
                if tgt:
                    e["tgt_id"] = tgt
                    e["to_claim_id"] = tgt
                # Backfill source_unit_refs from claim graph
                if not e.get("source_unit_refs") or e["source_unit_refs"] == []:
                    # Try by accepted_as (for accepted claims) or canonical_text match
                    accepted_id = e.get("accepted_as") or ""
                    if accepted_id and accepted_id in _claim_refs_lookup:
                        e["source_unit_refs"] = _claim_refs_lookup[accepted_id]
                    elif e.get("candidate_id"):
                        # Try matching by claim text
                        ct = e.get("canonical_text", "")
                        for cid, refs in _claim_refs_lookup.items():
                            if refs:  # Just use the first available refs
                                e["source_unit_refs"] = refs
                                break
                if e.get("accepted") and not e.get("accepted_as"):
                    e.setdefault("accepted_as", e.get("candidate_id"))
                # Mark whether this hard negative is usable for Edge Proposer
                # (both src and tgt must be non-empty and resolve in graph)
                if e.get("is_hard_negative"):
                    src_ok = bool(e.get("src_id") or e.get("from_claim_id"))
                    tgt_ok = bool(e.get("tgt_id") or e.get("to_claim_id"))
                    tgt_val = str(e.get("tgt_id") or e.get("to_claim_id") or "")
                    e["is_hard_negative_usable"] = src_ok and tgt_ok and "missing" not in tgt_val
                enriched.append(e)
            return enriched

        return {
            "claims_proposed": _enrich_ledger_entries(
                list(runtime_candidate_ledger.get("claims_proposed") or [])
            ),
            "claims_accepted": _enrich_ledger_entries(
                list(runtime_candidate_ledger.get("claims_accepted") or [])
            ),
            "claims_rejected": _enrich_ledger_entries(
                list(runtime_candidate_ledger.get("claims_rejected") or []),
                is_rejected_bucket=True,
            ),
            "relations_proposed": _enrich_ledger_entries(
                list(runtime_candidate_ledger.get("relations_proposed") or [])
            ),
            "relations_accepted": _enrich_ledger_entries(
                list(runtime_candidate_ledger.get("relations_accepted") or [])
            ),
            "relations_rejected": _enrich_ledger_entries(
                list(runtime_candidate_ledger.get("relations_rejected") or []),
                is_rejected_bucket=True,
            ),
        }

    if candidate_registry is None:
        candidate_registry = CandidateRegistry(trace_id="export-local")

    unit_ids = [u["unit_id"] for u in source_units]
    claims_proposed: list[dict[str, Any]] = []
    claims_accepted: list[dict[str, Any]] = []
    claims_rejected: list[dict[str, Any]] = []
    relations_proposed: list[dict[str, Any]] = []
    relations_accepted: list[dict[str, Any]] = []
    relations_rejected: list[dict[str, Any]] = []

    for claim in list(claim_graph_data.get("claims") or []):
        claim_id = str(canonical_artifact_id(claim.get("claim_id") or claim.get("node_id") or ""))
        text = str(claim.get("nl_statement") or claim.get("text") or claim.get("title") or "")
        status = str(claim.get("status").value if hasattr(claim.get("status"), "value") else (claim.get("status") or ""))
        refs = _extract_source_unit_refs(claim, unit_ids)
        base = candidate_registry.create_candidate_entry(
            kind="claim",
            source_unit_refs=refs,
            canonical_text=text,
            accepted_as=claim_id if status != "rejected" else None,
            reject_reason="claim_rejected" if status == "rejected" else None,
            is_hard_negative=(status == "rejected"),
        )
        entry = {
            "candidate_id": base["candidate_id"],
            "proposal_id": base["proposal_id"],
            "canonical_text": text,
            "raw_text": text,
            "type": _semantic_node_type(claim),
            "source_unit_refs": refs,
            "accepted": status != "rejected",
            "accepted_as": claim_id if status != "rejected" else None,
            "merged_into": None,
            "reject_reason": None if status != "rejected" else "claim_rejected",
        }
        claims_proposed.append(dict(entry))
        if entry["accepted"]:
            claims_accepted.append(dict(entry))
        else:
            claims_rejected.append(dict(entry))

    for rel in list(claim_graph_data.get("relations") or []):
        rel_id = str(canonical_artifact_id(rel.get("relation_id") or rel.get("edge_id") or ""))
        status = str(rel.get("status").value if hasattr(rel.get("status"), "value") else (rel.get("status") or ""))
        base = candidate_registry.create_candidate_entry(
            kind="relation",
            source_unit_refs=[],
            canonical_text=_semantic_relation_type(rel),
            accepted_as=rel_id if status != "rejected" else None,
            reject_reason="relation_rejected" if status == "rejected" else None,
            is_hard_negative=(status == "rejected"),
        )
        entry = {
            "candidate_id": base["candidate_id"],
            "proposal_id": base["proposal_id"],
            "src_id": str(rel.get("from_claim_id") or rel.get("source_id") or ""),
            "tgt_id": str(rel.get("to_claim_id") or rel.get("target_id") or ""),
            "relation_type": _semantic_relation_type(rel),
            "strength": _dataset_strength(rel) or "unknown",
            "accepted": status != "rejected",
            "accepted_as": rel_id if status != "rejected" else None,
            "reject_reason": None if status != "rejected" else "relation_rejected",
        }
        relations_proposed.append(dict(entry))
        if entry["accepted"]:
            relations_accepted.append(dict(entry))
        else:
            relations_rejected.append(dict(entry))

    return {
        "claims_proposed": claims_proposed,
        "claims_accepted": claims_accepted,
        "claims_rejected": claims_rejected,
        "relations_proposed": relations_proposed,
        "relations_accepted": relations_accepted,
        "relations_rejected": relations_rejected,
    }


def build_v2_structuring_workflow(workflow_state: dict[str, Any] | None = None) -> dict[str, Any]:
    if not workflow_state:
        return {"planner_action": None, "attempts": [], "validation_errors": []}
    return {
        "planner_action": workflow_state.get("planner_action"),
        "attempts": list(workflow_state.get("attempts") or []),
        "validation_errors": list(
            workflow_state.get("last_validation_errors")
            or workflow_state.get("validation_errors")
            or []
        ),
    }


# ---------------------------------------------------------------------------
# TraceExportBuilder
# ---------------------------------------------------------------------------


class TraceExportBuilder:
    """Builds PipelineTraceV1 from OAE runtime state."""

    def __init__(self, run_id: str, engine_state: dict[str, Any] | None = None) -> None:
        self.run_id = run_id
        self.engine_state = engine_state or {}
        self.trace_id = f"trace.{run_id}"
        self._created_at = now_utc().isoformat()

    def build_meta(self) -> dict[str, Any]:
        """Build trace metadata (trace_id, schema_version, oae_commit, etc.)."""
        return {
            "trace_id": self.trace_id,
            "schema_version": PIPELINE_TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "created_at": self._created_at,
            "oae_commit": self.engine_state.get("oae_commit", ""),
            "engine_version": self.engine_state.get("engine_version", ""),
        }

    def build_meta_v2(self) -> dict[str, Any]:
        state = self.engine_state
        source_text = str(state.get("source_text") or "")
        return {
            "trace_id": self.trace_id,
            "schema_version": PIPELINE_TRACE_V2_SCHEMA_VERSION,
            "run_id": self.run_id,
            "created_at": self._created_at,
            "engine_version": str(state.get("engine_version") or "0.1.0"),
            "oae_commit": str(state.get("oae_commit") or ""),
            "audit_rules_version": str(state.get("audit_rules_version") or ""),
            "promotion_fsm_version": str(state.get("promotion_fsm_version") or ""),
            "verifier_versions": dict(state.get("verifier_versions") or {}),
            "source_sha256": _sha256(source_text),
        }

    def build_source(self, source_text: str, source_units: list[dict[str, Any]]) -> dict[str, Any]:
        """Build source section with text and unit spans."""
        return {
            "source_text": source_text,
            "source_units": list(source_units),
            "source_hash": _sha256(source_text),
        }

    def build_source_v2(
        self,
        source_text: str,
        source_units: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "source_text": source_text,
            "source_units": list(source_units or build_source_units_from_text(source_text)),
        }

    def build_phase1(
        self,
        claim_graph: dict[str, Any],
        candidate_ledger: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build phase1 section with claim graph and candidate ledger."""
        return {
            "claim_graph": dict(claim_graph),
            "candidate_ledger": list(candidate_ledger or []),
        }

    def build_phase1_v2(
        self,
        claim_graph_data: dict[str, Any],
        source_units: list[dict[str, Any]],
        *,
        candidate_registry: Any | None = None,
        legacy_ledger: list[dict[str, Any]] | None = None,
        workflow_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "claim_graph": build_v2_claim_graph(claim_graph_data, source_units),
            "structuring_workflow": build_v2_structuring_workflow(workflow_state),
            "candidate_ledger": build_v2_candidate_ledger(
                claim_graph_data,
                source_units,
                legacy_ledger=legacy_ledger,
                candidate_registry=candidate_registry,
                runtime_candidate_ledger=(
                    dict(workflow_state.get("candidate_ledger") or {})
                    if workflow_state
                    else None
                ),
            ),
        }

    def build_phase2(self, per_claim_results: dict[str, Any]) -> dict[str, Any]:
        """Build phase2 section with dual formalization, audit, profile, promotion."""
        results: dict[str, Any] = {}
        for claim_id, claim_data in per_claim_results.items():
            results[claim_id] = {
                "dual_formalization": dict(claim_data.get("dual_formalization") or {}),
                "audit": dict(claim_data.get("audit") or {}),
                "profile": dict(claim_data.get("profile") or {}),
                "promotion": dict(claim_data.get("promotion") or {}),
            }
        return {"per_claim": results}

    def build_phase2_v2(self, per_claim_results: dict[str, Any]) -> dict[str, Any]:
        per_claim: dict[str, Any] = {}
        any_executed = False
        all_certification_eligible = True
        for claim_id, claim_data in per_claim_results.items():
            dual_formalization = claim_data.get("dual_formalization")
            if dual_formalization == {}:
                dual_formalization = None
            audit = claim_data.get("audit")
            if audit == {}:
                audit = None
            profile = claim_data.get("profile")
            if profile == {}:
                profile = None
            promotion_transitions = []
            for item in list(claim_data.get("promotion_transitions") or []):
                if isinstance(item, dict):
                    promotion_transitions.append({
                        "event_id": item.get("event_id"),
                        "from_gate": str(item.get("from_gate") or ""),
                        "to_gate": str(item.get("to_gate") or ""),
                        "actor": str(item.get("actor") or ""),
                        "actor_role": str(item.get("actor_role") or ""),
                        "override": bool(item.get("override", False)),
                        "rationale": str(item.get("rationale") or ""),
                        "created_at": str(item.get("created_at") or ""),
                    })
            per_claim[claim_id] = {
                "dual_formalization": dual_formalization,
                "build_results": dict(claim_data.get("build_results") or {}),
                "verifier_results": dict(claim_data.get("verifier_results") or {}),
                "audit": audit,
                "profile": profile,
                "promotion_transitions": promotion_transitions,
            }
            if dual_formalization or audit or profile:
                any_executed = True
            if profile is None or str(profile.get("gate") or "draft") in {"draft", "blocked", "rejected"}:
                all_certification_eligible = False

        phase2_flags = {
            "phase2_executed": any_executed,
            "certification_eligible": any_executed and all_certification_eligible,
        }
        if not any_executed:
            phase2_flags["unavailable_reason"] = "runtime_not_captured"
        return {
            "per_claim": per_claim,
            "phase2_flags": phase2_flags,
        }

    def build_phase3(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Build phase3 section with evidence and updated profiles."""
        return {
            "evidence": dict(evidence.get("evidence") or {}),
            "updated_profiles": dict(evidence.get("updated_profiles") or {}),
        }

    def build_phase3_v2(self, evidence_data: dict[str, Any] | None) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        any_executed = False
        if evidence_data and isinstance(evidence_data, dict):
            raw_evidence = evidence_data.get("evidence", evidence_data)
            if isinstance(raw_evidence, dict):
                for claim_id, claim_evidence in raw_evidence.items():
                    if not isinstance(claim_evidence, dict):
                        continue
                    research_output = claim_evidence.get("research_output")
                    updated_profile = claim_evidence.get("updated_profile")
                    if research_output is not None:
                        any_executed = True
                    evidence[claim_id] = {
                        "research_output": dict(research_output or {}),
                        "updated_profile": dict(updated_profile) if isinstance(updated_profile, dict) else None,
                    }
        result: dict[str, Any] = {"evidence": evidence}
        if any_executed:
            result["phase3_flags"] = {"phase3_executed": True}
        else:
            result["phase3_flags"] = {
                "phase3_executed": False,
                "unavailable_reason": "runtime_not_captured",
            }
        return result

    def build_trace_results(
        self,
        forward_traces: list[dict[str, Any]] | None = None,
        backward_traces: list[dict[str, Any]] | None = None,
        gaps: list[dict[str, Any]] | None = None,
        soundness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build trace_results section."""
        return {
            "forward_traces": list(forward_traces or []),
            "backward_traces": list(backward_traces or []),
            "gaps": list(gaps or []),
            "soundness": dict(soundness or {}),
        }

    def build_trace_results_v2(
        self,
        *,
        forward_traces: dict[str, Any] | None = None,
        backward_traces: dict[str, Any] | None = None,
        propagation_traces: list[dict[str, Any]] | None = None,
        vector_score_deltas: list[dict[str, Any]] | None = None,
        gap_analysis: dict[str, Any] | None = None,
        soundness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "forward_traces": dict(forward_traces or {}),
            "backward_traces": dict(backward_traces or {}),
            "gap_analysis": dict(gap_analysis or {}),
            "soundness": dict(soundness or {"score": 0.0, "method": "", "notes": []}),
        }
        if not result["forward_traces"]:
            result["forward_traces_unavailable_reason"] = "runtime_not_captured"
        if not result["backward_traces"]:
            result["backward_traces_unavailable_reason"] = "runtime_not_captured"
        if not result["gap_analysis"]:
            result["gap_analysis_unavailable_reason"] = "runtime_not_captured"
        if propagation_traces:
            result["propagation_traces"] = list(propagation_traces)
        else:
            result["propagation_traces"] = []
            result["propagation_traces_unavailable_reason"] = "runtime_not_captured"
        if vector_score_deltas:
            result["vector_score_deltas"] = list(vector_score_deltas)
        else:
            result["vector_score_deltas"] = []
            result["vector_score_deltas_unavailable_reason"] = "runtime_not_captured"
        return result

    def build(self) -> dict[str, Any]:
        """Assemble complete PipelineTraceV1."""
        trace: dict[str, Any] = {
            "meta": self.build_meta(),
        }
        state = self.engine_state

        if "source_text" in state or "source_units" in state:
            trace["source"] = self.build_source(
                source_text=str(state.get("source_text") or ""),
                source_units=list(state.get("source_units") or []),
            )

        if "claim_graph" in state:
            trace["phase1"] = self.build_phase1(
                claim_graph=dict(state.get("claim_graph") or {}),
                candidate_ledger=list(state.get("candidate_ledger") or []),
            )

        if "per_claim_results" in state:
            trace["phase2"] = self.build_phase2(
                per_claim_results=dict(state.get("per_claim_results") or {}),
            )

        if "evidence" in state:
            trace["phase3"] = self.build_phase3(
                evidence=dict(state.get("evidence") or {}),
            )

        if any(
            key in state
            for key in ("forward_traces", "backward_traces", "gaps", "soundness")
        ):
            trace["trace_results"] = self.build_trace_results(
                forward_traces=list(state.get("forward_traces") or []),
                backward_traces=list(state.get("backward_traces") or []),
                gaps=list(state.get("gaps") or []),
                soundness=dict(state.get("soundness") or {}),
            )

        return trace

    def build_v2(
        self,
        *,
        candidate_registry: Any | None = None,
        workflow_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.engine_state
        source = self.build_source_v2(
            str(state.get("source_text") or ""),
            list(state.get("source_units") or []),
        )
        phase1 = self.build_phase1_v2(
            dict(state.get("claim_graph") or {}),
            list(source.get("source_units") or []),
            candidate_registry=candidate_registry,
            legacy_ledger=list(state.get("candidate_ledger") or []),
            workflow_state=workflow_state,
        )
        phase2 = self.build_phase2_v2(dict(state.get("per_claim_results") or {}))
        phase3 = self.build_phase3_v2(state.get("evidence"))
        trace_results = self.build_trace_results_v2(
            forward_traces=state.get("forward_traces"),
            backward_traces=state.get("backward_traces"),
            propagation_traces=state.get("propagation_traces"),
            vector_score_deltas=state.get("vector_score_deltas"),
            gap_analysis=state.get("gap_analysis") or state.get("gaps"),
            soundness=state.get("soundness"),
        )
        return {
            "schema_version": PIPELINE_TRACE_V2_SCHEMA_VERSION,
            "meta": self.build_meta_v2(),
            "source": source,
            "phase1": phase1,
            "phase2": phase2,
            "phase3": phase3,
            "trace_results": trace_results,
        }

    def export_to_directory(self, output_dir: str) -> None:
        """Write trace.json, transition_log.jsonl, sidecar_meta.json to output_dir."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Import here to avoid circular dependency at module level
        from .model_safe_serializer import ModelSafeSerializer

        trace = self.build()
        redacted_trace = ModelSafeSerializer.redact(trace)
        (out / "trace.json").write_text(
            _json_text(redacted_trace, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_store(
        cls,
        run_id: str,
        store: ArtifactStore,
        project_id: str,
        *,
        claim_graph: Any | None = None,
    ) -> dict[str, Any]:
        from .phase_assembler import PhaseAssembler

        assembler = PhaseAssembler(store)
        if claim_graph is None:
            graph_ids = store.list_claim_graphs()
            for gid in graph_ids:
                try:
                    cg = store.load_claim_graph(gid)
                    if canonical_artifact_id(getattr(cg, "project_id", "")) == canonical_artifact_id(project_id):
                        claim_graph = cg
                        break
                except Exception:
                    continue
            if claim_graph is None and graph_ids:
                claim_graph = store.load_claim_graph(graph_ids[0])

        claim_ids: list[str] = []
        if claim_graph is not None:
            claim_ids = [canonical_artifact_id(c.claim_id) for c in claim_graph.claims]

        builder = cls(run_id)
        return {
            "meta": builder.build_meta(),
            "phase2": assembler.assemble_phase2(project_id, claim_ids),
            "phase3": assembler.assemble_phase3(project_id, claim_ids),
            "trace_version": "1.0.0",
            "project_id": project_id,
            "generated_at": now_utc().isoformat(),
        }


# ---------------------------------------------------------------------------
# TransitionLogWriter
# ---------------------------------------------------------------------------


class TransitionLogWriter:
    """Collects PipelineEventV2 events and writes transition_log.jsonl."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self._events: list[dict[str, Any]] = []
        self._event_counter = 0

    def record_event(
        self,
        step_id: str,
        phase: str,
        event_type: str,
        actor: str,
        before_hash: str,
        after_hash: str,
        event_class: str = "controllable_action",
        cause_event_id: str | None = None,
        proposal: dict[str, Any] | None = None,
        accepted: bool | None = None,
        reject_reason: str | None = None,
        changed_ids: list[str] | None = None,
        verifier_delta: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a single pipeline event.

        B30/ACT-002: The ``outcome`` parameter carries the state changes
        produced by this event (claims added, relations added, etc.) so
        that prefix builders can reconstruct progressive visible state
        without relying on a final-snapshot copy.
        """
        self._event_counter += 1
        seq = self._event_counter
        event: dict[str, Any] = {
            "schema_version": PIPELINE_EVENT_V2_SCHEMA_VERSION,
            "event_id": f"{self.trace_id}.evt.{seq:04d}",
            "trace_id": self.trace_id,
            "event_seq": seq,
            "step_id": step_id or f"step-{seq:04d}",
            "phase": phase,
            "event_type": event_type,
            "event_class": event_class,
            "actor": actor,
            "timestamp": now_utc().isoformat(),
            "before_hash": before_hash or "0" * 16,
            "after_hash": after_hash or "0" * 16,
            "changed_ids": list(changed_ids or []),
            "cause_event_id": cause_event_id if event_class == "automatic_consequence" else None,
        }
        event["proposal"] = dict(proposal) if proposal is not None else None
        event["accepted"] = bool(accepted) if accepted is not None else None
        # B40/SAFE-002: Defense-in-depth sanitization of reject_reason.
        # The primary sanitization is in event_normalizer.sanitize_reject_reason,
        # but we also guard here in case callers bypass the normalizer.
        event["reject_reason"] = _sanitize_reject_reason_for_log(reject_reason)
        event["verifier_delta"] = _normalize_verifier_delta(verifier_delta)
        # B30: Carry outcome data so prefix builders can accumulate state.
        if outcome is not None:
            event["outcome"] = dict(outcome)
        self._events.append(event)
        return event

    def get_events(self) -> list[dict[str, Any]]:
        """Return all recorded events."""
        return list(self._events)

    def write_jsonl(self, path: str) -> None:
        """Write events as JSONL to the given path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for event in self._events:
                handle.write(json.dumps(event, default=str) + "\n")


# ---------------------------------------------------------------------------
# SidecarMetaWriter
# ---------------------------------------------------------------------------


class SidecarMetaWriter:
    """Writes sidecar_meta.json with operator-only metadata."""

    def __init__(self, trace_id: str, source_domain: str) -> None:
        self.trace_id = trace_id
        self.source_domain = source_domain

    def build(self) -> dict[str, Any]:
        """Build the sidecar metadata dict."""
        return {
            "schema_version": SIDECAR_META_SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "source_domain": self.source_domain,
        }

    def write(self, path: str) -> None:
        """Write sidecar_meta.json to the given path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            _json_text(self.build(), indent=2),
            encoding="utf-8",
        )


__all__ = [
    "PIPELINE_EVENT_SCHEMA_VERSION",
    "PIPELINE_EVENT_V2_SCHEMA_VERSION",
    "PIPELINE_TRACE_SCHEMA_VERSION",
    "PIPELINE_TRACE_V2_SCHEMA_VERSION",
    "SIDECAR_META_SCHEMA_VERSION",
    "SidecarMetaWriter",
    "TraceExportBuilder",
    "TransitionLogWriter",
    "build_source_units_from_text",
    "build_v2_candidate_ledger",
    "build_v2_claim_edges",
    "build_v2_claim_graph",
    "build_v2_claim_nodes",
    "build_v2_structuring_workflow",
    "reconstruct_source_text",
]
