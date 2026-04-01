"""PFX-006 -- PrefixSliceGraphV1 Builder.

Builds graph-structured prefix slices from PipelineTrace data at each
decision point.  Unlike PrefixSliceBuilder (PFX-001) which produces
canonical *text* representations, this builder produces the v2
state_graph shape (nodes + edges) suitable for GNN-based models.

Invariants (same as PFX-001 text builder)
-----------------------------------------
* Same temporal gating: no future data leaks (state at step t only
  includes information from events strictly before t).
* Same domain-free policy: ``source_domain`` NEVER appears in node or
  edge features.
* Graph structure comes from the claim graph's claims and relations.
* Must share trace_id, step_id, available_artifacts, and cutoff
  semantics with PrefixSliceBuilder (PFX-001 parity).

PFX-001: Canonical PrefixSliceGraphV1 builder with trace_id/step_id parity.
PFX-003: Gold action DSL extraction from controllable events.
PFX-004: No-future leakage, omission_reason semantics.

Spec references
---------------
* OAE Output Spec v2 -- PrefixSliceGraphV1 schema
* packages/contracts/schemas/prefix-slice-graph-v1.schema.json
* PFX-001 PrefixSliceBuilder -- temporal gating contract
"""

from __future__ import annotations

from typing import Any

try:
    from .prefix_slice_builder import (
        REDACTED_FIELDS,
        _FUTURE_LEAK_FIELDS,
        _Phase,
        extract_gold_action_from_event,
    )
    from .action_dsl import is_pointer_resolvable
except ImportError:
    # Inline definitions when prefix_slice_builder is not yet available
    # (e.g. worktree that predates PFX-001).  These MUST stay in sync
    # with the canonical definitions in prefix_slice_builder.py.

    REDACTED_FIELDS: frozenset[str] = frozenset({  # type: ignore[no-redef]
        "source_domain",
        "prompt_id",
        "api_key",
        "api_key_env",
        "api_base",
        "provider",
        "raw_llm_response",
        "raw_text",
        "usage",
        "model",
        "temperature",
        "max_tokens",
        "reasoning_effort",
    })

    _FUTURE_LEAK_FIELDS: frozenset[str] = frozenset({  # type: ignore[no-redef]
        "updated_profile",
        "promotion_transitions",
        "soundness",
        "backward_traces",
    })

    class _Phase:  # type: ignore[no-redef]
        STRUCTURING = 1
        FORMALIZATION = 2
        EVIDENCE = 3

        @staticmethod
        def of_event(event: dict[str, Any]) -> int:
            etype = str(event.get("event_type") or event.get("type") or "").lower()
            phase_hint = event.get("phase")
            if phase_hint is not None:
                try:
                    return int(phase_hint)
                except (ValueError, TypeError):
                    pass
            if any(k in etype for k in ("structur", "ingest", "planner", "claim_graph")):
                return 1
            if any(k in etype for k in ("formal", "audit", "profile", "verif")):
                return 2
            if any(k in etype for k in ("evidence", "research", "dev_agent", "promot", "execution")):
                return 3
            return 1

    def extract_gold_action_from_event(  # type: ignore[no-redef]
        event: dict[str, Any], *, is_last_step: bool = False,
    ) -> dict[str, Any] | None:
        if is_last_step:
            return {"action": "DONE", "action_type": "DONE", "arguments": {}}
        return event.get("action") or event.get("gold_action")

    def is_pointer_resolvable(  # type: ignore[no-redef]
        gold_action: dict[str, Any] | None,
        visible_node_ids: set[str],
    ) -> bool:
        """Fallback pointer resolution check."""
        if gold_action is None:
            return True
        return True  # Conservative fallback


# ============================================================================
# Graph-specific banned fields (superset of text builder's banned fields)
# ============================================================================

_GRAPH_BANNED_FIELDS: frozenset[str] = REDACTED_FIELDS | frozenset({
    "source_domain",
    "domain",
})


