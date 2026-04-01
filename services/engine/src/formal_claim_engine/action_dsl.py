"""PFX-003 — Action DSL and Legal Action Mask builder.

Defines the canonical Action DSL verbs for OAE training and derives
legal_action_mask from the current pipeline state using verifier rules
and the promotion FSM.

Spec references
---------------
* OAE Output Spec v1 -- Action DSL section
* PromotionStateMachine: LINEAR_PROMOTION_GATES, TERMINAL_PROMOTION_GATES
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .promotion_state_machine import (
    LINEAR_PROMOTION_GATES,
    TERMINAL_PROMOTION_GATES,
)
from .models import Gate


# ============================================================================
# Action DSL
# ============================================================================


class ActionVerb(str, Enum):
    """Canonical action verbs for the OAE training Action DSL."""

    PROPOSE_RELATION = "PROPOSE_RELATION"
    ADD_HIDDEN_ASSUMPTION = "ADD_HIDDEN_ASSUMPTION"
    REQUEST_RECHECK = "REQUEST_RECHECK"
    SELECT_FORMALIZATION = "SELECT_FORMALIZATION"
    FINALIZE_PROFILE = "FINALIZE_PROFILE"
    PROPOSE_PROMOTION = "PROPOSE_PROMOTION"


# Phase-to-verbs mapping: which verbs are potentially available in each phase.
_PHASE_VERBS: dict[str, list[ActionVerb]] = {
    "phase1": [
        ActionVerb.PROPOSE_RELATION,
        ActionVerb.ADD_HIDDEN_ASSUMPTION,
    ],
    "phase2": [
        ActionVerb.SELECT_FORMALIZATION,
        ActionVerb.REQUEST_RECHECK,
        ActionVerb.FINALIZE_PROFILE,
    ],
    "phase3": [
        ActionVerb.PROPOSE_PROMOTION,
        ActionVerb.REQUEST_RECHECK,
    ],
    "trace": [
        ActionVerb.PROPOSE_PROMOTION,
        ActionVerb.REQUEST_RECHECK,
    ],
}


class ActionTemplate:
    """A legal action available at a given state."""

    def __init__(self, verb: ActionVerb, params: dict[str, Any]) -> None:
        self.verb = verb
        self.params = dict(params)

    def to_dsl_string(self) -> str:
        """Format as DSL text, e.g. PROPOSE_RELATION(c1, derives, c2, deductive)."""
        if self.params:
            parts = ", ".join(str(v) for v in self.params.values())
            return f"{self.verb.value}({parts})"
        return f"{self.verb.value}()"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the frozen ActionDSL object shape."""
        return action_object(self.verb.value, self.params)

    def __repr__(self) -> str:
        return f"ActionTemplate({self.to_dsl_string()!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ActionTemplate):
            return NotImplemented
        return self.verb == other.verb and self.params == other.params

    def __hash__(self) -> int:
        return hash((self.verb, tuple(sorted(self.params.items()))))


# ============================================================================
# Legal Action Mask Builder
# ============================================================================


def _coerce_gate(value: Gate | str) -> Gate:
    """Convert a string to a Gate enum value."""
    if isinstance(value, Gate):
        return value
    return Gate(str(value))


def _linear_rank(gate: Gate) -> int:
    """Return the rank of a gate in LINEAR_PROMOTION_GATES."""
    return LINEAR_PROMOTION_GATES.index(gate)


