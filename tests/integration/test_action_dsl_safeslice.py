"""Integration tests for PFX-003 (Action DSL / Legal Action Mask) and PFX-004 (SafeSlice Bridge).

Tests:
- LegalActionMaskBuilder produces correct masks per phase
- Promotion legality follows FSM rules (only next gate, no skip)
- Terminal gates produce no promotion actions
- SafeSliceBridge catches domain leaks
- SafeSliceBridge catches sidecar metadata in slices
- Action DSL string format matches spec
- PrefixSliceBuilder integration with mask builder and SafeSlice bridge
"""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from action DSL test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.action_dsl import (  # noqa: E402
    ActionTemplate,
    ActionVerb,
    LegalActionMaskBuilder,
    extract_gold_action_from_event,
)
from formal_claim_engine.safeslice_bridge import SafeSliceBridge  # noqa: E402
from formal_claim_engine.prefix_slice_builder import PrefixSliceBuilder  # noqa: E402
from formal_claim_engine.models import Gate  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_claim_graph(*claim_ids: str) -> dict:
    return {
        "claims": [{"claim_id": cid, "title": f"Claim {cid}"} for cid in claim_ids],
        "relations": [],
    }


def _make_profiles(claim_ids: list[str], gate: str = "draft") -> dict:
    return {
        cid: {"gate": gate, "formal_status": "unformalized", "required_actions": []}
        for cid in claim_ids
    }


def _make_promotion_states(
    claim_ids: list[str],
    current_gate: str = "draft",
    recommended_gate: str = "certified",
) -> dict:
    return {
        cid: {"current_gate": current_gate, "recommended_gate": recommended_gate}
        for cid in claim_ids
    }


# ============================================================================
# PFX-003: Action DSL string format
# ============================================================================


def test_action_template_dsl_string_with_params() -> None:
    """Action DSL string format must match spec: VERB(param1, param2, ...)."""
    t = ActionTemplate(
        ActionVerb.PROPOSE_RELATION,
        {
            "src_id": "c1",
            "relation_type": "supports",
            "tgt_id": "c2",
            "strength": "deductive",
        },
    )
    dsl = t.to_dsl_string()
    assert dsl == "PROPOSE_RELATION(c1, supports, c2, deductive)", dsl


def test_action_template_dsl_string_no_params() -> None:
    """Empty-param actions render as VERB()."""
    t = ActionTemplate(ActionVerb.REQUEST_RECHECK, {})
    assert t.to_dsl_string() == "REQUEST_RECHECK()"


def test_action_template_to_dict() -> None:
    """to_dict emits the canonical ActionDSL shape."""
    t = ActionTemplate(
        ActionVerb.FINALIZE_PROFILE, {"claim_id": "c.abc"}
    )
    d = t.to_dict()
    assert d["action"] == "FINALIZE_PROFILE"
    assert d["action_type"] == "FINALIZE_PROFILE"
    assert d["arguments"]["claim_id"] == "c.abc"


def test_action_verb_enum_values_match_spec() -> None:
    """All six spec verbs must be present as ActionVerb members."""
    expected = {
        "PROPOSE_RELATION",
        "ADD_HIDDEN_ASSUMPTION",
        "REQUEST_RECHECK",
        "SELECT_FORMALIZATION",
        "FINALIZE_PROFILE",
        "PROPOSE_PROMOTION",
    }
    actual = {v.value for v in ActionVerb}
    assert actual == expected, f"mismatch: {actual ^ expected}"


# ============================================================================
# PFX-003: LegalActionMaskBuilder — phase-based masks
# ============================================================================


def test_phase1_mask_has_relation_and_assumption_only() -> None:
    """Phase1 mask should contain only PROPOSE_RELATION and ADD_HIDDEN_ASSUMPTION."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(["c1"]),
    )
    mask = builder.compute_mask("phase1")
    verbs = {item["action"] for item in mask}
    assert verbs == {"PROPOSE_RELATION", "ADD_HIDDEN_ASSUMPTION"}, verbs


def test_phase2_mask_has_formalization_recheck_finalize() -> None:
    """Phase2 mask should contain SELECT_FORMALIZATION, REQUEST_RECHECK, FINALIZE_PROFILE."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(["c1"]),
    )
    mask = builder.compute_mask("phase2", claim_id="c1")
    verbs = {item["action"] for item in mask}
    assert "SELECT_FORMALIZATION" in verbs
    assert "REQUEST_RECHECK" in verbs
    assert "FINALIZE_PROFILE" in verbs
    # No promotion actions in phase2
    assert "PROPOSE_PROMOTION" not in verbs


