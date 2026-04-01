"""Integration smoke for explicit promotion checkpoints and review journal policy."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from promotion state test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine import (  # noqa: E402
    ArtifactStore,
    AssuranceProfile,
    Gate,
    PromotionStateMachine,
    ReviewActorRole,
)
from formal_claim_engine.promotion_state_machine import PromotionStateError  # noqa: E402


def load_profile() -> AssuranceProfile:
    return AssuranceProfile.model_validate(
        json.loads(
            (REPO_ROOT / "examples" / "theorem-audit" / "assurance-profile.json").read_text(
                encoding="utf-8"
            )
        )
    )


def build_blocked_profile() -> AssuranceProfile:
    payload = load_profile().model_dump(mode="json", exclude_none=True)
    payload["gate"] = "blocked"
    payload["allowed_downstream"] = ["research"]
    payload["decision_rationale"] = "Runner audit produced blockers that require manual override."
    payload["required_actions"] = [
        "Resolve the countermodel before promotion.",
        "Document the override rationale if promotion is retried.",
    ]
    return AssuranceProfile.model_validate(payload)


def expect_error(fn, message: str) -> None:
    try:
        fn()
    except PromotionStateError as exc:
        assert message in str(exc), str(exc)
        return
    raise AssertionError(f"Expected PromotionStateError containing {message!r}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        profile = load_profile()
        store.save_assurance_profile(
            profile,
            actor="auditor",
            reason="fixture_import",
            metadata={"source": "theorem-audit"},
        )
        machine = PromotionStateMachine(store)

        initial = machine.load_state(profile)
        assert initial.current_gate == Gate.draft, initial

        expect_error(
            lambda: machine.transition(
                profile,
                target_gate="research_only",
                actor="human.reviewer",
                actor_role=ReviewActorRole.reviewer,
            ),
            "may not be skipped",
        )

        store.append_review_event(
            target_claim_id=str(profile.claim_id),
            artifact_kind="formalization_attempt",
            artifact_id="formalization_attempt.manual_authoring",
            event_type="formalization_attempt",
            actor="human.author",
            actor_role=ReviewActorRole.author.value,
            notes="Human authored a manual bridge note.",
            metadata={"source": "manual"},
        )

        queued = machine.transition(
            profile,
            target_gate="queued",
            actor="human.reviewer",
            actor_role=ReviewActorRole.reviewer,
            notes="Queue claim for staged promotion review.",
        )
        assert queued.current_gate == Gate.queued, queued

        research_only = machine.transition(
            profile,
            target_gate="research_only",
            actor="human.reviewer",
            actor_role=ReviewActorRole.reviewer,
        )
        assert research_only.current_gate == Gate.research_only, research_only

        dev_guarded = machine.transition(
            profile,
            target_gate="dev_guarded",
            actor="human.reviewer",
            actor_role=ReviewActorRole.reviewer,
        )
        assert dev_guarded.current_gate == Gate.dev_guarded, dev_guarded

        expect_error(
            lambda: machine.transition(
                profile,
                target_gate="certified",
                actor="human.author",
                actor_role=ReviewActorRole.certifier,
            ),
            "previously authored",
        )
        expect_error(
            lambda: machine.transition(
                profile,
                target_gate="certified",
                actor="human.certifier",
                actor_role=ReviewActorRole.reviewer,
            ),
            "actor_role=certifier",
        )

        certified = machine.transition(
            profile,
            target_gate="certified",
            actor="human.certifier",
            actor_role=ReviewActorRole.certifier,
            notes="Independent certification review completed.",
        )
        assert certified.current_gate == Gate.certified, certified
        assert len(certified.transitions) == 4, certified.transitions
        assert certified.transitions[-1].actor_role == ReviewActorRole.certifier

        promotion_events = [
            event
            for event in store.query_review_events(str(profile.claim_id))
            if event["event_type"] == "promotion_transition"
        ]
        assert len(promotion_events) == 4, promotion_events
        assert promotion_events[-1]["actor_role"] == "certifier"
        assert (
            promotion_events[-1]["metadata"]["profile_revision_id"]
            == certified.profile_revision_id
        )

        store.save_assurance_profile(
            profile,
            actor="auditor",
            reason="recompute",
            metadata={"source": "updated"},
        )
        reset = machine.load_state(profile)
        assert reset.current_gate == Gate.draft, reset
        assert reset.profile_revision_id != certified.profile_revision_id

    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        blocked_profile = build_blocked_profile()
        store.save_assurance_profile(
            blocked_profile,
            actor="auditor",
            reason="fixture_import",
        )
        machine = PromotionStateMachine(store)

        expect_error(
            lambda: machine.transition(
                blocked_profile,
                target_gate="queued",
                actor="human.reviewer",
                actor_role=ReviewActorRole.reviewer,
            ),
            "requires override",
        )
        expect_error(
            lambda: machine.transition(
                blocked_profile,
                target_gate="queued",
                actor="human.reviewer",
                actor_role=ReviewActorRole.reviewer,
                override=True,
            ),
            "non-empty rationale",
        )

        overridden = machine.transition(
            blocked_profile,
            target_gate="queued",
            actor="human.reviewer",
            actor_role=ReviewActorRole.reviewer,
            override=True,
            rationale="Runner blockers were reviewed and queued for manual salvage.",
        )
        assert overridden.current_gate == Gate.queued, overridden
        assert overridden.transitions[-1].override is True
        assert (
            overridden.transitions[-1].rationale
            == "Runner blockers were reviewed and queued for manual salvage."
        )


if __name__ == "__main__":
    main()