class LegalActionMaskBuilder:
    """Derives legal_action_mask from current pipeline state.

    Uses verifier rules and promotion FSM to determine which actions
    are legal at the current state.
    """

    def __init__(
        self,
        claim_graph: dict[str, Any],
        profiles: dict[str, Any],
        promotion_states: dict[str, Any],
    ) -> None:
        """
        Args:
            claim_graph: ClaimGraph dict with claims, relations, etc.
            profiles: Map of claim_id -> AssuranceProfile-like dict
                      (must include at least 'gate', 'formal_status',
                      'required_actions').
            promotion_states: Map of claim_id -> current promotion state dict
                              with at least 'current_gate' and 'recommended_gate'.
        """
        self.claim_graph = claim_graph
        self.profiles = profiles
        self.promotion_states = promotion_states

    def compute_mask(
        self,
        phase: str,
        claim_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Compute legal actions at current state.

        Phase determines which actions are available:
        - phase1: PROPOSE_RELATION, ADD_HIDDEN_ASSUMPTION
        - phase2: SELECT_FORMALIZATION, REQUEST_RECHECK, FINALIZE_PROFILE
        - phase3/trace: PROPOSE_PROMOTION, REQUEST_RECHECK

        Promotion legality comes from PromotionStateMachine rules:
        - Only next gate in LINEAR_PROMOTION_GATES is legal
        - Terminal gates block further promotion
        - Override-required transitions are marked
        """
        templates: list[ActionTemplate] = []
        allowed_verbs = _PHASE_VERBS.get(phase, [])

        for verb in allowed_verbs:
            if verb == ActionVerb.PROPOSE_RELATION:
                templates.extend(self._get_legal_relations())
            elif verb == ActionVerb.ADD_HIDDEN_ASSUMPTION:
                templates.append(
                    ActionTemplate(
                        ActionVerb.ADD_HIDDEN_ASSUMPTION,
                        {"text": "<text>", "attaches_to": "<claim_id>"},
                    )
                )
            elif verb == ActionVerb.REQUEST_RECHECK:
                if claim_id:
                    templates.append(
                        ActionTemplate(
                            ActionVerb.REQUEST_RECHECK,
                            {"claim_id": claim_id},
                        )
                    )
                else:
                    # All claims are recheckable
                    for cid in self._get_claim_ids():
                        templates.append(
                            ActionTemplate(
                                ActionVerb.REQUEST_RECHECK,
                                {"claim_id": cid},
                            )
                        )
            elif verb == ActionVerb.SELECT_FORMALIZATION:
                if claim_id:
                    templates.extend(self._get_legal_formalizations(claim_id))
                else:
                    for cid in self._get_claim_ids():
                        templates.extend(self._get_legal_formalizations(cid))
            elif verb == ActionVerb.FINALIZE_PROFILE:
                if claim_id:
                    templates.append(
                        ActionTemplate(
                            ActionVerb.FINALIZE_PROFILE,
                            {"claim_id": claim_id},
                        )
                    )
                else:
                    for cid in self._get_claim_ids():
                        templates.append(
                            ActionTemplate(
                                ActionVerb.FINALIZE_PROFILE,
                                {"claim_id": cid},
                            )
                        )
            elif verb == ActionVerb.PROPOSE_PROMOTION:
                if claim_id:
                    templates.extend(self._get_legal_promotions(claim_id))
                else:
                    for cid in self._get_claim_ids():
                        templates.extend(self._get_legal_promotions(cid))

        return [t.to_dict() for t in templates]

    # ------------------------------------------------------------------
    # Promotion FSM integration
    # ------------------------------------------------------------------

    def _get_legal_promotions(self, claim_id: str) -> list[ActionTemplate]:
        """Check promotion FSM for legal next gates.

        Rules (mirroring PromotionStateMachine._validate_structural_transition):
        - If current_gate is terminal -> no promotions allowed
        - If current_gate is in LINEAR_PROMOTION_GATES -> only next gate is legal
        - If target would exceed recommended_gate ceiling -> mark override_required
        """
        state = self.promotion_states.get(claim_id)
        if state is None:
            # No promotion state: assume draft, only queued is legal
            return [
                ActionTemplate(
                    ActionVerb.PROPOSE_PROMOTION,
                    {"claim_id": claim_id, "target_gate": Gate.queued.value},
                )
            ]

        current_gate = _coerce_gate(
            state.get("current_gate", Gate.draft.value)
        )
        recommended_gate = _coerce_gate(
            state.get("recommended_gate", Gate.draft.value)
        )

        # Terminal gates block all further promotion
        if current_gate in TERMINAL_PROMOTION_GATES:
            return []

        # Current gate must be in the linear sequence
        if current_gate not in LINEAR_PROMOTION_GATES:
            return []

        current_rank = _linear_rank(current_gate)
        templates: list[ActionTemplate] = []

        # Only the immediate next linear gate is a legal structural transition
        if current_rank + 1 < len(LINEAR_PROMOTION_GATES):
            next_gate = LINEAR_PROMOTION_GATES[current_rank + 1]
            override_required = self._promotion_override_required(
                recommended_gate, next_gate
            )
            templates.append(
                ActionTemplate(
                    ActionVerb.PROPOSE_PROMOTION,
                    {
                        "claim_id": claim_id,
                        "target_gate": next_gate.value,
                        "override_required": override_required,
                    },
                )
            )

        # Terminal gates are always structurally legal transitions
        for terminal_gate in sorted(TERMINAL_PROMOTION_GATES, key=lambda g: g.value):
            templates.append(
                ActionTemplate(
                    ActionVerb.PROPOSE_PROMOTION,
                    {
                        "claim_id": claim_id,
                        "target_gate": terminal_gate.value,
                    },
                )
            )

        return templates

    def _promotion_override_required(
        self,
        recommended_gate: Gate,
        target_gate: Gate,
    ) -> bool:
        """Check if promotion to target_gate requires an override.

        Mirrors PromotionStateMachine._override_required:
        - Terminal targets never require override
        - Otherwise, override is needed if target exceeds the normal
          promotion ceiling (recommended_gate's rank).
        """
        if target_gate in TERMINAL_PROMOTION_GATES:
            return False
        if recommended_gate in TERMINAL_PROMOTION_GATES:
            # Recommended is terminal: any linear promotion above draft
            # requires override (ceiling is draft rank = 0).
            return _linear_rank(target_gate) > _linear_rank(Gate.draft)
        if recommended_gate in LINEAR_PROMOTION_GATES:
            return _linear_rank(target_gate) > _linear_rank(recommended_gate)
        return _linear_rank(target_gate) > _linear_rank(Gate.draft)

    # ------------------------------------------------------------------
    # Relation actions
    # ------------------------------------------------------------------

    def _get_legal_relations(self) -> list[ActionTemplate]:
        """All possible relation proposals (unconstrained in phase1).

        In phase1, any pair of claims can be connected with any relation
        type and strength. We return a single template-style action.
        """
        return [
            ActionTemplate(
                ActionVerb.PROPOSE_RELATION,
                {
                    "src_id": "<source_claim_id>",
                    "relation_type": "<relation_type>",
                    "tgt_id": "<target_claim_id>",
                    "strength": "<strength>",
                },
            )
        ]

    # ------------------------------------------------------------------
    # Formalization actions
    # ------------------------------------------------------------------

    def _get_legal_formalizations(self, claim_id: str) -> list[ActionTemplate]:
        """Available formalization selections for a claim.

        If the claim has a profile with formalization attempts, each
        attempt index is a legal selection. Otherwise, a generic
        selection template is returned.
        """
        profile = self.profiles.get(claim_id)
        if profile is None:
            return [
                ActionTemplate(
                    ActionVerb.SELECT_FORMALIZATION,
                    {"claim_id": claim_id, "attempt": "<attempt_label>"},
                )
            ]

        # Check for available formalization attempts
        formal_data = profile.get("formalization") or {}
        attempts = formal_data.get("attempts") or formal_data.get("attempt_count") or 0
        if isinstance(attempts, int) and attempts > 0:
            labels = ["a", "b"]
            return [
                ActionTemplate(
                    ActionVerb.SELECT_FORMALIZATION,
                    {
                        "claim_id": claim_id,
                        "attempt": labels[min(i, len(labels) - 1)],
                    },
                )
                for i in range(attempts)
            ]

        return [
            ActionTemplate(
                ActionVerb.SELECT_FORMALIZATION,
                {"claim_id": claim_id, "attempt": "<attempt_label>"},
            )
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_claim_ids(self) -> list[str]:
        """Extract claim IDs from the claim graph."""
        claims = self.claim_graph.get("claims") or []
        ids: list[str] = []
        for c in claims:
            if isinstance(c, dict):
                cid = c.get("claim_id") or c.get("id")
            else:
                cid = getattr(c, "claim_id", None) or getattr(c, "id", None)
            if cid:
                ids.append(str(cid))
        return ids


def is_pointer_resolvable(
    gold_action: dict[str, Any] | None,
    visible_node_ids: set[str],
) -> bool:
    """B30/PTR-001: Check whether a gold action's IDs resolve in the visible state.

    A policy prefix row is exportable only when ALL ids referenced by its
    gold action resolve in the visible state at that cutoff.

    Returns True when the action has no pointer references, or when all
    pointer references resolve.  Returns False when at least one id is
    unresolvable.
    """
    if gold_action is None:
        return True  # No action => nothing to resolve

    action_type = str(gold_action.get("action") or gold_action.get("action_type") or "")
    arguments = gold_action.get("arguments") or {}

    if action_type == "DONE":
        return True

    # For PROPOSE_RELATION: src_id and tgt_id must both be present and resolve
    if action_type == "PROPOSE_RELATION":
        src_id = str(arguments.get("src_id") or "")
        tgt_id = str(arguments.get("tgt_id") or "")
        # Empty src/tgt is unresolvable — malformed proposal
        if not src_id or not tgt_id:
            return False
        if src_id not in visible_node_ids:
            return False
        if tgt_id not in visible_node_ids:
            return False
        return True

    # For actions referencing a single claim_id
    if action_type in ("SELECT_FORMALIZATION", "FINALIZE_PROFILE",
                        "PROPOSE_PROMOTION", "REQUEST_RECHECK"):
        claim_id = str(arguments.get("claim_id") or "")
        if claim_id and claim_id not in visible_node_ids:
            return False
        return True

    # For ADD_HIDDEN_ASSUMPTION: attaches_to must resolve
    if action_type == "ADD_HIDDEN_ASSUMPTION":
        attaches_to = str(arguments.get("attaches_to") or "")
        if attaches_to and attaches_to not in visible_node_ids:
            return False
        return True

    # Unknown action types are conservatively accepted
    return True


__all__ = [
    "ActionVerb",
    "ActionTemplate",
    "LegalActionMaskBuilder",
    "action_object",
    "done_action",
    "extract_gold_action_from_event",
    "is_pointer_resolvable",
]


def action_object(action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    canonical_action = str(action)
    arguments = dict(arguments or {})
    if arguments:
        dsl = f"{canonical_action}({', '.join(str(v) for v in arguments.values())})"
    else:
        dsl = f"{canonical_action}()"
    return {
        "action": canonical_action,
        "action_type": canonical_action,
        "arguments": arguments,
        "dsl": dsl,
    }


def done_action() -> dict[str, Any]:
    return action_object("DONE", {})


_EVENT_TYPE_TO_ACTION: dict[str, str] = {
    "propose_relation": "PROPOSE_RELATION",
    "relation_proposal": "PROPOSE_RELATION",
    "add_hidden_assumption": "ADD_HIDDEN_ASSUMPTION",
    "hidden_assumption": "ADD_HIDDEN_ASSUMPTION",
    "request_recheck": "REQUEST_RECHECK",
    "recheck_request": "REQUEST_RECHECK",
    "select_formalization": "SELECT_FORMALIZATION",
    "formalization_selection": "SELECT_FORMALIZATION",
    "formalization_attempt": "SELECT_FORMALIZATION",
    "dual_formalization_workflow": "SELECT_FORMALIZATION",
    "finalize_profile": "FINALIZE_PROFILE",
    "profile_finalization": "FINALIZE_PROFILE",
    "audit_workflow": "FINALIZE_PROFILE",
    "propose_promotion": "PROPOSE_PROMOTION",
    "promotion_transition": "PROPOSE_PROMOTION",
    "promotion_proposal": "PROPOSE_PROMOTION",
}

_ACTION_ARGUMENT_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "PROPOSE_RELATION": {
        "src_id": ("src_id", "source_id", "from_claim_id", "src", "u"),
        "relation_type": ("relation_type", "type", "rel"),
        "tgt_id": ("tgt_id", "target_id", "to_claim_id", "tgt", "v"),
        "strength": ("strength",),
    },
    "ADD_HIDDEN_ASSUMPTION": {
        "text": ("text", "assumption_text", "statement"),
        "attaches_to": ("attaches_to", "claim_id", "target_claim_id"),
    },
    "REQUEST_RECHECK": {
        "claim_id": ("claim_id", "id"),
    },
    "SELECT_FORMALIZATION": {
        "claim_id": ("claim_id", "id"),
        "attempt": ("attempt", "attempt_index", "selected_attempt", "formalizer_label", "attempts"),
    },
    "FINALIZE_PROFILE": {
        "claim_id": ("claim_id", "id"),
    },
    "PROPOSE_PROMOTION": {
        "claim_id": ("claim_id", "id"),
        "target_gate": ("target_gate", "gate", "to_gate"),
    },
}


def _extract_argument(
    event: dict[str, Any],
    proposal: dict[str, Any],
    aliases: tuple[str, ...],
) -> Any:
    for alias in aliases:
        if alias in proposal:
            value = proposal[alias]
            if alias == "attempts" and isinstance(value, list) and value:
                return value[0]
            return value
        if alias in event:
            return event[alias]
    return None


def _normalize_attempt_label(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return {0: "a", 1: "b"}.get(value, str(value))
    text = str(value).strip().lower()
    if text in {"0", "a", "attempt_a"}:
        return "a"
    if text in {"1", "b", "attempt_b"}:
        return "b"
    return text or None


def extract_gold_action_from_event(
    event: dict[str, Any],
    *,
    is_last_step: bool = False,
) -> dict[str, Any] | None:
    if str(event.get("event_class") or "") == "automatic_consequence":
        if is_last_step:
            return done_action()
        return None

    event_type = str(event.get("event_type") or event.get("type") or "").strip().lower()
    action = _EVENT_TYPE_TO_ACTION.get(event_type)
    if action is None:
        if is_last_step:
            return done_action()
        return None

    proposal = event.get("proposal") or event.get("action") or {}
    if not isinstance(proposal, dict):
        proposal = {}
    nested = proposal.get("arguments") or proposal.get("args") or proposal.get("params") or proposal.get("parameters")
    if isinstance(nested, dict):
        proposal = {**proposal, **nested}

    # B20/AUD-007: Treat canonical proposal as the source of truth for gold_action.
    # Extract all required canonical arguments, enforcing completeness.
    arguments: dict[str, Any] = {}
    for canonical_key, aliases in _ACTION_ARGUMENT_ALIASES.get(action, {}).items():
        value = _extract_argument(event, proposal, aliases)
        if canonical_key == "attempt":
            value = _normalize_attempt_label(value)
        if value is not None:
            arguments[canonical_key] = value

    # B20: Default missing required fields per action family
    if action == "PROPOSE_RELATION":
        # AUD-006/AUD-007: strength must never be omitted; default to "unknown"
        raw_strength = arguments.get("strength")
        if raw_strength is None or (isinstance(raw_strength, str) and not raw_strength.strip()):
            arguments["strength"] = "unknown"

    if action in {"REQUEST_RECHECK", "SELECT_FORMALIZATION", "FINALIZE_PROFILE", "PROPOSE_PROMOTION"} and "claim_id" not in arguments:
        changed_ids = event.get("changed_ids") or []
        if isinstance(changed_ids, list) and changed_ids:
            arguments["claim_id"] = changed_ids[0]

    return action_object(action, arguments)
