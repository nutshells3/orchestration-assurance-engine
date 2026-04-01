"""Normalizes engine mutations into PipelineEventV1 events with before/after state hashes.

TRC-009: Adds event_class (controllable_action / automatic_consequence) and
cause_event_id for causal linking of automatic consequences to their triggers.

EVT-001..004 (bundle-04): Frozen vocabularies for event_type, actor, and
event_class. Legacy event type names are mapped to the canonical enum.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


V2_EVENT_TYPES = frozenset({
    "propose_relation",
    "add_hidden_assumption",
    "request_recheck",
    "select_formalization",
    "finalize_profile",
    "propose_promotion",
    "profile_recomputed",
    "propagation_applied",
    "gate_updated",
    "audit_completed",
})

V2_ACTORS = frozenset({
    "planner",
    "formalizer",
    "auditor",
    "system",
    "human",
})

V2_EVENT_CLASSES = frozenset({
    "controllable_action",
    "automatic_consequence",
})

V2_PHASES = frozenset({
    "phase1",
    "phase2",
    "phase3",
    "trace",
})

V2_CONTROLLABLE_EVENT_TYPES = frozenset({
    "propose_relation",
    "add_hidden_assumption",
    "request_recheck",
    "select_formalization",
    "finalize_profile",
    "propose_promotion",
})

V2_AUTOMATIC_EVENT_TYPES = frozenset({
    "profile_recomputed",
    "propagation_applied",
    "gate_updated",
    "audit_completed",
})

LEGACY_EVENT_TYPE_TO_V2: dict[str, str] = {
    "propose_relation": "propose_relation",
    "relation_proposal": "propose_relation",
    "add_hidden_assumption": "add_hidden_assumption",
    "hidden_assumption": "add_hidden_assumption",
    "request_recheck": "request_recheck",
    "recheck_request": "request_recheck",
    "select_formalization": "select_formalization",
    "formalization_selection": "select_formalization",
    "formalization_attempt": "select_formalization",
    "dual_formalization_workflow": "select_formalization",
    "finalize_profile": "finalize_profile",
    "profile_finalization": "finalize_profile",
    "audit_workflow": "finalize_profile",
    "propose_promotion": "propose_promotion",
    "promotion_transition": "propose_promotion",
    "promotion_proposal": "propose_promotion",
    "profile_recomputed": "profile_recomputed",
    "profile_recompute": "profile_recomputed",
    "gate_updated": "gate_updated",
    "gate_update": "gate_updated",
    "propagation_applied": "propagation_applied",
    "propagation_update": "propagation_applied",
    "audit_completed": "audit_completed",
}

LEGACY_ACTOR_TO_V2: dict[str, str] = {
    "planner": "planner",
    "formalizer": "formalizer",
    "formalizer_a": "formalizer",
    "formalizer_b": "formalizer",
    "auditor": "auditor",
    "reviewer": "human",
    "human_reviewer": "human",
    "human": "human",
    "system": "system",
    "orchestrator": "system",
    "engine_api": "system",
    "policy_engine": "system",
    "claim_graph_agent": "planner",
}

LEGACY_PHASE_TO_V2: dict[str, str] = {
    "phase1": "phase1",
    "structuring": "phase1",
    "formalization": "phase2",
    "phase2": "phase2",
    "audit": "phase2",
    "profile": "phase2",
    "promotion": "phase2",
    "phase3": "phase3",
    "trace": "trace",
    "trace_forward": "trace",
    "trace_backward": "trace",
    "find_gaps": "trace",
}


def normalize_event_type(raw: str) -> str:
    return LEGACY_EVENT_TYPE_TO_V2.get(raw, "propose_relation")


def normalize_actor(raw: str) -> str:
    return LEGACY_ACTOR_TO_V2.get(raw, "system")


def normalize_phase(raw: str) -> str:
    return LEGACY_PHASE_TO_V2.get(raw, "phase2")


def classify_event(event_type_v2: str) -> str:
    if event_type_v2 in V2_AUTOMATIC_EVENT_TYPES:
        return "automatic_consequence"
    return "controllable_action"


# ---------------------------------------------------------------------------
# B40/SAFE-002: reject_reason sanitization
# ---------------------------------------------------------------------------
# Known runtime error patterns that leak provider/model/tool/session info
# into model-visible output.  These are matched case-insensitively against
# the raw reject_reason text.  Matches cause the reason to be replaced with
# a concise, model-safe code.  The original raw text is returned separately
# so the caller can route it to operator-only metadata.
#
# IMPORTANT: These patterns target *known runtime diagnostic shapes* only.
# They must not be a naive global blacklist that could damage legitimate
# source text or user-authored content.

_RUNTIME_LEAK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Provider names
    (re.compile(r"\bopenai\b", re.IGNORECASE), "provider_error"),
    (re.compile(r"\banthropic\b", re.IGNORECASE), "provider_error"),
    (re.compile(r"\bazure\b", re.IGNORECASE), "provider_error"),
    (re.compile(r"\bcohere\b", re.IGNORECASE), "provider_error"),
    (re.compile(r"\bmistral\b", re.IGNORECASE), "provider_error"),
    (re.compile(r"\bdeepseek\b", re.IGNORECASE), "provider_error"),
    # Model identifiers
    (re.compile(r"\bgpt-\d", re.IGNORECASE), "model_error"),
    (re.compile(r"\bgpt4\b", re.IGNORECASE), "model_error"),
    (re.compile(r"\bclaude[\s-]", re.IGNORECASE), "model_error"),
    (re.compile(r"\bo[134]-", re.IGNORECASE), "model_error"),
    (re.compile(r"\bgemini[\s-]", re.IGNORECASE), "model_error"),
    # Codex / tool runtime references
    (re.compile(r"\bcodex\b", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\bsession[\s_-]?id\b", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\bapi[\s_-]?key\b", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\bapi[\s_-]?base\b", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\brate[\s_-]?limit", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\btoken[\s_-]?limit", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\bmax[\s_-]?tokens?\b", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\btemperature\b", re.IGNORECASE), "runtime_error"),
    # Raw HTTP / stack trace / OS diagnostics
    (re.compile(r"\bHTTP\s+\d{3}\b"), "runtime_error"),
    (re.compile(r"\bTraceback\b"), "runtime_error"),
    (re.compile(r"\[WinError\s+\d+\]"), "runtime_error"),
    (re.compile(r"\[Errno\s+\d+\]"), "runtime_error"),
    (re.compile(r"The system cannot find", re.IGNORECASE), "runtime_error"),
    (re.compile(r"FileNotFoundError", re.IGNORECASE), "runtime_error"),
    (re.compile(r"PermissionError", re.IGNORECASE), "runtime_error"),
    (re.compile(r"OSError", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\bstatus[\s_]?code\b", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\brequest_id\b", re.IGNORECASE), "runtime_error"),
    (re.compile(r"\bx-request-id\b", re.IGNORECASE), "runtime_error"),
]


def sanitize_reject_reason(
    raw_reason: str | None,
) -> tuple[str | None, str | None]:
    """Normalize a reject_reason for model-visible output.

    Returns ``(sanitized_reason, raw_diagnostics)``.

    - If the raw reason is None or empty, returns ``(None, None)``.
    - If the raw reason contains known runtime/provider/model/tool leak
      patterns, replaces it with a concise code and returns the original
      text as ``raw_diagnostics`` (for operator-only storage).
    - Otherwise, returns the original reason unchanged (no raw diagnostics).

    This function targets *specific known patterns* rather than a naive
    global blacklist to avoid corrupting legitimate source text or
    user-authored content.
    """
    if raw_reason is None or not str(raw_reason).strip():
        return (None, None)

    reason_str = str(raw_reason).strip()
    for pattern, safe_code in _RUNTIME_LEAK_PATTERNS:
        if pattern.search(reason_str):
            return (safe_code, reason_str)

    return (reason_str, None)


CONTROLLABLE_EVENTS = frozenset({
    "add_hidden_assumption",
    "relation_proposal",
    "propose_relation",
    "promotion_transition",
    "promotion_proposal",
    "recheck_request",
    "formalization_selection",
    "propose_promotion",
    "select_formalization",
})

AUTOMATIC_EVENTS = frozenset({
    "profile_finalization",
    "finalize_profile",
    "profile_recomputed",
    "profile_recompute",
    "propagation_applied",
    "gate_updated",
})


class StateHasher:
    """Deterministic SHA-256 hash of arbitrary state dicts (truncated to 16 hex)."""

    @staticmethod
    def compute_state_hash(state: Any) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    compute = compute_state_hash

    @staticmethod
    def hash_claim_graph(claim_graph: Any) -> str:
        return StateHasher.compute_state_hash(claim_graph)

    @staticmethod
    def hash_combined_pipeline(**kwargs: Any) -> str:
        return StateHasher.compute_state_hash(kwargs)


class PipelineEventV1(BaseModel):
    """Canonical event envelope for mutation capture."""

    schema_version: str = "PipelineEventV1"
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    trace_id: str
    step_id: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    phase: str
    event_type: str
    event_class: str = "controllable_action"
    actor: str
    before_hash: str
    after_hash: str
    cause_event_id: str | None = None
    proposal: dict[str, Any] = Field(default_factory=dict)
    accepted: bool | None = None
    reject_reason: str | None = None
    changed_ids: list[str] = Field(default_factory=list)
    verifier_delta: dict[str, Any] = Field(default_factory=dict)
    step: int = 0

    def __getitem__(self, key: str) -> Any:
        if key == "schema":
            return self.schema_version
        return getattr(self, key)


class EventNormalizer:
    """Accumulates PipelineEventV1 events for a single trace session."""

    compute_state_hash = staticmethod(StateHasher.compute_state_hash)

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self._step = 0
        self.events: list[PipelineEventV1] = []

    def get_events(self) -> list[PipelineEventV1]:
        return list(self.events)

    def _next_step(self) -> int:
        self._step += 1
        return self._step

    def _emit(
        self,
        *,
        phase: str,
        event_type: str,
        actor: str,
        before_state: Any,
        after_state: Any,
        event_class: str = "controllable_action",
        cause_event_id: str | None = None,
        proposal: dict[str, Any] | None = None,
        accepted: bool | None = None,
        reject_reason: str | None = None,
        changed_ids: list[str] | None = None,
        verifier_delta: dict[str, Any] | None = None,
    ) -> PipelineEventV1:
        step_num = self._next_step()
        deterministic_event_id = f"{self.trace_id}.evt.{step_num:04d}"
        effective_changed_ids = changed_ids or []
        if accepted is False:
            effective_changed_ids = []
        event = PipelineEventV1(
            event_id=deterministic_event_id,
            trace_id=self.trace_id,
            step=step_num,
            step_id=f"step-{step_num:04d}",
            phase=phase,
            event_type=event_type,
            event_class=event_class,
            actor=actor,
            before_hash=StateHasher.compute_state_hash(before_state),
            after_hash=StateHasher.compute_state_hash(after_state),
            cause_event_id=cause_event_id,
            proposal=proposal or {},
            accepted=accepted,
            reject_reason=reject_reason,
            changed_ids=effective_changed_ids,
            verifier_delta=verifier_delta if verifier_delta else {},
        )
        self.events.append(event)
        return event

    def emit_consequence(
        self,
        *,
        cause: PipelineEventV1,
        phase: str,
        event_type: str,
        actor: str = "system",
        before_state: Any,
        after_state: Any,
        changed_ids: list[str] | None = None,
        verifier_delta: dict[str, Any] | None = None,
    ) -> PipelineEventV1:
        return self._emit(
            phase=phase,
            event_type=event_type,
            event_class="automatic_consequence",
            cause_event_id=cause.event_id,
            actor=actor,
            before_state=before_state,
            after_state=after_state,
            changed_ids=changed_ids,
            verifier_delta=verifier_delta,
        )

    def record_hidden_assumption(
        self,
        phase: str,
        actor: str,
        before_state: Any,
        after_state: Any,
        assumption_text: str,
        attaches_to: str,
        accepted: bool = True,
        reject_reason: str | None = None,
        changed_ids: list[str] | None = None,
        verifier_delta: dict[str, Any] | None = None,
    ) -> PipelineEventV1:
        return self._emit(
            phase=phase,
            event_type="add_hidden_assumption",
            actor=actor,
            before_state=before_state,
            after_state=after_state,
            proposal={"assumption_text": assumption_text, "attaches_to": attaches_to},
            accepted=accepted,
            reject_reason=reject_reason,
            changed_ids=changed_ids,
            verifier_delta=verifier_delta,
        )

    def record_relation_proposal(
        self,
        phase: str,
        actor: str,
        before_state: Any,
        after_state: Any,
        proposal: dict[str, Any] | None = None,
        accepted: bool = True,
        reject_reason: str | None = None,
        changed_ids: list[str] | None = None,
        verifier_delta: dict[str, Any] | None = None,
    ) -> PipelineEventV1:
        return self._emit(
            phase=phase,
            event_type="relation_proposal",
            actor=actor,
            before_state=before_state,
            after_state=after_state,
            proposal=proposal or {},
            accepted=accepted,
            reject_reason=reject_reason,
            changed_ids=changed_ids,
            verifier_delta=verifier_delta,
        )

    def record_promotion_proposal(
        self,
        actor: str,
        before_state: Any,
        after_state: Any,
        claim_id: str,
        target_gate: str,
        accepted: bool = True,
        reject_reason: str | None = None,
        verifier_delta: dict[str, Any] | None = None,
    ) -> PipelineEventV1:
        return self._emit(
            phase="promotion",
            event_type="promotion_transition",
            actor=actor,
            before_state=before_state,
            after_state=after_state,
            proposal={"claim_id": claim_id, "target_gate": target_gate},
            accepted=accepted,
            reject_reason=reject_reason,
            changed_ids=[claim_id] if accepted else [],
            verifier_delta=verifier_delta,
        )

    def record_recheck_request(
        self,
        phase: str,
        actor: str,
        before_state: Any,
        after_state: Any,
        claim_id: str,
        verifier_delta: dict[str, Any] | None = None,
    ) -> PipelineEventV1:
        return self._emit(
            phase=phase,
            event_type="recheck_request",
            actor=actor,
            before_state=before_state,
            after_state=after_state,
            proposal={"claim_id": claim_id},
            accepted=True,
            reject_reason=None,
            changed_ids=[claim_id],
            verifier_delta=verifier_delta,
        )

    def record_formalization_selection(
        self,
        actor: str,
        before_state: Any,
        after_state: Any,
        claim_id: str,
        attempt: str | int,
        accepted: bool = True,
        reject_reason: str | None = None,
        verifier_delta: dict[str, Any] | None = None,
    ) -> PipelineEventV1:
        return self._emit(
            phase="formalization",
            event_type="formalization_selection",
            actor=actor,
            before_state=before_state,
            after_state=after_state,
            proposal={"claim_id": claim_id, "attempt": attempt},
            accepted=accepted,
            reject_reason=reject_reason,
            changed_ids=[claim_id] if accepted else [],
            verifier_delta=verifier_delta,
        )

    def record_profile_finalization(
        self,
        actor: str,
        before_state: Any,
        after_state: Any,
        claim_id: str,
        verifier_delta: dict[str, Any] | None = None,
    ) -> PipelineEventV1:
        return self._emit(
            phase="profile",
            event_type="profile_finalization",
            actor=actor,
            before_state=before_state,
            after_state=after_state,
            proposal={"claim_id": claim_id},
            accepted=True,
            reject_reason=None,
            changed_ids=[claim_id],
            verifier_delta=verifier_delta,
        )

    def record_generic_event(
        self,
        phase: str,
        event_type: str,
        actor: str,
        before_state: Any,
        after_state: Any,
        proposal: dict[str, Any] | None = None,
        accepted: bool | None = None,
        reject_reason: str | None = None,
        changed_ids: list[str] | None = None,
        verifier_delta: dict[str, Any] | None = None,
        event_class: str = "controllable_action",
        cause_event_id: str | None = None,
    ) -> PipelineEventV1:
        return self._emit(
            phase=phase,
            event_type=event_type,
            event_class=event_class,
            cause_event_id=cause_event_id,
            actor=actor,
            before_state=before_state,
            after_state=after_state,
            proposal=proposal,
            accepted=accepted,
            reject_reason=reject_reason,
            changed_ids=changed_ids,
            verifier_delta=verifier_delta,
        )

    def normalize_review_event(
        self,
        review_event: dict[str, Any],
        claim_id: str,
        idx: int = 0,
    ) -> PipelineEventV1:
        meta = review_event.get("metadata") or {}
        raw_event_type = review_event.get("event_type", "review")
        v2_event_type = normalize_event_type(raw_event_type)
        v2_event_class = classify_event(v2_event_type)
        v2_actor = normalize_actor(review_event.get("actor", "system"))
        v2_phase = normalize_phase(meta.get("phase", "phase2"))
        before_state = meta.get("before_hash") or {
            "claim_id": claim_id,
            "event_idx": idx,
            "phase": "before",
            "event_type": raw_event_type,
        }
        after_state = meta.get("after_hash") or {
            "claim_id": claim_id,
            "event_idx": idx,
            "phase": "after",
            "event_type": raw_event_type,
            "actor": review_event.get("actor", ""),
        }
        accepted = meta.get("accepted")
        if accepted is None:
            accepted = meta.get("reject_reason") in (None, "")
        # B40/SAFE-002: Sanitize reject_reason before model-visible output.
        safe_reason, _raw_diagnostics = sanitize_reject_reason(
            meta.get("reject_reason"),
        )
        return self._emit(
            phase=v2_phase,
            event_type=v2_event_type,
            event_class=v2_event_class,
            actor=v2_actor,
            before_state=before_state,
            after_state=after_state,
            proposal=meta.get("proposal"),
            accepted=bool(accepted),
            reject_reason=safe_reason,
            changed_ids=[claim_id],
            verifier_delta=meta.get("verifier_delta"),
        )