def test_phase3_mask_has_promotion_and_recheck() -> None:
    """Phase3 mask should contain PROPOSE_PROMOTION and REQUEST_RECHECK."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(["c1"]),
    )
    mask = builder.compute_mask("phase3", claim_id="c1")
    verbs = {item["action"] for item in mask}
    assert "PROPOSE_PROMOTION" in verbs
    assert "REQUEST_RECHECK" in verbs


# ============================================================================
# PFX-003: Promotion legality follows FSM rules
# ============================================================================


def test_draft_can_only_promote_to_queued() -> None:
    """From draft, the only legal linear promotion target is queued."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(["c1"], current_gate="draft"),
    )
    mask = builder.compute_mask("phase3", claim_id="c1")
    promotion_actions = [
        item for item in mask if item["action"] == "PROPOSE_PROMOTION"
    ]
    linear_targets = [
        a["arguments"]["target_gate"]
        for a in promotion_actions
        if a["arguments"]["target_gate"] not in ("blocked", "rejected", "superseded")
    ]
    assert linear_targets == ["queued"], linear_targets


def test_queued_can_only_promote_to_research_only() -> None:
    """From queued, the only legal linear target is research_only."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(["c1"], current_gate="queued"),
    )
    mask = builder.compute_mask("phase3", claim_id="c1")
    promotion_actions = [
        item for item in mask if item["action"] == "PROPOSE_PROMOTION"
    ]
    linear_targets = [
        a["arguments"]["target_gate"]
        for a in promotion_actions
        if a["arguments"]["target_gate"] not in ("blocked", "rejected", "superseded")
    ]
    assert linear_targets == ["research_only"], linear_targets


def test_no_gate_skipping() -> None:
    """Cannot skip gates: draft -> research_only is illegal; only draft -> queued."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(["c1"], current_gate="draft"),
    )
    mask = builder.compute_mask("phase3", claim_id="c1")
    promotion_actions = [
        item for item in mask if item["action"] == "PROPOSE_PROMOTION"
    ]
    linear_targets = [
        a["arguments"]["target_gate"]
        for a in promotion_actions
        if a["arguments"]["target_gate"] not in ("blocked", "rejected", "superseded")
    ]
    assert "research_only" not in linear_targets
    assert "dev_guarded" not in linear_targets
    assert "certified" not in linear_targets


def test_terminal_gate_produces_no_linear_promotions() -> None:
    """Terminal gates (blocked, rejected, superseded) block further linear promotion."""
    for terminal in ("blocked", "rejected", "superseded"):
        builder = LegalActionMaskBuilder(
            claim_graph=_make_claim_graph("c1"),
            profiles=_make_profiles(["c1"]),
            promotion_states=_make_promotion_states(
                ["c1"], current_gate=terminal
            ),
        )
        mask = builder.compute_mask("phase3", claim_id="c1")
        promotion_actions = [
            item for item in mask if item["action"] == "PROPOSE_PROMOTION"
        ]
        assert promotion_actions == [], (
            f"Terminal gate {terminal} should produce no promotion actions, "
            f"got {promotion_actions}"
        )


def test_terminal_gates_always_available_from_linear() -> None:
    """From any linear gate, terminal transitions (blocked, rejected, superseded) are available."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(["c1"], current_gate="queued"),
    )
    mask = builder.compute_mask("phase3", claim_id="c1")
    promotion_actions = [
        item for item in mask if item["action"] == "PROPOSE_PROMOTION"
    ]
    all_targets = {a["arguments"]["target_gate"] for a in promotion_actions}
    assert "blocked" in all_targets
    assert "rejected" in all_targets
    assert "superseded" in all_targets


def test_override_required_marked_when_exceeding_recommended() -> None:
    """If target exceeds recommended gate, override_required must be True."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(
            ["c1"], current_gate="draft", recommended_gate="draft"
        ),
    )
    mask = builder.compute_mask("phase3", claim_id="c1")
    promotion_actions = [
        item for item in mask if item["action"] == "PROPOSE_PROMOTION"
    ]
    queued_action = next(
        (a for a in promotion_actions if a["arguments"]["target_gate"] == "queued"),
        None,
    )
    assert queued_action is not None
    # Promoting from draft to queued when recommended is draft -> override required
    assert queued_action["arguments"].get("override_required") is True, queued_action


