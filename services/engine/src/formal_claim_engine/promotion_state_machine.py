"""Explicit promotion state machine layered over deterministic assurance profiles."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .event_normalizer import EventNormalizer
from .models import AssuranceProfile, Gate
from .store import ArtifactStore, canonical_artifact_id, now_utc


LINEAR_PROMOTION_GATES = [
    Gate.draft,
    Gate.queued,
    Gate.research_only,
    Gate.dev_guarded,
    Gate.certified,
]
TERMINAL_PROMOTION_GATES = {
    Gate.blocked,
    Gate.rejected,
    Gate.superseded,
}


class ReviewActorRole(str, Enum):
    author = "author"
    reviewer = "reviewer"
    certifier = "certifier"
    operator = "operator"
    system = "system"


class PromotionStateError(ValueError):
    """Raised when a requested promotion transition violates policy."""


class PromotionTransition(BaseModel):
    event_id: str | None = None
    from_gate: Gate
    to_gate: Gate
    actor: str
    actor_role: ReviewActorRole
    override: bool = False
    rationale: str = ""
    notes: str = ""
    created_at: str = Field(default_factory=lambda: now_utc().isoformat())
    profile_id: str
    profile_revision_id: str | None = None
    recommended_gate: Gate


class PromotionCheckpointState(BaseModel):
    claim_id: str
    profile_id: str
    profile_revision_id: str | None = None
    recommended_gate: Gate
    current_gate: Gate = Gate.draft
    required_actions: list[str] = Field(default_factory=list)
    transitions: list[PromotionTransition] = Field(default_factory=list)


def _coerce_gate(value: Gate | str) -> Gate:
    if isinstance(value, Gate):
        return value
    return Gate(str(value))


def _linear_rank(gate: Gate) -> int:
    return LINEAR_PROMOTION_GATES.index(gate)


def _normal_promotion_ceiling(recommended_gate: Gate) -> int:
    if recommended_gate in TERMINAL_PROMOTION_GATES:
        return _linear_rank(Gate.draft)
    if recommended_gate in LINEAR_PROMOTION_GATES:
        return _linear_rank(recommended_gate)
    return _linear_rank(Gate.draft)


class PromotionStateMachine:
    """Persists human review checkpoints without mutating the deterministic profile gate."""

    def __init__(self, store: ArtifactStore, event_normalizer: EventNormalizer | None = None):
        self.store = store
        self._event_normalizer = event_normalizer

    def load_state(
        self,
        profile: AssuranceProfile | dict,
    ) -> PromotionCheckpointState:
        profile_model = (
            profile
            if isinstance(profile, AssuranceProfile)
            else AssuranceProfile.model_validate(profile)
        )
        claim_id = canonical_artifact_id(profile_model.claim_id)
        profile_id = canonical_artifact_id(profile_model.profile_id)
        recommended_gate = _coerce_gate(profile_model.gate)
        current_revision = self._current_profile_revision(profile_id)

        transitions: list[PromotionTransition] = []
        current_gate = Gate.draft
        for event in self.store.query_review_events(claim_id):
            if event.get("event_type") != "promotion_transition":
                continue
            metadata = dict(event.get("metadata") or {})
            if canonical_artifact_id(metadata.get("profile_id", "")) != profile_id:
                continue
            event_revision = metadata.get("profile_revision_id")
            if current_revision and event_revision and event_revision != current_revision:
                continue
            transition = PromotionTransition(
                event_id=event["event_id"],
                from_gate=_coerce_gate(metadata["from_gate"]),
                to_gate=_coerce_gate(metadata["to_gate"]),
                actor=str(event["actor"]),
                actor_role=ReviewActorRole(
                    str(event.get("actor_role") or metadata.get("actor_role") or "reviewer")
                ),
                override=bool(metadata.get("override")),
                rationale=str(metadata.get("rationale") or ""),
                notes=str(event.get("notes") or ""),
                created_at=str(event["created_at"]),
                profile_id=profile_id,
                profile_revision_id=event_revision or current_revision,
                recommended_gate=_coerce_gate(
                    metadata.get("recommended_gate", recommended_gate.value)
                ),
            )
            transitions.append(transition)
            current_gate = transition.to_gate

        return PromotionCheckpointState(
            claim_id=claim_id,
            profile_id=profile_id,
            profile_revision_id=current_revision,
            recommended_gate=recommended_gate,
            current_gate=current_gate,
            required_actions=[
                str(item) for item in list(profile_model.required_actions or [])
            ],
            transitions=transitions,
        )

    @staticmethod
    def _snapshot_promotion_state(state: PromotionCheckpointState) -> dict:
        """Produce a hashable snapshot of the promotion checkpoint."""
        return state.model_dump(mode="json")

    def transition(
        self,
        profile: AssuranceProfile | dict,
        *,
        target_gate: Gate | str,
        actor: str,
        actor_role: ReviewActorRole | str,
        override: bool = False,
        rationale: str = "",
        notes: str = "",
    ) -> PromotionCheckpointState:
        profile_model = (
            profile
            if isinstance(profile, AssuranceProfile)
            else AssuranceProfile.model_validate(profile)
        )
        state = self.load_state(profile_model)
        requested_gate = _coerce_gate(target_gate)
        normalized_role = (
            actor_role if isinstance(actor_role, ReviewActorRole) else ReviewActorRole(str(actor_role))
        )
        rationale_text = str(rationale or "").strip()
        notes_text = str(notes or "").strip()

        before_state = self._snapshot_promotion_state(state)

        # --- validation gates (capture rejections before re-raising) ---
        try:
            self._validate_structural_transition(state.current_gate, requested_gate)
        except PromotionStateError as exc:
            self._record_rejection(
                actor=actor,
                before_state=before_state,
                claim_id=state.claim_id,
                target_gate=requested_gate.value,
                reject_reason=str(exc),
            )
            raise

        override_required = self._override_required(
            recommended_gate=state.recommended_gate,
            target_gate=requested_gate,
        )
        if override_required and not override:
            reject_msg = (
                f"Transition {state.current_gate.value} -> {requested_gate.value} "
                f"requires override because recommended gate is {state.recommended_gate.value}."
            )
            self._record_rejection(
                actor=actor,
                before_state=before_state,
                claim_id=state.claim_id,
                target_gate=requested_gate.value,
                reject_reason=reject_msg,
            )
            raise PromotionStateError(reject_msg)

        if override and not rationale_text:
            reject_msg = "Override transitions require a non-empty rationale."
            self._record_rejection(
                actor=actor,
                before_state=before_state,
                claim_id=state.claim_id,
                target_gate=requested_gate.value,
                reject_reason=reject_msg,
            )
            raise PromotionStateError(reject_msg)

        if requested_gate == Gate.certified:
            try:
                self._validate_certifier(actor=actor, actor_role=normalized_role, claim_id=state.claim_id)
            except PromotionStateError as exc:
                self._record_rejection(
                    actor=actor,
                    before_state=before_state,
                    claim_id=state.claim_id,
                    target_gate=requested_gate.value,
                    reject_reason=str(exc),
                )
                raise

        event = self.store.append_review_event(
            target_claim_id=state.claim_id,
            artifact_kind="assurance_profiles",
            artifact_id=state.profile_id,
            event_type="promotion_transition",
            actor=actor,
            actor_role=normalized_role.value,
            notes=notes_text or rationale_text,
            metadata={
                "profile_id": state.profile_id,
                "profile_revision_id": state.profile_revision_id,
                "from_gate": state.current_gate.value,
                "to_gate": requested_gate.value,
                "recommended_gate": state.recommended_gate.value,
                "override": bool(override),
                "rationale": rationale_text,
                "actor_role": normalized_role.value,
                "required_actions": list(state.required_actions),
                "proposal": {
                    "claim_id": state.claim_id,
                    "target_gate": requested_gate.value,
                },
                "accepted": True,
                "verifier_delta": {
                    "legality": True,
                    "vector_score_delta": None,
                    "vector_score_delta_unavailable_reason": "runtime_not_captured",
                    "gate_before": state.current_gate.value,
                    "gate_after": requested_gate.value,
                    "hidden_assumptions_added": [],
                    "profile_recomputed": False,
                },
            },
        )
        updated = self.load_state(profile_model)
        if not updated.transitions or updated.transitions[-1].event_id != event["event_id"]:
            raise PromotionStateError("Promotion transition journal verification failed.")

        # Record accepted promotion event
        if self._event_normalizer is not None:
            after_state = self._snapshot_promotion_state(updated)
            self._event_normalizer.record_promotion_proposal(
                actor=actor,
                before_state=before_state,
                after_state=after_state,
                claim_id=state.claim_id,
                target_gate=requested_gate.value,
                accepted=True,
            )

        return updated

    def _record_rejection(
        self,
        *,
        actor: str,
        before_state: dict,
        claim_id: str,
        target_gate: str,
        reject_reason: str,
    ) -> None:
        """Record a rejected promotion event if an EventNormalizer is available."""
        if self._event_normalizer is not None:
            self._event_normalizer.record_promotion_proposal(
                actor=actor,
                before_state=before_state,
                after_state=before_state,  # state unchanged on rejection
                claim_id=claim_id,
                target_gate=target_gate,
                accepted=False,
                reject_reason=reject_reason,
            )

    def _current_profile_revision(self, profile_id: str) -> str | None:
        revisions = self.store.list_revisions("assurance_profiles", profile_id)
        if not revisions:
            return None
        return str(revisions[-1]["revision_id"])

    def _validate_structural_transition(self, current_gate: Gate, target_gate: Gate) -> None:
        if current_gate in TERMINAL_PROMOTION_GATES:
            raise PromotionStateError(
                f"Cannot transition from terminal gate {current_gate.value}."
            )
        if current_gate == target_gate:
            raise PromotionStateError(
                f"Promotion already at gate {current_gate.value}."
            )
        if target_gate in TERMINAL_PROMOTION_GATES:
            return
        if current_gate not in LINEAR_PROMOTION_GATES or target_gate not in LINEAR_PROMOTION_GATES:
            raise PromotionStateError(
                f"Unsupported promotion transition {current_gate.value} -> {target_gate.value}."
            )
        if _linear_rank(target_gate) != _linear_rank(current_gate) + 1:
            raise PromotionStateError(
                f"Invalid promotion transition {current_gate.value} -> {target_gate.value}; "
                "linear checkpoints may not be skipped."
            )

    def _override_required(self, *, recommended_gate: Gate, target_gate: Gate) -> bool:
        if target_gate in TERMINAL_PROMOTION_GATES:
            return False
        return _linear_rank(target_gate) > _normal_promotion_ceiling(recommended_gate)

    def _validate_certifier(
        self,
        *,
        actor: str,
        actor_role: ReviewActorRole,
        claim_id: str,
    ) -> None:
        if actor_role != ReviewActorRole.certifier:
            raise PromotionStateError(
                "Transitions into certified require actor_role=certifier."
            )
        for event in self.store.query_review_events(claim_id):
            prior_role = str(event.get("actor_role") or "")
            if event.get("actor") != actor:
                continue
            if prior_role == ReviewActorRole.author.value:
                raise PromotionStateError(
                    f"Actor {actor!r} cannot certify a claim they previously authored."
                )