# ============================================================================
# PrefixSliceGraphBuilder  (PFX-006)
# ============================================================================

class PrefixSliceGraphBuilder:
    """Builds PrefixSliceGraphV1 from trace data.

    Unlike PrefixSliceBuilder (text), this produces a graph representation
    suitable for GNN-based models.
    """

    def __init__(
        self,
        trace: dict[str, Any],
        transition_log: list[dict[str, Any]],
    ) -> None:
        """
        Args:
            trace: PipelineTraceV1 dict (already model-safe, no domain).
                   Expected keys: trace_id, source, claims, relations, gaps,
                   hidden_assumptions, artifacts.
            transition_log: list of pipeline-event dicts, each with at least:
                   step_id, event_type, and optionally phase, action, outcome,
                   artifacts_produced, claim_id.
        """
        self.trace = trace
        # B10/PFX-001: preserve input order (event_seq / file order).
        self.transition_log = self._order_by_event_seq(transition_log)
        self._action_mask_builder: Any = None

    def set_action_mask_builder(self, builder: Any) -> None:
        """Attach a LegalActionMaskBuilder for automatic mask computation."""
        self._action_mask_builder = builder

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_graph_slices(self) -> list[dict[str, Any]]:
        """Extract one PrefixSliceGraphV1 per controllable decision point.

        B10/PFX-001: Only controllable events become policy rows.
        Automatic consequences update later state but do not produce rows.

        B30/PTR-001: Policy rows are only exported when their gold_action
        IDs resolve in the visible state_graph nodes at that cutoff.

        Each slice contains:
        - state_graph: {nodes, edges}
        - available_artifacts: list of artifact IDs at this step
        - legal_action_mask: valid actions (passed through from event metadata)
        - gold_action: the action actually taken at this step
        """
        slices: list[dict[str, Any]] = []
        controllable_indices = self._controllable_event_indices()
        for rank, idx in enumerate(controllable_indices):
            event = self.transition_log[idx]
            step_id = str(event.get("step_id") or f"step_{idx}")
            is_last = rank == len(controllable_indices) - 1
            built_slice = self._build_graph_slice(idx, step_id, event, is_last_controllable=is_last)

            # B30/PTR-001: If gold_action IDs do not resolve in the visible
            # state_graph nodes, keep the row but mark it as omitted.
            gold_action = built_slice.get("gold_action")
            if gold_action is not None:
                visible_ids = self._extract_visible_node_ids(built_slice)
                if not is_pointer_resolvable(gold_action, visible_ids):
                    built_slice["omission_reason"] = "pointer_unresolved"
                    built_slice["gold_action"] = None

            slices.append(built_slice)
        return slices

    def _controllable_event_indices(self) -> list[int]:
        """Return indices of controllable events in self.transition_log.

        B10: Same logic as PrefixSliceBuilder._controllable_event_indices.
        """
        indices: list[int] = []
        for idx, event in enumerate(self.transition_log):
            ec = event.get("event_class") or ""
            if ec == "automatic_consequence":
                continue
            etype = str(event.get("event_type") or "").lower()
            if etype in ("profile_recomputed", "propagation_applied",
                         "gate_updated", "audit_completed"):
                continue
            indices.append(idx)
        return indices

    @staticmethod
    def _order_by_event_seq(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Order events by integer event_seq when present, else keep input order.

        B10/PFX-001: step_id is a label, NOT the ordering key.
        """
        has_seq = all(e.get("event_seq") is not None for e in events)
        if has_seq and events:
            return sorted(events, key=lambda e: int(e["event_seq"]))
        return list(events)

    def extract_graph_slice_at_step(self, step_id: str) -> dict[str, Any]:
        """Extract a single PrefixSliceGraphV1 at the given step."""
        for idx, event in enumerate(self.transition_log):
            if str(event.get("step_id") or "") == step_id:
                return self._build_graph_slice(idx, step_id, event)
        raise KeyError(f"step_id {step_id!r} not found in transition_log")

    # ------------------------------------------------------------------
    # Internal: full slice construction
    # ------------------------------------------------------------------

    def _resolve_trace_id(self) -> str:
        """Resolve trace_id from trace dict (PFX-001 parity).

        Uses the same resolution logic as PrefixSliceBuilder.
        """
        tid = self.trace.get("trace_id")
        if not tid:
            meta = self.trace.get("meta") or {}
            tid = meta.get("trace_id")
        tid_str = str(tid) if tid else ""
        if tid_str and tid_str not in ("", "placeholder", "PLACEHOLDER", "TBD", "TODO"):
            return tid_str
        import hashlib, json
        content_hash = hashlib.sha256(
            json.dumps(self.trace, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        return f"trace-{content_hash}"

    def _build_graph_slice(
        self,
        step_index: int,
        step_id: str,
        event: dict[str, Any],
        *,
        is_last_controllable: bool = False,
    ) -> dict[str, Any]:
        adjacency = self._build_adjacency_at_step(step_index)
        node_features = self._build_node_features_at_step(step_index)
        edge_features = self._build_edge_features_at_step(step_index)
        available = self._get_available_artifacts(step_index)
        trace_id = self._resolve_trace_id()

        # PFX-003: Extract gold_action from controllable events via DSL
        # B10: use is_last_controllable to determine DONE action
        is_last_step = is_last_controllable
        gold_action = extract_gold_action_from_event(
            event, is_last_step=is_last_step,
        )
        # Fall back to raw action dict if DSL extraction returns None
        if gold_action is None:
            raw_action = event.get("action") or event.get("gold_action")
            if raw_action is not None:
                gold_action = raw_action

        legal_mask = event.get("legal_action_mask")
        if legal_mask is None and self._action_mask_builder is not None:
            phase = event.get("phase", "phase1")
            claim_id = self._extract_claim_id(event)
            legal_mask = self._action_mask_builder.compute_mask(
                phase, claim_id=claim_id,
            )

        # Build PrefixSliceGraphV1 shape (CNT-005): state_graph contains
        # nodes and edges, NOT the old adjacency/node_features/edge_features
        # Collect audit data once (same source as profiles_map below)
        audit_data_for_nodes = self._collect_audit_data_up_to(step_index)

        # flat layout.
        nodes: list[dict[str, Any]] = []
        for cid, feats in node_features.items():
            raw_profile = feats.get("profile_summary") or {}
            gate = feats.get("gate") or None
            confidence = feats.get("confidence", 0.0)
            # Use audit_data directly for vector_scores so node.profile
            # stays coherent with state_graph.profiles (both from same source).
            claim_audit = audit_data_for_nodes.get(cid) or {}
            audit_vs = claim_audit.get("vector_scores") or raw_profile.get("vector_scores") or {}
            profile: dict[str, Any] | None = None
            if raw_profile or gate or audit_vs:
                vector_scores = {
                    "trust_base_integrity": float(audit_vs.get("trust_base_integrity",
                        confidence / 100.0 if confidence > 1 else float(confidence or 0))),
                    "intent_alignment": float(audit_vs.get("intent_alignment",
                        0.5 if gate and gate != "draft" else 0.0)),
                    "evidence_support": float(audit_vs.get("evidence_support", 0.0)),
                    "coverage": float(audit_vs.get("coverage", 0.0)),
                    "robustness": float(audit_vs.get("robustness", 0.0)),
                }
                profile = {
                    "gate": gate or claim_audit.get("gate") or raw_profile.get("gate"),
                    "formal_status": claim_audit.get("formal_status") or raw_profile.get("formal_status"),
                    "vector_scores": vector_scores,
                    "required_actions": list(
                        claim_audit.get("required_actions") or
                        claim_audit.get("blocking_issues") or
                        raw_profile.get("required_actions") or []
                    ),
                }
                if not confidence:
                    vs_vals = [v for v in vector_scores.values() if v]
                    confidence = sum(vs_vals) / max(len(vs_vals), 1) if vs_vals else 0.0
            # Include text from the claim for GraphLM readability
            claim_text = ""
            for claim in self._collect_claims_up_to(step_index):
                cid_check = str(claim.get("claim_id") or claim.get("id") or "")
                if cid_check == cid:
                    claim_text = str(claim.get("text") or claim.get("nl_statement") or claim.get("title") or "")
                    break
            nodes.append({
                "node_id": cid,
                "type": feats.get("type", "claim"),
                "text": claim_text,
                "role": feats.get("role", ""),
                "status": feats.get("status", ""),
                "gate": gate,
                "confidence": confidence,
                "lifecycle": feats.get("lifecycle", "proposed"),
                "profile": profile,
            })

        edges: list[dict[str, Any]] = []
        for eidx, edge in enumerate(adjacency):
            eid = list(edge_features.keys())[eidx] if eidx < len(edge_features) else f"e_{eidx}"
            ef = edge_features.get(eid, {})
            strength_val = edge.get("strength") or "unknown"
            edges.append({
                "edge_id": str(eid),
                "src": edge.get("source", ""),
                "tgt": edge.get("target", ""),
                "relation_type": edge.get("relation_type", ""),
                "strength": strength_val,
                "weight": 1.0 if not ef.get("is_hidden_assumption", False) else 0.5,
            })

        # PFX-004: omission_reason
        omission_reason = None
        if gold_action is None and not is_last_step:
            omission_reason = "not_requested"

        # Build profiles dict from audit data for state_graph,
        # including vector_scores for message-passing supervision.
        audit_data = self._collect_audit_data_up_to(step_index)
        profiles_map: dict[str, dict[str, Any]] = {}
        for pcid, pdata in audit_data.items():
            p_vs = pdata.get("vector_scores") or {}
            profiles_map[pcid] = {
                "gate": pdata.get("gate"),
                "formal_status": pdata.get("formal_status"),
                "vector_scores": {
                    "trust_base_integrity": p_vs.get("trust_base_integrity", 0.0),
                    "intent_alignment": p_vs.get("intent_alignment", 0.0),
                    "evidence_support": p_vs.get("evidence_support", 0.0),
                    "coverage": p_vs.get("coverage", 0.0),
                    "robustness": p_vs.get("robustness", 0.0),
                },
                "required_actions": list(pdata.get("required_actions") or
                                         pdata.get("blocking_issues") or []),
            }

        # Collect recent controllable event IDs (last 5 before cutoff)
        # Spec: list of event_id strings, not dicts.
        # v2-final: recent_events is a list of event summary objects
        recent_event_objs: list[dict[str, Any]] = []
        count = 0
        for ri in range(step_index - 1, -1, -1):
            if count >= 5:
                break
            rev = self.transition_log[ri]
            if rev.get("event_class") == "controllable_action":
                recent_event_objs.append({
                    "event_id": str(rev.get("event_id") or rev.get("step_id", "")),
                    "event_type": str(rev.get("event_type", "")),
                    "changed_ids": list(rev.get("changed_ids") or []),
                    "accepted": rev.get("accepted"),
                })
                count += 1
        recent_event_objs.reverse()

        return {
            "schema_version": "1.0.0",
            "trace_id": trace_id,
            "step_id": step_id,
            "state_graph": {
                "nodes": nodes,
                "edges": edges,
                "profiles": profiles_map,
                "open_gaps": [],
                "recent_events": recent_event_objs,
            },
            "available_artifacts": available,
            "legal_action_mask": legal_mask,
            "gold_action": gold_action,
            "omission_reason": omission_reason,
        }

    # ------------------------------------------------------------------
    # Adjacency list construction
    # ------------------------------------------------------------------

    def _build_adjacency_at_step(self, step_index: int) -> list[dict[str, Any]]:
        """Build claim graph adjacency list up to step.

        Returns list of edges, each: {source, target, relation_type, strength}.
        Only includes relations from the base trace and from events
        strictly before step_index, subject to phase gating.
        """
        relations = self._collect_relations_up_to(step_index)
        adjacency: list[dict[str, Any]] = []
        for rel in relations:
            adjacency.append({
                "source": rel.get("source_id") or rel.get("from_claim_id") or "",
                "target": rel.get("target_id") or rel.get("to_claim_id") or "",
                "relation_type": self._safe_str_value(
                    rel.get("relation_type") or rel.get("type") or "related_to"
                ),
                "strength": self._safe_str_value(
                    rel.get("strength") or "unknown"
                ),
            })
        return adjacency

    # ------------------------------------------------------------------
    # Node features
    # ------------------------------------------------------------------

    def _build_node_features_at_step(self, step_index: int) -> dict[str, dict[str, Any]]:
        """Build node feature dict up to step.

        Each node (claim) gets: {role, status, gate, profile_summary}.
        Domain-free: no source_domain in features.
        """
        claims = self._collect_claims_up_to(step_index)
        audit_data = self._collect_audit_data_up_to(step_index)

        features: dict[str, dict[str, Any]] = {}
        for claim in claims:
            cid = str(
                claim.get("claim_id")
                or claim.get("id")
                or ""
            )
            if not cid:
                continue

            role = self._safe_str_value(
                claim.get("role") or claim.get("claim_class") or ""
            )
            status = self._safe_str_value(
                claim.get("status") or ""
            )

            # Audit / profile info for this claim (if available from prior steps)
            claim_audit = audit_data.get(cid) or {}
            gate = self._safe_str_value(claim_audit.get("gate") or "")
            profile_summary = self._build_profile_summary(claim_audit)

            node_type = self._safe_str_value(
                claim.get("type") or claim.get("claim_class") or "claim"
            )
            confidence = claim.get("confidence", 0.0)
            lifecycle = self._safe_str_value(
                claim.get("lifecycle") or ("active" if gate else "proposed")
            )

            features[cid] = {
                "type": node_type,
                "role": role,
                "status": status,
                "gate": gate,
                "confidence": confidence,
                "lifecycle": lifecycle,
                "profile_summary": profile_summary,
            }

        return features

    # ------------------------------------------------------------------
    # Edge features
    # ------------------------------------------------------------------

    def _build_edge_features_at_step(self, step_index: int) -> dict[str, dict[str, Any]]:
        """Build edge feature dict up to step.

        Each edge gets: {relation_type, strength, is_hidden_assumption}.
        Domain-free: no source_domain in features.
        """
        relations = self._collect_relations_up_to(step_index)
        assumptions = self._collect_assumptions_up_to(step_index)

        # Build a set of (source, target) pairs from hidden assumptions
        assumption_pairs: set[tuple[str, str]] = set()
        for a in assumptions:
            attaches_to = a.get("attaches_to") or a.get("claim_id") or ""
            if attaches_to:
                assumption_pairs.add(("_hidden", attaches_to))

        features: dict[str, dict[str, Any]] = {}
        for i, rel in enumerate(relations):
            src = rel.get("source_id") or rel.get("from_claim_id") or ""
            tgt = rel.get("target_id") or rel.get("to_claim_id") or ""
            edge_id = rel.get("relation_id") or rel.get("id") or f"e_{i}"

            rtype = self._safe_str_value(
                rel.get("relation_type") or rel.get("type") or "related_to"
            )
            strength = self._safe_str_value(
                rel.get("strength") or "unknown"
            )

            # Check if this edge connects to a hidden assumption node
            is_hidden = rtype == "assumes" or (src, tgt) in assumption_pairs

            features[str(edge_id)] = {
                "relation_type": rtype,
                "strength": strength,
                "is_hidden_assumption": is_hidden,
            }

        return features

    # ------------------------------------------------------------------
    # State accumulation (mirrors PrefixSliceBuilder temporal gating)
    # ------------------------------------------------------------------

    def _collect_claims_up_to(self, step_index: int) -> list[dict[str, Any]]:
        """Collect all claims visible at step_index (prefix-only)."""
        current_phase = _Phase.STRUCTURING
        if step_index < len(self.transition_log):
            current_phase = _Phase.of_event(self.transition_log[step_index])

        base_claims = [
            self._strip_banned(c if isinstance(c, dict) else dict(c))
            for c in (self.trace.get("claims") or [])
        ]
        event_claims: list[dict[str, Any]] = []

        for i in range(step_index):
            prior = self.transition_log[i]
            prior_phase = _Phase.of_event(prior)
            if prior_phase > current_phase:
                continue
            outcome = prior.get("outcome") or {}
            for c in outcome.get("claims") or outcome.get("new_claims") or []:
                event_claims.append(
                    self._strip_banned(c if isinstance(c, dict) else dict(c))
                )

        return self._deduplicate(
            base_claims + event_claims,
            id_keys=("claim_id", "id"),
        )

    def _collect_relations_up_to(self, step_index: int) -> list[dict[str, Any]]:
        """Collect all relations visible at step_index (prefix-only)."""
        current_phase = _Phase.STRUCTURING
        if step_index < len(self.transition_log):
            current_phase = _Phase.of_event(self.transition_log[step_index])

        base_relations = [
            self._strip_banned(r if isinstance(r, dict) else dict(r))
            for r in (self.trace.get("relations") or [])
        ]
        event_relations: list[dict[str, Any]] = []

        for i in range(step_index):
            prior = self.transition_log[i]
            prior_phase = _Phase.of_event(prior)
            if prior_phase > current_phase:
                continue
            outcome = prior.get("outcome") or {}
            for r in outcome.get("relations") or outcome.get("new_relations") or []:
                event_relations.append(
                    self._strip_banned(r if isinstance(r, dict) else dict(r))
                )

        return self._deduplicate(
            base_relations + event_relations,
            id_keys=("relation_id", "id"),
        )

    def _collect_audit_data_up_to(self, step_index: int) -> dict[str, dict[str, Any]]:
        """Collect per-claim audit/profile data visible at step_index.

        Same gating rules as PrefixSliceBuilder:
        - Only from phase-2+ events strictly before step_index
        - Excludes audit data for the current step's target claim
        """
        current_phase = _Phase.STRUCTURING
        current_claim_id: str | None = None
        if step_index < len(self.transition_log):
            current_phase = _Phase.of_event(self.transition_log[step_index])
            current_claim_id = self._extract_claim_id(self.transition_log[step_index])

        audit_map: dict[str, dict[str, Any]] = {}

        for i in range(step_index):
            prior = self.transition_log[i]
            prior_phase = _Phase.of_event(prior)
            if prior_phase > current_phase:
                continue
            if prior_phase < _Phase.FORMALIZATION:
                continue

            prior_claim = self._extract_claim_id(prior)
            outcome = prior.get("outcome") or {}
            audit_info = outcome.get("audit") or outcome.get("profile") or {}
            # For promotion (phase 3), allow own claim's profile
            own_claim_ok = current_phase >= _Phase.EVIDENCE
            if audit_info and prior_claim and (prior_claim != current_claim_id or own_claim_ok):
                clean = self._strip_banned(dict(audit_info))
                for f in _FUTURE_LEAK_FIELDS:
                    clean.pop(f, None)
                audit_map[str(prior_claim)] = clean

        return audit_map

    def _collect_assumptions_up_to(self, step_index: int) -> list[dict[str, Any]]:
        """Collect hidden assumptions visible at step_index."""
        current_phase = _Phase.STRUCTURING
        if step_index < len(self.transition_log):
            current_phase = _Phase.of_event(self.transition_log[step_index])

        base = [
            self._strip_banned(a if isinstance(a, dict) else {"text": str(a)})
            for a in (self.trace.get("hidden_assumptions") or [])
        ]
        event_assumptions: list[dict[str, Any]] = []

        for i in range(step_index):
            prior = self.transition_log[i]
            prior_phase = _Phase.of_event(prior)
            if prior_phase > current_phase:
                continue
            outcome = prior.get("outcome") or {}
            for a in (
                outcome.get("hidden_assumptions")
                or outcome.get("new_hidden_assumptions")
                or []
            ):
                event_assumptions.append(
                    self._strip_banned(a if isinstance(a, dict) else {"text": str(a)})
                )

        return base + event_assumptions

    @staticmethod
    def _extract_claim_id(event: dict[str, Any]) -> str | None:
        """B30: Extract the target claim_id from an event.

        Same logic as PrefixSliceBuilder._extract_claim_id.
        """
        cid = event.get("claim_id")
        if cid:
            return str(cid)
        proposal = event.get("proposal") or {}
        if isinstance(proposal, dict):
            cid = proposal.get("claim_id")
            if cid:
                return str(cid)
        changed = event.get("changed_ids") or []
        if isinstance(changed, list) and changed:
            return str(changed[0])
        return None

    def _extract_visible_node_ids(self, built_slice: dict[str, Any]) -> set[str]:
        """B30/PTR-001: Extract visible node IDs from a built graph slice.

        Returns the set of node_ids present in the state_graph.nodes list.
        """
        ids: set[str] = set()
        state_graph = built_slice.get("state_graph") or {}
        for node in state_graph.get("nodes") or []:
            nid = node.get("node_id") or ""
            if nid:
                ids.add(str(nid))
        return ids

    def _get_available_artifacts(self, step_index: int) -> list[str]:
        """List artifact IDs available at this point (same as text builder)."""
        base_artifacts: list[str] = [
            str(a) for a in (self.trace.get("artifacts") or [])
        ]
        for i in range(step_index):
            prior = self.transition_log[i]
            produced = (
                prior.get("artifacts_produced")
                or prior.get("outcome", {}).get("artifacts_produced")
                or []
            )
            for aid in produced:
                aid_str = str(aid)
                if aid_str not in base_artifacts:
                    base_artifacts.append(aid_str)
        return base_artifacts

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_banned(d: dict[str, Any]) -> dict[str, Any]:
        """Remove banned / redacted / domain fields from a dict."""
        return {
            k: v for k, v in d.items()
            if k not in _GRAPH_BANNED_FIELDS and k not in _FUTURE_LEAK_FIELDS
        }

    @staticmethod
    def _safe_str_value(v: Any) -> str:
        """Coerce a value to string, handling enums with .value."""
        if hasattr(v, "value"):
            return str(v.value)
        return str(v)

    @staticmethod
    def _build_profile_summary(audit_data: dict[str, Any]) -> dict[str, Any]:
        """Extract profile summary from audit data including vector_scores."""
        summary: dict[str, Any] = {}
        for key in ("formal_status", "blocking_issues", "required_actions", "gate"):
            if key in audit_data:
                summary[key] = audit_data[key]
        # Include vector_scores for GraphState coherence
        vs = audit_data.get("vector_scores")
        if vs and isinstance(vs, dict):
            summary["vector_scores"] = vs
        return summary

    @staticmethod
    def _deduplicate(
        items: list[dict[str, Any]],
        id_keys: tuple[str, ...] = ("id",),
    ) -> list[dict[str, Any]]:
        """Deduplicate dicts by the first available id key, preserving order."""
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for item in items:
            item_id: str | None = None
            for key in id_keys:
                val = item.get(key)
                if val is not None:
                    item_id = str(val)
                    break
            if item_id is not None:
                if item_id in seen:
                    continue
                seen.add(item_id)
            result.append(item)
        return result