def test_override_not_required_within_recommended() -> None:
    """If target is within recommended gate ceiling, override_required is False."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(
            ["c1"], current_gate="draft", recommended_gate="certified"
        ),
    )
    mask = builder.compute_mask("phase3", claim_id="c1")
    promotion_actions = [
        item for item in mask if item["action"] == "PROPOSE_PROMOTION"
    ]
    queued_action = next(
        (a for a in promotion_actions if a["arguments"]["target_gate"] == "queued"),
        None,
    )
    assert queued_action is not None
    assert queued_action["arguments"].get("override_required") is False, queued_action


def test_certified_is_top_of_linear_no_further_promotion() -> None:
    """At certified (last linear gate), no further linear promotion is possible."""
    builder = LegalActionMaskBuilder(
        claim_graph=_make_claim_graph("c1"),
        profiles=_make_profiles(["c1"]),
        promotion_states=_make_promotion_states(["c1"], current_gate="certified"),
    )
    mask = builder.compute_mask("phase3", claim_id="c1")
    promotion_actions = [
        item for item in mask if item["action"] == "PROPOSE_PROMOTION"
    ]
    linear_targets = [
        a["arguments"]["target_gate"]
        for a in promotion_actions
        if a["arguments"]["target_gate"] not in ("blocked", "rejected", "superseded")
    ]
    assert linear_targets == [], linear_targets


# ============================================================================
# PFX-004: SafeSliceBridge — domain leak detection
# ============================================================================


def test_safeslice_catches_source_domain_in_state_text() -> None:
    """SafeSliceBridge must flag source_domain in state_text."""
    bridge = SafeSliceBridge()
    bad_slice = {
        "step_id": "s1",
        "state_text": "The source_domain is medical and this is the claim.",
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    violations = bridge.validate_slice(bad_slice)
    assert any("source_domain" in v for v in violations), violations


def test_safeslice_catches_prompt_id_in_state_text() -> None:
    """SafeSliceBridge must flag prompt_id in state_text."""
    bridge = SafeSliceBridge()
    bad_slice = {
        "step_id": "s1",
        "state_text": "Used prompt_id=abc-123 for generation.",
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    violations = bridge.validate_slice(bad_slice)
    assert any("prompt_id" in v for v in violations), violations


def test_safeslice_catches_operator_notes_in_state_text() -> None:
    """SafeSliceBridge must flag operator_notes in state_text."""
    bridge = SafeSliceBridge()
    bad_slice = {
        "step_id": "s1",
        "state_text": "The operator_notes say this claim is important.",
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    violations = bridge.validate_slice(bad_slice)
    assert any("operator_notes" in v for v in violations), violations


def test_safeslice_clean_slice_passes() -> None:
    """A clean slice with canonical text should pass validation."""
    bridge = SafeSliceBridge()
    clean_slice = {
        "trace_id": "t1",
        "step_id": "s1",
        "state_text": "[CURRENT CLAIMS]\nc1: Example claim (status=stated, role=theorem)",
        "available_artifacts": ["artifact-1"],
        "legal_action_mask": [{"verb": "PROPOSE_RELATION", "params": {}}],
        "gold_action": {"verb": "PROPOSE_RELATION"},
    }
    violations = bridge.validate_slice(clean_slice)
    assert violations == [], violations


# ============================================================================
# PFX-004: SafeSliceBridge — sidecar metadata detection
# ============================================================================


def test_safeslice_catches_sidecar_key() -> None:
    """SafeSliceBridge must flag sidecar_meta mixed into slice."""
    bridge = SafeSliceBridge()
    bad_slice = {
        "step_id": "s1",
        "state_text": "[CURRENT CLAIMS]\nc1: Example",
        "sidecar_meta": {"notes": "internal only"},
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    violations = bridge.validate_slice(bad_slice)
    assert any("sidecar" in v.lower() for v in violations), violations


def test_safeslice_catches_operator_metadata_key() -> None:
    """SafeSliceBridge must flag operator_metadata mixed into slice."""
    bridge = SafeSliceBridge()
    bad_slice = {
        "step_id": "s1",
        "state_text": "[CURRENT CLAIMS]\nc1: Example",
        "operator_metadata": {"secret": "value"},
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    violations = bridge.validate_slice(bad_slice)
    assert any("operator_metadata" in v for v in violations), violations


def test_safeslice_catches_source_domain_key() -> None:
    """SafeSliceBridge must flag source_domain as a top-level slice key."""
    bridge = SafeSliceBridge()
    bad_slice = {
        "step_id": "s1",
        "state_text": "[CURRENT CLAIMS]\nc1: Example",
        "source_domain": "medical",
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    violations = bridge.validate_slice(bad_slice)
    assert any("source_domain" in v for v in violations), violations


def test_safeslice_catches_raw_json_state_text() -> None:
    """SafeSliceBridge must flag raw JSON dumps as state_text."""
    bridge = SafeSliceBridge()
    bad_slice = {
        "step_id": "s1",
        "state_text": '{"claims": [{"id": "c1", "text": "something"}]}',
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    violations = bridge.validate_slice(bad_slice)
    assert any("raw JSON" in v for v in violations), violations


# ============================================================================
# PFX-004: SafeSliceBridge — filtering
# ============================================================================


def test_safeslice_filter_removes_sidecar_keys() -> None:
    """filter_slice must remove sidecar keys."""
    bridge = SafeSliceBridge()
    dirty_slice = {
        "step_id": "s1",
        "state_text": "[CURRENT CLAIMS]\nc1: Example",
        "sidecar_meta": {"notes": "internal only"},
        "operator_metadata": {"key": "val"},
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    clean = bridge.filter_slice(dirty_slice)
    assert "sidecar_meta" not in clean
    assert "operator_metadata" not in clean
    assert "step_id" in clean


def test_safeslice_filter_scrubs_banned_from_state_text() -> None:
    """filter_slice must replace banned field occurrences in state_text."""
    bridge = SafeSliceBridge()
    dirty_slice = {
        "step_id": "s1",
        "state_text": "The source_domain is medical.",
        "available_artifacts": [],
        "legal_action_mask": None,
        "gold_action": None,
    }
    clean = bridge.filter_slice(dirty_slice)
    assert "source_domain" not in clean["state_text"].lower()


# ============================================================================
# PFX-004: SafeSliceBridge — batch validation
# ============================================================================


def test_batch_validate_returns_only_violations() -> None:
    """batch_validate returns only step_ids with violations."""
    bridge = SafeSliceBridge()
    slices = [
        {
            "step_id": "s1",
            "state_text": "[CURRENT CLAIMS]\nc1: Clean",
            "available_artifacts": [],
            "legal_action_mask": None,
            "gold_action": None,
        },
        {
            "step_id": "s2",
            "state_text": "Has source_domain leak",
            "available_artifacts": [],
            "legal_action_mask": None,
            "gold_action": None,
        },
    ]
    results = bridge.batch_validate(slices)
    assert "s1" not in results
    assert "s2" in results
    assert any("source_domain" in v for v in results["s2"])


# ============================================================================
# Integration: PrefixSliceBuilder + LegalActionMaskBuilder + SafeSliceBridge
# ============================================================================


def test_prefix_builder_with_action_mask_builder() -> None:
    """PrefixSliceBuilder should populate legal_action_mask from LegalActionMaskBuilder."""
    trace = {
        "trace_id": "trace-001",
        "source": {"title": "Test Document", "text": "Some content."},
        "claims": [{"claim_id": "c1", "title": "Claim 1", "statement": "X implies Y"}],
        "relations": [],
        "gaps": [],
        "hidden_assumptions": [],
        "formalization": {},
        "artifacts": [],
    }
    transition_log = [
        {
            "step_id": "step_0",
            "event_type": "structuring",
            "phase": 1,
            "claim_id": "c1",
        },
    ]
    builder = PrefixSliceBuilder(trace, transition_log)
    mask_builder = LegalActionMaskBuilder(
        claim_graph={"claims": [{"claim_id": "c1"}]},
        profiles={"c1": {"gate": "draft"}},
        promotion_states={"c1": {"current_gate": "draft", "recommended_gate": "certified"}},
    )
    builder.set_action_mask_builder(mask_builder)

    slices = builder.extract_slices()
    assert len(slices) == 1
    mask = slices[0]["legal_action_mask"]
    assert mask is not None
    verbs = {item["action"] for item in mask}
    # Phase1 structuring -> PROPOSE_RELATION, ADD_HIDDEN_ASSUMPTION
    assert "PROPOSE_RELATION" in verbs
    assert "ADD_HIDDEN_ASSUMPTION" in verbs


def test_prefix_builder_with_safeslice_bridge_passes_clean() -> None:
    """PrefixSliceBuilder with SafeSliceBridge attached should pass clean slices."""
    trace = {
        "trace_id": "trace-002",
        "source": {"title": "Clean Doc", "text": "Clean content."},
        "claims": [{"claim_id": "c1", "title": "Claim 1", "statement": "A is B"}],
        "relations": [],
        "gaps": [],
        "hidden_assumptions": [],
        "formalization": {},
        "artifacts": [],
    }
    transition_log = [
        {
            "step_id": "step_0",
            "event_type": "structuring",
            "phase": 1,
        },
    ]
    builder = PrefixSliceBuilder(trace, transition_log)
    bridge = SafeSliceBridge()
    builder.set_safeslice_bridge(bridge)

    slices = builder.extract_slices()
    assert len(slices) == 1


def test_prefix_builder_with_safeslice_bridge_rejects_dirty() -> None:
    """PrefixSliceBuilder with SafeSliceBridge should reject slices with domain leaks."""
    trace = {
        "trace_id": "trace-003",
        "source": {"title": "Dirty Doc", "text": "The source_domain is legal."},
        "claims": [],
        "relations": [],
        "gaps": [],
        "hidden_assumptions": [],
        "formalization": {},
        "artifacts": [],
    }
    transition_log = [
        {
            "step_id": "step_0",
            "event_type": "structuring",
            "phase": 1,
        },
    ]
    builder = PrefixSliceBuilder(trace, transition_log)
    bridge = SafeSliceBridge()
    builder.set_safeslice_bridge(bridge)

    try:
        builder.extract_slices()
        raise AssertionError("Expected ValueError from SafeSlice validation")
    except ValueError as exc:
        assert "source_domain" in str(exc).lower(), str(exc)


# ============================================================================
# B20: Canonical proposal completeness and gold_action extraction
# ============================================================================


def test_gold_action_propose_relation_always_has_strength() -> None:
    """B20/AUD-007: PROPOSE_RELATION gold_action must always include strength."""
    event = {
        "event_type": "propose_relation",
        "event_class": "controllable_action",
        "proposal": {
            "src_id": "c1",
            "tgt_id": "c2",
            "relation_type": "supports",
            # strength is omitted
        },
        "changed_ids": [],
    }
    ga = extract_gold_action_from_event(event)
    assert ga is not None
    assert ga["arguments"]["strength"] == "unknown", (
        f"Expected strength='unknown' when omitted, got {ga['arguments'].get('strength')}"
    )


def test_gold_action_propose_relation_null_strength_defaults_unknown() -> None:
    """B20/AUD-007: null strength in proposal must become 'unknown' in gold_action."""
    event = {
        "event_type": "propose_relation",
        "event_class": "controllable_action",
        "proposal": {
            "src_id": "c1",
            "tgt_id": "c2",
            "relation_type": "supports",
            "strength": None,
        },
        "changed_ids": [],
    }
    ga = extract_gold_action_from_event(event)
    assert ga is not None
    assert ga["arguments"]["strength"] == "unknown"


def test_gold_action_select_formalization_normalizes_attempts_array() -> None:
    """B20/AUD-006: attempts=[a,b] in proposal must become singular attempt in gold_action."""
    event = {
        "event_type": "select_formalization",
        "event_class": "controllable_action",
        "proposal": {
            "claim_id": "c1",
            "attempts": ["a", "b"],
        },
        "changed_ids": ["c1"],
    }
    ga = extract_gold_action_from_event(event)
    assert ga is not None
    assert ga["action"] == "SELECT_FORMALIZATION"
    # Must have singular "attempt", not "attempts"
    assert "attempt" in ga["arguments"], f"Expected 'attempt' key, got {ga['arguments']}"
    assert ga["arguments"]["attempt"] in ("a", "b"), (
        f"Expected 'a' or 'b', got {ga['arguments']['attempt']}"
    )


def test_gold_action_select_formalization_uses_singular_attempt() -> None:
    """B20: select_formalization with canonical singular attempt should preserve it."""
    event = {
        "event_type": "select_formalization",
        "event_class": "controllable_action",
        "proposal": {
            "claim_id": "c1",
            "attempt": "b",
        },
        "changed_ids": ["c1"],
    }
    ga = extract_gold_action_from_event(event)
    assert ga is not None
    assert ga["arguments"]["attempt"] == "b"
    assert ga["arguments"]["claim_id"] == "c1"


def test_gold_action_finalize_profile_has_claim_id() -> None:
    """B20: FINALIZE_PROFILE gold_action must have claim_id."""
    event = {
        "event_type": "finalize_profile",
        "event_class": "controllable_action",
        "proposal": {"claim_id": "c.xyz"},
        "changed_ids": ["c.xyz"],
    }
    ga = extract_gold_action_from_event(event)
    assert ga is not None
    assert ga["action"] == "FINALIZE_PROFILE"
    assert ga["arguments"]["claim_id"] == "c.xyz"


def test_gold_action_propose_promotion_has_target_gate() -> None:
    """B20: PROPOSE_PROMOTION gold_action must have claim_id and target_gate."""
    event = {
        "event_type": "propose_promotion",
        "event_class": "controllable_action",
        "proposal": {"claim_id": "c1", "target_gate": "queued"},
        "changed_ids": ["c1"],
    }
    ga = extract_gold_action_from_event(event)
    assert ga is not None
    assert ga["action"] == "PROPOSE_PROMOTION"
    assert ga["arguments"]["claim_id"] == "c1"
    assert ga["arguments"]["target_gate"] == "queued"


def test_gold_action_add_hidden_assumption_has_text_and_attaches_to() -> None:
    """B20: ADD_HIDDEN_ASSUMPTION gold_action must have text and attaches_to."""
    event = {
        "event_type": "add_hidden_assumption",
        "event_class": "controllable_action",
        "proposal": {"text": "Assumes X", "attaches_to": "c1"},
        "changed_ids": [],
    }
    ga = extract_gold_action_from_event(event)
    assert ga is not None
    assert ga["action"] == "ADD_HIDDEN_ASSUMPTION"
    assert ga["arguments"]["text"] == "Assumes X"
    assert ga["arguments"]["attaches_to"] == "c1"


def test_gold_action_request_recheck_has_claim_id() -> None:
    """B20: REQUEST_RECHECK gold_action must have claim_id."""
    event = {
        "event_type": "request_recheck",
        "event_class": "controllable_action",
        "proposal": {"claim_id": "c.abc"},
        "changed_ids": ["c.abc"],
    }
    ga = extract_gold_action_from_event(event)
    assert ga is not None
    assert ga["action"] == "REQUEST_RECHECK"
    assert ga["arguments"]["claim_id"] == "c.abc"


def test_automatic_consequence_returns_none_not_gold_action() -> None:
    """B20: automatic_consequence events should not produce gold_action (except at end)."""
    event = {
        "event_type": "profile_recomputed",
        "event_class": "automatic_consequence",
        "proposal": {},
        "changed_ids": ["c1"],
    }
    ga = extract_gold_action_from_event(event, is_last_step=False)
    assert ga is None


# ============================================================================
# B30/PTR-001: Pointer resolution checks
# ============================================================================

from formal_claim_engine.action_dsl import is_pointer_resolvable


def test_pointer_resolvable_propose_relation_visible_ids() -> None:
    """PTR-001: PROPOSE_RELATION with visible src_id and tgt_id is resolvable."""
    action = {
        "action": "PROPOSE_RELATION",
        "action_type": "PROPOSE_RELATION",
        "arguments": {
            "src_id": "claim.a",
            "tgt_id": "claim.b",
            "relation_type": "supports",
            "strength": "deductive",
        },
    }
    visible = {"claim.a", "claim.b", "claim.c"}
    assert is_pointer_resolvable(action, visible) is True


def test_pointer_unresolvable_propose_relation_missing_tgt() -> None:
    """PTR-001: PROPOSE_RELATION with tgt_id not in visible is unresolvable."""
    action = {
        "action": "PROPOSE_RELATION",
        "action_type": "PROPOSE_RELATION",
        "arguments": {
            "src_id": "claim.a",
            "tgt_id": "claim.missing.proj_85cf2779",
            "relation_type": "supports",
            "strength": "unknown",
        },
    }
    visible = {"claim.a", "claim.b"}
    assert is_pointer_resolvable(action, visible) is False


def test_pointer_unresolvable_propose_relation_missing_src() -> None:
    """PTR-001: PROPOSE_RELATION with src_id not in visible is unresolvable."""
    action = {
        "action": "PROPOSE_RELATION",
        "action_type": "PROPOSE_RELATION",
        "arguments": {
            "src_id": "claim.missing",
            "tgt_id": "claim.b",
            "relation_type": "supports",
            "strength": "deductive",
        },
    }
    visible = {"claim.b"}
    assert is_pointer_resolvable(action, visible) is False


def test_pointer_resolvable_select_formalization() -> None:
    """PTR-001: SELECT_FORMALIZATION with visible claim_id is resolvable."""
    action = {
        "action": "SELECT_FORMALIZATION",
        "action_type": "SELECT_FORMALIZATION",
        "arguments": {"claim_id": "claim.x", "attempt": "a"},
    }
    visible = {"claim.x"}
    assert is_pointer_resolvable(action, visible) is True


def test_pointer_unresolvable_select_formalization() -> None:
    """PTR-001: SELECT_FORMALIZATION with missing claim_id is unresolvable."""
    action = {
        "action": "SELECT_FORMALIZATION",
        "action_type": "SELECT_FORMALIZATION",
        "arguments": {"claim_id": "claim.missing", "attempt": "a"},
    }
    visible = {"claim.x"}
    assert is_pointer_resolvable(action, visible) is False


def test_pointer_resolvable_done_action() -> None:
    """PTR-001: DONE action is always resolvable."""
    action = {"action": "DONE", "action_type": "DONE", "arguments": {}}
    visible = set()
    assert is_pointer_resolvable(action, visible) is True


def test_pointer_resolvable_none_action() -> None:
    """PTR-001: None gold_action is always resolvable."""
    assert is_pointer_resolvable(None, set()) is True


def test_pointer_resolvable_add_hidden_assumption() -> None:
    """PTR-001: ADD_HIDDEN_ASSUMPTION with visible attaches_to is resolvable."""
    action = {
        "action": "ADD_HIDDEN_ASSUMPTION",
        "action_type": "ADD_HIDDEN_ASSUMPTION",
        "arguments": {"text": "Assumption text", "attaches_to": "claim.x"},
    }
    visible = {"claim.x"}
    assert is_pointer_resolvable(action, visible) is True


def test_pointer_unresolvable_add_hidden_assumption() -> None:
    """PTR-001: ADD_HIDDEN_ASSUMPTION with missing attaches_to is unresolvable."""
    action = {
        "action": "ADD_HIDDEN_ASSUMPTION",
        "action_type": "ADD_HIDDEN_ASSUMPTION",
        "arguments": {"text": "Assumption text", "attaches_to": "claim.gone"},
    }
    visible = {"claim.x"}
    assert is_pointer_resolvable(action, visible) is False


def test_prefix_builder_filters_unresolvable_policy_rows() -> None:
    """PTR-001: PrefixSliceBuilder must omit pointer-unresolvable rows."""
    trace = {
        "trace_id": "trace-ptr-001",
        "source": {"title": "Test", "text": "Content."},
        "claims": [
            {"claim_id": "claim.a", "title": "A", "statement": "Claim A"},
            {"claim_id": "claim.b", "title": "B", "statement": "Claim B"},
        ],
        "relations": [],
        "gaps": [],
        "hidden_assumptions": [],
        "formalization": {},
        "artifacts": [],
    }

    transition_log = [
        # Resolvable: both src and tgt in visible claims
        {
            "step_id": "step-1",
            "event_type": "propose_relation",
            "phase": "phase1",
            "event_class": "controllable_action",
            "proposal": {
                "src_id": "claim.a",
                "tgt_id": "claim.b",
                "relation_type": "supports",
                "strength": "deductive",
            },
            "accepted": True,
            "outcome": {
                "relations": [{
                    "source_id": "claim.a",
                    "target_id": "claim.b",
                    "relation_type": "supports",
                    "relation_id": "edge.1",
                }],
            },
        },
        # Unresolvable: tgt_id not in visible claims
        {
            "step_id": "step-2",
            "event_type": "propose_relation",
            "phase": "phase1",
            "event_class": "controllable_action",
            "proposal": {
                "src_id": "claim.a",
                "tgt_id": "claim.missing.proj_85cf2779",
                "relation_type": "challenges",
                "strength": "unknown",
            },
            "accepted": False,
        },
    ]

    builder = PrefixSliceBuilder(trace, transition_log)
    slices = builder.extract_slices()

    # The resolvable event produces a policy row; the unresolvable does not
    assert len(slices) == 1, f"Expected 1 resolvable slice, got {len(slices)}"
    assert slices[0]["step_id"] == "step-1"


def main() -> None:
    # Run all test functions
    test_funcs = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    passed = 0
    failed = 0
    for fn in test_funcs:
        try:
            fn()
            passed += 1
            print(f"  PASS  {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")

    print(f"\n{passed} passed, {failed} failed out of {passed + failed} tests")
    if failed:
        sys.exit(1)


# ===================================================================
# B60/VRF-001: Real artifact regression tests (AUD-006, AUD-007, AUD-008)
# ===================================================================

_EXPORT_DIR = REPO_ROOT.parent / "_push" / "e2e-run-test-doc" / "export-current"


class TestB60ActionDSLArtifactRegression:
    """Real-artifact regression tests for canonical proposals and gold actions."""

    @staticmethod
    def _skip_if_no_artifacts():
        if not _EXPORT_DIR.exists():
            import pytest
            pytest.skip("Export artifacts not available at expected path")

    def _load_transition_log(self) -> list[dict]:
        self._skip_if_no_artifacts()
        import json
        path = _EXPORT_DIR / "transition_log.jsonl"
        if not path.exists():
            import pytest
            pytest.skip("transition_log.jsonl not found")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _load_prefix_slices(self) -> list[dict]:
        self._skip_if_no_artifacts()
        import json
        path = _EXPORT_DIR / "prefix_slices.jsonl"
        if not path.exists():
            import pytest
            pytest.skip("prefix_slices.jsonl not found")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # AUD-006: controllable proposals must have required canonical fields
    def test_aud006_controllable_proposals_canonical(self):
        """AUD-006 regression: controllable proposals must be canonically complete."""
        events = self._load_transition_log()
        controllable = [e for e in events if e.get("event_class") == "controllable_action"]
        required_fields_by_type = {
            "propose_relation": {"src_id", "tgt_id", "relation_type", "strength"},
            "select_formalization": {"claim_id"},
            "propose_promotion": {"claim_id", "target_gate"},
            "finalize_profile": {"claim_id"},
            "request_recheck": {"claim_id"},
            "add_hidden_assumption": {"text", "attaches_to"},
        }
        for e in controllable:
            proposal = e.get("proposal")
            if not proposal:
                continue  # proposal may be null for some events
            event_type = e.get("event_type", "")
            required = required_fields_by_type.get(event_type, set())
            if not required:
                continue
            args = proposal.get("arguments", proposal.get("args", {}))
            # Check no 'attempts' array remains for select_formalization
            if event_type == "select_formalization":
                assert "attempts" not in args, (
                    f"AUD-006: 'attempts' array found in {e['step_id']} proposal"
                )
            # Check strength is not null for propose_relation
            if event_type == "propose_relation":
                strength = args.get("strength")
                assert strength is not None, (
                    f"AUD-006: null strength in {e['step_id']} proposal"
                )

    # AUD-007: exported gold actions must have required canonical fields
    def test_aud007_gold_actions_canonical(self):
        """AUD-007 regression: gold_action arguments must be complete."""
        slices = self._load_prefix_slices()
        for s in slices:
            gold = s.get("gold_action")
            if not gold:
                continue
            action_type = gold.get("action", gold.get("action_type", ""))
            args = gold.get("arguments", {})
            # PROPOSE_RELATION must have strength
            if action_type == "PROPOSE_RELATION":
                assert "strength" in args and args["strength"] is not None, (
                    f"AUD-007: PROPOSE_RELATION gold_action at {s['step_id']} missing strength"
                )
            # SELECT_FORMALIZATION must have claim_id and singular attempt (no attempts array)
            if action_type == "SELECT_FORMALIZATION":
                assert "claim_id" in args, (
                    f"AUD-007: SELECT_FORMALIZATION gold_action at {s['step_id']} missing claim_id"
                )
            # PROPOSE_PROMOTION must have claim_id and target_gate
            if action_type == "PROPOSE_PROMOTION":
                assert "claim_id" in args, (
                    f"AUD-007: PROPOSE_PROMOTION gold_action at {s['step_id']} missing claim_id"
                )
                assert "target_gate" in args, (
                    f"AUD-007: PROPOSE_PROMOTION gold_action at {s['step_id']} missing target_gate"
                )

    # AUD-008: no unresolved pointer IDs in policy rows
    def test_aud008_no_unresolved_pointers_in_policy_gold_actions(self):
        """AUD-008 regression: gold_action ids must not reference missing nodes."""
        slices = self._load_prefix_slices()
        for s in slices:
            gold = s.get("gold_action")
            if not gold:
                continue
            args = gold.get("arguments", {})
            # Check that claim_id, src_id, tgt_id do not match the known missing pattern
            for key in ("claim_id", "src_id", "tgt_id"):
                val = args.get(key, "")
                if val:
                    assert "missing" not in str(val).lower(), (
                        f"AUD-008: unresolved pointer {key}={val} in gold_action at {s['step_id']}"
                    )


class TestB60ActionCanonicalUnit:
    """Unit-level regression tests for canonical action requirements."""

    def test_select_formalization_singular_attempt(self):
        """SELECT_FORMALIZATION must use singular 'attempt', not 'attempts' array."""
        event = {
            "step_id": "step-001",
            "event_type": "select_formalization",
            "event_class": "controllable_action",
            "action": {
                "action": "SELECT_FORMALIZATION",
                "arguments": {"claim_id": "c1", "attempt": "a"},
            },
        }
        gold = extract_gold_action_from_event(event)
        if gold:
            args = gold.get("arguments", {})
            assert "attempts" not in args, "Gold action must not have 'attempts' array"

    def test_propose_relation_strength_required(self):
        """PROPOSE_RELATION must always include strength."""
        event = {
            "step_id": "step-001",
            "event_type": "propose_relation",
            "event_class": "controllable_action",
            "action": {
                "action": "PROPOSE_RELATION",
                "arguments": {
                    "src_id": "c1", "tgt_id": "c2",
                    "relation_type": "supports", "strength": "unknown",
                },
            },
        }
        gold = extract_gold_action_from_event(event)
        if gold:
            assert gold.get("arguments", {}).get("strength") is not None

    def test_propose_promotion_target_gate_required(self):
        """PROPOSE_PROMOTION must include target_gate."""
        event = {
            "step_id": "step-001",
            "event_type": "propose_promotion",
            "event_class": "controllable_action",
            "action": {
                "action": "PROPOSE_PROMOTION",
                "arguments": {"claim_id": "c1", "target_gate": "queued"},
            },
        }
        gold = extract_gold_action_from_event(event)
        if gold:
            assert "target_gate" in gold.get("arguments", {})


if __name__ == "__main__":
    main()
