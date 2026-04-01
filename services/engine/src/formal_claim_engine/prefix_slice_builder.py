"""PFX-001 / PFX-002 / PFX-003 / PFX-004 — PrefixSlice builder and canonical state-text serializer.

Extracts prefix-only training samples from PipelineTrace data at each
decision point.  The canonical state_text is a domain-free, human-readable
serialization that NEVER leaks future information.

PFX-001: Canonical PrefixSliceTextV1 builder with trace_id/step_id parity.
PFX-002: Consistent available_artifacts, legal_action_mask, gold_action.
PFX-003: Gold action DSL extraction from controllable events.
PFX-004: No-future leakage, omission_reason semantics.

Spec references
---------------
* OAE Output Spec v2 — PrefixSliceTextV1 schema
* packages/contracts/schemas/prefix-slice-text-v1.schema.json
"""

from __future__ import annotations

import re
from typing import Any

from .action_dsl import extract_gold_action_from_event, is_pointer_resolvable


# ---------------------------------------------------------------------------
# REDACTED / banned fields — MUST NEVER appear in state_text
# ---------------------------------------------------------------------------

REDACTED_FIELDS: frozenset[str] = frozenset({
    # Top-level forbidden (SAFE-001 v2 set)
    "source_domain",
    "prompt_id",
    "router_decision",
    "corpus_name",
    "split",
    "source_uri",
    "operator_notes",
    "license",
    # Infra forbidden
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

# Future-leak fields: these belong to *later* pipeline phases and must be
# excluded from the state snapshot at the current step.
_FUTURE_LEAK_FIELDS: frozenset[str] = frozenset({
    "updated_profile",
    "promotion_transitions",
    "soundness",
    "backward_traces",
})

# Module-level alias used by hardening tests
FUTURE_STEP_FIELDS: frozenset[str] = _FUTURE_LEAK_FIELDS

# Phase-to-field mapping for temporal gating validation (VRF-002)
PHASE_FIELDS: dict[str, list[str]] = {
    "structuring": ["claim_graph", "candidate_ledger", "structuring_workflow"],
    "formalization": ["dual_formalization", "build_results"],
    "verification": ["verifier_results"],
    "audit": ["audit", "trust_frontier", "model_health", "intent_alignment"],
    "profile": ["profile", "assurance_profile"],
    "promotion": ["promotion_transitions"],
    "evidence": ["updated_profile", "research_output"],
}


# ============================================================================
# Pipeline phase classification
# ============================================================================

class _Phase:
    STRUCTURING = 1   # Phase 1: claim extraction / structuring
    FORMALIZATION = 2  # Phase 2: formalization, audit, profiling
    EVIDENCE = 3       # Phase 3: evidence / execution, promotion

    @staticmethod
    def of_event(event: dict[str, Any]) -> int:
        """Infer the phase of a pipeline event from its type or metadata."""
        etype = str(event.get("event_type") or event.get("type") or "").lower()
        phase_hint = event.get("phase")
        if phase_hint is not None:
            try:
                return int(phase_hint)
            except (ValueError, TypeError):
                pass
        if any(k in etype for k in ("structur", "ingest", "planner", "claim_graph")):
            return _Phase.STRUCTURING
        if any(k in etype for k in ("formal", "audit", "profile", "verif")):
            return _Phase.FORMALIZATION
        if any(k in etype for k in ("evidence", "research", "dev_agent", "promot", "execution")):
            return _Phase.EVIDENCE
        return _Phase.STRUCTURING


# ============================================================================
# CanonicalStateSerializer  (PFX-002)
# ============================================================================

class CanonicalStateSerializer:
    """Serializes OAE pipeline state into domain-free canonical text.

    Output format is a structured text representation suitable for
    model input, **not** raw JSON.

    Sections (in order)
    --------------------
    1. [DOCUMENT]              -- source text
    2. [CURRENT CLAIMS]        -- claims with IDs and text
    3. [CURRENT RELATIONS]     -- relations between claims
    4. [CURRENT AUDIT / PROFILE] -- audit results and profile gate
    5. [OPEN GAPS]             -- detected gaps
    6. [HIDDEN ASSUMPTIONS]    -- surfaced hidden assumptions
    7. [FORMALIZATION STATUS]  -- if phase2 data available
    """

    BANNED_FIELDS: frozenset[str] = REDACTED_FIELDS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def serialize(self, state: dict[str, Any]) -> str:
        """Serialize pipeline state to canonical text format.

        PFX-001: state_text must never be empty.  If no sections are
        present, emit at least a minimal "[DOCUMENT]\\n(empty)" section.
        """
        sections: list[str] = []

        source = state.get("source") or state.get("document") or {}
        if source:
            sections.append(self._serialize_document(source))

        claims = state.get("claims") or []
        if claims:
            sections.append(self._serialize_claims(claims))

        relations = state.get("relations") or []
        if relations:
            sections.append(self._serialize_relations(relations))

        audit_data = state.get("audit") or state.get("profile") or {}
        if audit_data:
            sections.append(self._serialize_audit_profile(audit_data))

        # Per-claim profiles (for PROPOSE_PROMOTION visibility)
        profiles = state.get("profiles") or {}
        if profiles and not audit_data:
            # Use profiles as audit context when no single audit is set
            first_profile = next(iter(profiles.values()), {})
            if first_profile:
                sections.append(self._serialize_audit_profile(first_profile))
        elif profiles:
            # Add per-claim profile lines after the main audit section
            lines = ["[CLAIM PROFILES]"]
            for cid, prof in profiles.items():
                gate = prof.get("gate", "")
                formal = prof.get("formal_status", "")
                lines.append(f"{cid}: gate={gate}, formal_status={formal}")
            sections.append("\n".join(lines))

        gaps = state.get("gaps") or []
        if gaps:
            sections.append(self._serialize_gaps(gaps))

        assumptions = state.get("hidden_assumptions") or []
        if assumptions:
            sections.append(self._serialize_hidden_assumptions(assumptions))

        recent_actions = state.get("recent_actions") or []
        if recent_actions:
            lines = ["[RECENT ACTIONS]"]
            for action in recent_actions:
                lines.append(f"- {action}")
            sections.append("\n".join(lines))

        formalization = state.get("formalization") or {}
        if formalization:
            sections.append(self._serialize_formalization(formalization))

        # PFX-001: state_text must never be empty
        if not sections:
            sections.append("[DOCUMENT]\n(empty)")

        text = "\n\n".join(sections)
        violations = self.validate_no_leak(text)
        if violations:
            raise ValueError(
                f"state_text contains banned content: {violations}"
            )
        return text

    # ------------------------------------------------------------------
    # Section serializers
    # ------------------------------------------------------------------

    def _serialize_document(self, source: dict[str, Any]) -> str:
        lines = ["[DOCUMENT]"]
        title = source.get("title") or source.get("name") or ""
        if title:
            lines.append(f"Title: {title}")
        text = source.get("text") or source.get("content") or source.get("body") or ""
        if text:
            lines.append(text)
        return "\n".join(lines)

    def _serialize_claims(self, claims: list[dict[str, Any] | Any]) -> str:
        lines = ["[CURRENT CLAIMS]"]
        for c in claims:
            if isinstance(c, dict):
                cid = c.get("claim_id") or c.get("id") or "?"
                title = c.get("title") or ""
                stmt = c.get("nl_statement") or c.get("statement") or c.get("text") or ""
                status = c.get("status") or ""
                role = c.get("role") or c.get("claim_class") or ""
            else:
                # Pydantic model
                cid = getattr(c, "claim_id", None) or getattr(c, "id", "?")
                title = getattr(c, "title", "")
                stmt = getattr(c, "nl_statement", None) or getattr(c, "statement", "")
                status = getattr(c, "status", "")
                role = getattr(c, "role", None) or getattr(c, "claim_class", "")
            status_str = status.value if hasattr(status, "value") else str(status)
            role_str = role.value if hasattr(role, "value") else str(role)
            entry = f"{cid}: {title}"
            if stmt:
                entry += f" -- {stmt}"
            meta_parts: list[str] = []
            if status_str:
                meta_parts.append(f"status={status_str}")
            if role_str:
                meta_parts.append(f"role={role_str}")
            if meta_parts:
                entry += f" ({', '.join(meta_parts)})"
            lines.append(entry)
        return "\n".join(lines)

    def _serialize_relations(self, relations: list[dict[str, Any] | Any]) -> str:
        lines = ["[CURRENT RELATIONS]"]
        for r in relations:
            if isinstance(r, dict):
                src = r.get("source_id") or r.get("from_claim_id") or "?"
                tgt = r.get("target_id") or r.get("to_claim_id") or "?"
                rtype = r.get("relation_type") or r.get("type") or "related_to"
                strength = r.get("strength") or ""
                rationale = r.get("rationale") or ""
            else:
                src = getattr(r, "source_id", None) or getattr(r, "from_claim_id", "?")
                tgt = getattr(r, "target_id", None) or getattr(r, "to_claim_id", "?")
                rtype = getattr(r, "relation_type", "related_to")
                strength = getattr(r, "strength", "")
                rationale = getattr(r, "rationale", "")
            rtype_str = rtype.value if hasattr(rtype, "value") else str(rtype)
            strength_str = strength.value if hasattr(strength, "value") else str(strength)
            entry = f"{src} {rtype_str} {tgt}"
            if strength_str:
                entry += f" (strength={strength_str})"
            if rationale:
                entry += f" -- {rationale}"
            lines.append(entry)
        return "\n".join(lines)

    def _serialize_audit_profile(self, audit_data: dict[str, Any]) -> str:
        lines = ["[CURRENT AUDIT / PROFILE]"]
        claim_id = audit_data.get("claim_id") or ""
        gate = audit_data.get("gate") or ""
        if hasattr(gate, "value"):
            gate = gate.value
        if claim_id and gate:
            lines.append(f"{claim_id} gate={gate}")
        elif gate:
            lines.append(f"gate={gate}")

        blocking = audit_data.get("blocking_issues") or audit_data.get("required_actions") or []
        if blocking:
            items = ", ".join(str(b) for b in blocking)
            cid_prefix = f"{claim_id} " if claim_id else ""
            lines.append(f"{cid_prefix}blocking_issues=[{items}]")

        formal_status = audit_data.get("formal_status") or ""
        if hasattr(formal_status, "value"):
            formal_status = formal_status.value
        if formal_status:
            lines.append(f"formal_status={formal_status}")

        return "\n".join(lines)

    def _serialize_gaps(self, gaps: list[dict[str, Any] | Any]) -> str:
        lines = ["[OPEN GAPS]"]
        for g in gaps:
            if isinstance(g, dict):
                gid = g.get("id") or g.get("gap_id") or "?"
                kind = g.get("kind") or ""
                desc = g.get("description") or ""
                severity = g.get("severity") or ""
                affected = g.get("affected_claim_ids") or []
            else:
                gid = getattr(g, "id", "?")
                kind = getattr(g, "kind", "")
                desc = getattr(g, "description", "")
                severity = getattr(g, "severity", "")
                affected = getattr(g, "affected_claim_ids", [])
            entry = f"{gid}: [{kind}] {desc}"
            if severity:
                entry += f" (severity={severity})"
            if affected:
                entry += f" affects=[{', '.join(str(a) for a in affected)}]"
            lines.append(entry)
        return "\n".join(lines)

    def _serialize_hidden_assumptions(self, assumptions: list[dict[str, Any] | Any]) -> str:
        lines = ["[HIDDEN ASSUMPTIONS]"]
        for a in assumptions:
            if isinstance(a, dict):
                text = a.get("text") or a.get("statement") or str(a)
                attaches = a.get("attaches_to") or a.get("claim_id") or ""
            else:
                text = getattr(a, "text", None) or getattr(a, "statement", str(a))
                attaches = getattr(a, "attaches_to", None) or getattr(a, "claim_id", "")
            entry = f"- {text}"
            if attaches:
                entry += f" (attaches_to={attaches})"
            lines.append(entry)
        return "\n".join(lines)

    def _serialize_formalization(self, formalization: dict[str, Any]) -> str:
        lines = ["[FORMALIZATION STATUS]"]
        for claim_id, data in formalization.items():
            if isinstance(data, dict):
                status = data.get("status") or data.get("formal_status") or "unknown"
                if hasattr(status, "value"):
                    status = status.value
                attempts = data.get("attempts") or data.get("attempt_count") or 0
                selected = data.get("selected") or data.get("selected_attempt") or ""
                entry = f"{claim_id}: status={status}"
                if attempts:
                    entry += f" attempts={attempts}"
                if selected:
                    entry += f" selected={selected}"
                lines.append(entry)
            else:
                lines.append(f"{claim_id}: {data}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Leak validation
    # ------------------------------------------------------------------

    def validate_no_leak(self, state_text: str) -> list[str]:
        """Check that state_text contains no banned fields or future data.

        Uses word-boundary-aware matching: multi-word fields (containing ``_``)
        are matched as exact substrings (they are specific enough to avoid
        false positives), while short single-word fields use ``\\b`` boundaries
        so that e.g. "model" in normal prose is not a false positive.

        Returns a list of violation descriptions (empty means clean).
        """
        violations: list[str] = []
        lower = state_text.lower()
        for field in self.BANNED_FIELDS:
            fl = field.lower()
            if "_" in fl:
                # Multi-word identifier -- substring match is safe
                if fl in lower:
                    violations.append(f"banned field '{field}' found in state_text")
            else:
                # Single-word -- require word boundaries to avoid false positives
                if re.search(rf"(?<![a-z_]){re.escape(fl)}(?![a-z_])", lower):
                    violations.append(f"banned field '{field}' found in state_text")
        for field in _FUTURE_LEAK_FIELDS:
            fl = field.lower()
            if fl in lower:
                violations.append(f"future-leak field '{field}' found in state_text")
        return violations


# ============================================================================
# PrefixSliceBuilder  (PFX-001)
# ============================================================================

class PrefixSliceBuilder:
    """Builds PrefixSliceV1 instances from PipelineTrace + transition_log.

    Extracts prefix-only training samples at each decision point in the trace.
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
                   hidden_assumptions, formalization, audit, artifacts.
            transition_log: list of pipeline-event dicts, each with at least:
                   step_id, event_type, and optionally phase, action, outcome,
                   artifacts_produced, claim_id.
        """
        self.trace = trace
        # B10/PFX-001: preserve input order (event_seq / file order).
        # Sort by integer event_seq when present; otherwise keep input order.
        # NEVER sort by step_id string.
        self.transition_log = self._order_by_event_seq(transition_log)
        self._serializer = CanonicalStateSerializer()
        self._action_mask_builder: Any = None
        self._safeslice_bridge: Any = None

    @staticmethod
    def _order_by_event_seq(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Order events by integer event_seq when present, else keep input order.

        B10/PFX-001: step_id is a label, NOT the ordering key.
        """
        has_seq = all(e.get("event_seq") is not None for e in events)
        if has_seq and events:
            return sorted(events, key=lambda e: int(e["event_seq"]))
        return list(events)

    def set_action_mask_builder(self, builder: Any) -> None:
        """Attach a LegalActionMaskBuilder for automatic mask computation."""
        self._action_mask_builder = builder

    def set_safeslice_bridge(self, bridge: Any) -> None:
        """Attach a SafeSliceBridge for post-extraction validation."""
        self._safeslice_bridge = bridge

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_slices(self) -> list[dict[str, Any]]:
        """Extract one PrefixSliceV1 per controllable decision point.

        B10/PFX-001: Only controllable events become policy rows.
        Automatic consequences update later state but do not produce rows.

        B30/PTR-001: Policy rows are only exported when their gold_action
        IDs resolve in the visible state at that cutoff.  Pointer-unresolved
        events remain in the transition_log and candidate_ledger but are
        not exported as policy prefix rows.

        For each controllable event at position idx:
        - state_text = canonical serialization of state from ALL events
          with index < idx (including automatic consequences)
        - available_artifacts = list of artifact IDs available at that point
        - gold_action = the action taken at the controllable event (for SFT)
        - legal_action_mask = valid actions at this state
        """
        slices: list[dict[str, Any]] = []
        controllable_indices = self._controllable_event_indices()
        for rank, idx in enumerate(controllable_indices):
            event = self.transition_log[idx]
            step_id = str(event.get("step_id") or f"step_{idx}")
            is_last = rank == len(controllable_indices) - 1
            built_slice = self._build_slice(idx, step_id, event, is_last_controllable=is_last)

            # B30/PTR-001: If gold_action IDs do not resolve in the visible
            # state, keep the row but mark it as omitted so parity is preserved.
            gold_action = built_slice.get("gold_action")
            if gold_action is not None:
                state = self._build_state_up_to(idx)
                visible_ids = self._extract_visible_node_ids(state)
                if not is_pointer_resolvable(gold_action, visible_ids):
                    built_slice["omission_reason"] = "pointer_unresolved"
                    built_slice["gold_action"] = None  # not usable for policy training
                    # Still emit the row so trace-to-prefix parity is maintained

            # Post-extraction validation via SafeSliceBridge
            if self._safeslice_bridge is not None:
                violations = self._safeslice_bridge.validate_slice(built_slice)
                if violations:
                    raise ValueError(
                        f"SafeSlice validation failed at {step_id}: {violations}"
                    )

            slices.append(built_slice)
        return slices

    def _controllable_event_indices(self) -> list[int]:
        """Return indices of controllable events in self.transition_log.

        B10: Controllable events are those with event_class == 'controllable_action'
        or those not classified as automatic consequences.
        """
        indices: list[int] = []
        for idx, event in enumerate(self.transition_log):
            ec = event.get("event_class") or ""
            if ec == "automatic_consequence":
                continue
            # Also check event_type against known automatic types
            etype = str(event.get("event_type") or "").lower()
            if etype in ("profile_recomputed", "propagation_applied",
                         "gate_updated", "audit_completed"):
                continue
            indices.append(idx)
        return indices

    def extract_slice_at_step(self, step_id: str) -> dict[str, Any]:
        """Extract a single PrefixSliceV1 at the given step."""
        for idx, event in enumerate(self.transition_log):
            if str(event.get("step_id") or "") == step_id:
                return self._build_slice(idx, step_id, event)
        raise KeyError(f"step_id {step_id!r} not found in transition_log")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_trace_id(self) -> str:
        """Resolve trace_id from trace dict (PFX-001).

        Looks at trace["trace_id"], then trace["meta"]["trace_id"].
        Never returns empty string or placeholder values.
        """
        tid = self.trace.get("trace_id")
        if not tid:
            meta = self.trace.get("meta") or {}
            tid = meta.get("trace_id")
        tid_str = str(tid) if tid else ""
        if tid_str and tid_str not in ("", "placeholder", "PLACEHOLDER", "TBD", "TODO"):
            return tid_str
        # Fallback: generate a deterministic id from the trace content
        import hashlib, json
        content_hash = hashlib.sha256(
            json.dumps(self.trace, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        return f"trace-{content_hash}"

    def _build_slice(
        self,
        step_index: int,
        step_id: str,
        event: dict[str, Any],
        *,
        is_last_controllable: bool = False,
    ) -> dict[str, Any]:
        state = self._build_state_up_to(step_index)
        state_text = self._serializer.serialize(state)
        available = self._get_available_artifacts(step_index)
        trace_id = self._resolve_trace_id()

        # PFX-003: Extract gold_action from controllable events via DSL
        # B10: use is_last_controllable to determine DONE action
        is_last_step = is_last_controllable
        # Synthetic claim-creation events have _no_gold_action flag
        if event.get("_no_gold_action"):
            gold_action = None
        else:
            gold_action = extract_gold_action_from_event(
                event, is_last_step=is_last_step,
            )
            # Fall back to raw action dict if DSL extraction returns None
            if gold_action is None:
                raw_action = event.get("action") or event.get("gold_action")
                if raw_action is not None:
                    gold_action = raw_action

        # Compute legal_action_mask: prefer LegalActionMaskBuilder if attached,
        # otherwise fall back to event metadata.
        legal_mask = None
        if self._action_mask_builder is not None:
            phase = _Phase.of_event(event)
            phase_name = {
                _Phase.STRUCTURING: "phase1",
                _Phase.FORMALIZATION: "phase2",
                _Phase.EVIDENCE: "phase3",
            }.get(phase, "phase1")
            claim_id = event.get("claim_id")
            legal_mask = self._action_mask_builder.compute_mask(
                phase_name, claim_id=claim_id
            )
        if legal_mask is None:
            legal_mask = event.get("legal_action_mask")

        # PFX-004: omission_reason — set when canonical fields are
        # intentionally absent from this projection
        omission_reason = None
        if gold_action is None and not is_last_step:
            # No gold action and not end of trace: it was unavailable
            omission_reason = "not_requested"

        return {
            "schema_version": "1.0.0",
            "trace_id": trace_id,
            "step_id": step_id,
            "state_text": state_text,
            "available_artifacts": available,
            "legal_action_mask": legal_mask,
            "gold_action": gold_action,
            "omission_reason": omission_reason,
        }

    def _build_state_up_to(self, step_index: int) -> dict[str, Any]:
        """Build the state snapshot including all events strictly before step_index.

        Temporal ordering rules:
        - At phase-1 steps: NO phase-2 or phase-3 data in state
        - At phase-2 steps for claim X: NO phase-2 results for claim X itself
          (only prior claims that have already completed phase 2)
        - At phase-3 steps: NO soundness or backward_traces from future
        - Never include: updated_profile, promotion_transitions, soundness,
          backward_traces that come from events at or after step_index
        """
        current_phase = _Phase.STRUCTURING
        if step_index < len(self.transition_log):
            current_phase = _Phase.of_event(self.transition_log[step_index])

        current_claim_id: str | None = None
        if step_index < len(self.transition_log):
            current_claim_id = self._extract_claim_id(self.transition_log[step_index])

        # Accumulate completed prior events
        completed_claims: list[dict[str, Any]] = []
        completed_relations: list[dict[str, Any]] = []
        completed_gaps: list[dict[str, Any]] = []
        completed_assumptions: list[dict[str, Any]] = []
        completed_audit: dict[str, Any] = {}
        completed_profiles: dict[str, dict[str, Any]] = {}  # per-claim profiles
        completed_formalization: dict[str, Any] = {}
        source = self._strip_banned_fields(
            dict(self.trace.get("source") or self.trace.get("document") or {})
        )

        # Base state from trace (phase-1 data is always available)
        base_claims = self.trace.get("claims") or []
        base_relations = self.trace.get("relations") or []
        base_gaps = self.trace.get("gaps") or []
        base_assumptions = self.trace.get("hidden_assumptions") or []

        # Walk prior events and accumulate their contributions
        for i in range(step_index):
            prior = self.transition_log[i]
            prior_phase = _Phase.of_event(prior)
            outcome = prior.get("outcome") or {}

            # Phase gating: skip future-phase data
            if prior_phase > current_phase:
                continue

            # Accumulate claims produced by prior steps
            new_claims = outcome.get("claims") or outcome.get("new_claims") or []
            for c in new_claims:
                c_clean = self._strip_banned_fields(
                    c if isinstance(c, dict) else dict(c)
                )
                completed_claims.append(c_clean)

            # Accumulate relations
            new_rels = outcome.get("relations") or outcome.get("new_relations") or []
            for r in new_rels:
                r_clean = self._strip_banned_fields(
                    r if isinstance(r, dict) else dict(r)
                )
                completed_relations.append(r_clean)

            # Accumulate gaps
            new_gaps = outcome.get("gaps") or outcome.get("new_gaps") or []
            for g in new_gaps:
                g_clean = self._strip_banned_fields(
                    g if isinstance(g, dict) else dict(g)
                )
                completed_gaps.append(g_clean)

            # Accumulate hidden assumptions
            new_assumptions = (
                outcome.get("hidden_assumptions")
                or outcome.get("new_hidden_assumptions")
                or []
            )
            for a in new_assumptions:
                a_clean = self._strip_banned_fields(
                    a if isinstance(a, dict) else {"text": str(a)}
                )
                completed_assumptions.append(a_clean)

            # Audit / profile data -- from phase-2+ events.
            # For phase-2 steps (formalization/audit): exclude own claim's data
            # For phase-3 steps (promotion): include own claim's data (needed
            # to decide whether to promote)
            if prior_phase >= _Phase.FORMALIZATION:
                prior_claim = self._extract_claim_id(prior)
                audit_info = outcome.get("audit") or outcome.get("profile") or {}
                # Allow own-claim profile when the current step is promotion (phase 3)
                own_claim_ok = current_phase >= _Phase.EVIDENCE
                if audit_info and (prior_claim != current_claim_id or own_claim_ok):
                    audit_clean = self._strip_banned_fields(dict(audit_info))
                    for f in _FUTURE_LEAK_FIELDS:
                        audit_clean.pop(f, None)
                    completed_audit = audit_clean
                    if prior_claim:
                        completed_profiles[str(prior_claim)] = audit_clean

            # Formalization data
            if prior_phase >= _Phase.FORMALIZATION:
                prior_claim = self._extract_claim_id(prior)
                form_info = outcome.get("formalization") or {}
                if form_info and prior_claim != current_claim_id:
                    form_clean = self._strip_banned_fields(dict(form_info))
                    if prior_claim:
                        completed_formalization[str(prior_claim)] = form_clean

        # Merge base trace data (phase-1) with accumulated event data
        all_claims = self._deduplicate_by_id(
            [self._strip_banned_fields(c if isinstance(c, dict) else dict(c)) for c in base_claims]
            + completed_claims,
            id_keys=("claim_id", "id"),
        )
        all_relations = self._deduplicate_by_id(
            [self._strip_banned_fields(r if isinstance(r, dict) else dict(r)) for r in base_relations]
            + completed_relations,
            id_keys=("relation_id", "id"),
        )
        all_gaps = self._deduplicate_by_id(
            [self._strip_banned_fields(g if isinstance(g, dict) else dict(g)) for g in base_gaps]
            + completed_gaps,
            id_keys=("gap_id", "id"),
        )
        all_assumptions = (
            [self._strip_banned_fields(a if isinstance(a, dict) else {"text": str(a)}) for a in base_assumptions]
            + completed_assumptions
        )

        # Build the state dict
        state: dict[str, Any] = {}
        if source:
            state["source"] = source
        if all_claims:
            state["claims"] = all_claims
        if all_relations:
            state["relations"] = all_relations
        if completed_audit:
            state["audit"] = completed_audit
        if all_gaps:
            state["gaps"] = all_gaps
        if all_assumptions:
            state["hidden_assumptions"] = all_assumptions
        # Include per-claim profiles so PROPOSE_PROMOTION sees gate/profile
        if completed_profiles and not completed_audit:
            # Use the most recent profile as the global audit context
            completed_audit = next(iter(completed_profiles.values()))
        if completed_profiles:
            # Add all per-claim profiles to state for richer serialization
            state["profiles"] = completed_profiles
        if completed_formalization:
            state["formalization"] = completed_formalization

        # Add recent controllable events to break state_text aliasing.
        # Without this, multiple different gold_actions map to the same
        # state_text because the state doesn't change between them.
        recent: list[str] = []
        count = 0
        for ri in range(step_index - 1, -1, -1):
            if count >= 3:
                break
            rev = self.transition_log[ri]
            if rev.get("event_class") == "controllable_action":
                eid = rev.get("event_id") or rev.get("step_id", "")
                etype = rev.get("event_type", "")
                recent.append(f"{etype}({eid})")
                count += 1
        if recent:
            recent.reverse()
            state["recent_actions"] = recent

        return state

    @staticmethod
    def _extract_claim_id(event: dict[str, Any]) -> str | None:
        """B30: Extract the target claim_id from an event.

        Transition log events may have claim_id as a top-level field,
        inside the proposal, or in changed_ids.  This helper resolves
        claim_id from all known locations.
        """
        # Top-level claim_id
        cid = event.get("claim_id")
        if cid:
            return str(cid)
        # From proposal
        proposal = event.get("proposal") or {}
        if isinstance(proposal, dict):
            cid = proposal.get("claim_id")
            if cid:
                return str(cid)
        # From changed_ids (first entry)
        changed = event.get("changed_ids") or []
        if isinstance(changed, list) and changed:
            return str(changed[0])
        return None

    def _extract_visible_node_ids(self, state: dict[str, Any]) -> set[str]:
        """B30/PTR-001: Extract all visible node IDs from a state snapshot.

        Returns a set of claim/node IDs that are present in the visible
        state (claims list) at the given cutoff.  This is used by the
        pointer-resolution filter to decide whether a gold_action's
        referenced IDs are resolvable.
        """
        ids: set[str] = set()
        for c in state.get("claims") or []:
            if isinstance(c, dict):
                cid = c.get("claim_id") or c.get("id") or ""
            else:
                cid = getattr(c, "claim_id", None) or getattr(c, "id", "")
            if cid:
                ids.add(str(cid))
        return ids

    def _get_available_artifacts(self, step_index: int) -> list[str]:
        """List artifact IDs available at this point in the trace."""
        # Start with base trace artifacts
        base_artifacts: list[str] = [
            str(a) for a in (self.trace.get("artifacts") or [])
        ]
        # Add artifacts produced by all prior completed events
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

    def _strip_banned_fields(self, d: dict[str, Any]) -> dict[str, Any]:
        """Remove banned / redacted fields from a dict."""
        return {
            k: v for k, v in d.items()
            if k not in REDACTED_FIELDS and k not in _FUTURE_LEAK_FIELDS
        }

    @staticmethod
    def _deduplicate_by_id(
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
