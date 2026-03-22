"""Integration smoke for artifact revision metadata and review journals."""

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
    raise RuntimeError("Could not locate monorepo root from integration test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine import ArtifactStore  # noqa: E402
from formal_claim_engine.models import AssuranceProfile, ClaimGraph  # noqa: E402


def main() -> None:
    claim_graph = ClaimGraph.model_validate(
        json.loads(
            (REPO_ROOT / "examples" / "theorem-audit" / "claim-graph.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assurance_profile = AssuranceProfile.model_validate(
        json.loads(
            (
                REPO_ROOT / "examples" / "theorem-audit" / "assurance-profile.json"
            ).read_text(encoding="utf-8")
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        assert store.db_path.exists()
        store.save_claim_graph(
            claim_graph,
            actor="system.seed",
            reason="initial_import",
            metadata={"source": "fixture"},
        )
        store.save_claim_graph(
            claim_graph,
            actor="system.seed",
            reason="revalidation",
            metadata={"source": "fixture"},
        )
        store.save_assurance_profile(
            assurance_profile,
            actor="auditor",
            reason="profile_compute",
            metadata={"runner": "fixture"},
        )

        revisions = store.list_revisions("claim_graphs", str(claim_graph.graph_id))
        assert len(revisions) == 2, revisions
        assert revisions[-1]["reason"] == "revalidation"
        assert revisions[-1]["metadata"]["source"] == "fixture"

        profile_revisions = store.list_revisions(
            "assurance_profiles",
            str(assurance_profile.profile_id),
        )
        assert len(profile_revisions) == 1, profile_revisions
        assert (
            profile_revisions[0]["claim_id"]
            == "claim.dispatch.driver_assignment_converges"
        )

        review_event = store.append_review_event(
            target_claim_id="claim.dispatch.driver_assignment_converges",
            artifact_kind="assurance_profiles",
            artifact_id=str(assurance_profile.profile_id),
            event_type="approved",
            actor="human.reviewer",
            actor_role="reviewer",
            notes="Reviewed for deterministic fixture smoke.",
            metadata={"gate": "certified"},
        )
        queried = store.query_review_events("claim.dispatch.driver_assignment_converges")
        assert len(queried) == 1, queried
        assert queried[0]["event_id"] == review_event["event_id"]
        assert queried[0]["actor_role"] == "reviewer"
        assert queried[0]["metadata"]["gate"] == "certified"

        claim_graph_path = store._path("claim_graphs", str(claim_graph.graph_id))
        claim_graph_path.unlink()

        reloaded = ArtifactStore(tmp)
        restored_claim_graph = reloaded.load_claim_graph(str(claim_graph.graph_id))
        assert str(restored_claim_graph.graph_id) == str(claim_graph.graph_id)
        assert claim_graph_path.exists()
        assert len(reloaded.list_revisions("claim_graphs", str(claim_graph.graph_id))) == 2
        assert len(reloaded.query_review_events("claim.dispatch.driver_assignment_converges")) == 1


if __name__ == "__main__":
    main()
