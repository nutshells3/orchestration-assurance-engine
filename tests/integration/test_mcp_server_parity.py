"""Parity smoke: canonical MCP tools should be thin facades over engine API calls."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "mcp-server" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from MCP parity test.")


REPO_ROOT = resolve_repo_root()
MCP_SRC = REPO_ROOT / "services" / "mcp-server" / "src"
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

for src in (MCP_SRC, ENGINE_SRC):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from formal_claim_engine.engine_api import (  # noqa: E402
    AuditRunResult,
    ClaimStructuringRunResult,
    DocumentIngestRunResult,
    DualFormalizationRunResult,
    EngineProjectHandle,
    EngineProjectSnapshot,
    ProfileRecomputeResult,
    ProjectBundleExport,
)
from formal_claim_engine.models import Gate  # noqa: E402
from formal_claim_engine.promotion_state_machine import (  # noqa: E402
    PromotionCheckpointState,
    PromotionTransition,
    ReviewActorRole,
)
from formal_claim_mcp_server import reset_runtime  # noqa: E402
from formal_claim_mcp_server import server  # noqa: E402


def parse_tool_payload(result):
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    raise AssertionError(f"Unexpected MCP tool result: {result!r}")


async def wait_for_job(job_id: str) -> dict[str, object]:
    for _ in range(100):
        payload = parse_tool_payload(await server.mcp.call_tool("job.get", {"job_id": job_id}))
        job = payload["data"]["job"]
        if job["status"] in {"completed", "failed"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish.")


class StubEngineAPI:
    def __init__(self) -> None:
        self.project = EngineProjectHandle(
            project_id="project.mcp_parity",
            name="parity",
            domain="general",
            description="stub",
            claim_graph_id="cg.project.mcp_parity",
        )
        self.snapshot = EngineProjectSnapshot(
            project_id=self.project.project_id,
            name=self.project.name,
            domain=self.project.domain,
            description=self.project.description,
            claim_graph_id=self.project.claim_graph_id,
            claim_count=1,
            snapshot={"id": self.project.project_id},
        )

    def create_project(self, name: str, domain: str, description: str = "") -> EngineProjectHandle:
        return self.project

    def open_project(self, project_id: str) -> EngineProjectSnapshot:
        assert project_id == self.project.project_id
        return self.snapshot

    def list_projects(self):
        return [self.project]

    async def ingest_document(self, project_id: str, text: str) -> DocumentIngestRunResult:
        return DocumentIngestRunResult(
            project_id=project_id,
            claim_graph_id="cg.project.mcp_parity",
            claims_added=2,
            relations_added=1,
            claim_ids=["claim.a", "claim.b"],
            mapping_report={"imported_claim_count": 2},
            ingest_bundle={"claim_candidates": [{"claim_id": "claim.a"}]},
            unresolved_references=[],
            evidence_items_added=1,
            project=self.snapshot,
        )

    async def run_claim_structuring(self, project_id: str, user_input: str) -> ClaimStructuringRunResult:
        return ClaimStructuringRunResult(
            project_id=project_id,
            claim_graph={"graph_id": "cg.project.mcp_parity", "claims": []},
            workflow={"workflow_id": "workflow.claim_structuring.stub", "state": "admitted"},
            project=self.snapshot,
        )

    async def run_dual_formalization(self, project_id: str, claim_id: str) -> DualFormalizationRunResult:
        return DualFormalizationRunResult(
            project_id=project_id,
            claim_id=claim_id,
            workflow={"workflow_id": "workflow.dual.stub", "state": "completed"},
        )

    async def run_audit(self, project_id: str, claim_id: str) -> AuditRunResult:
        return AuditRunResult(
            project_id=project_id,
            claim_id=claim_id,
            build_results={"A": {"success": True}},
            verifier_results={"A": {"build_success": True}},
            proof_audit={"success": True},
            audit_output={"recommendation": "proceed"},
            profile={"profile_id": "profile.claim.a", "gate": "research_only"},
            audit_workflow={"workflow_id": "workflow.audit.stub", "state": "completed"},
            promotion_state={"current_gate": "draft"},
        )

    def recompute_profile(
        self,
        project_id: str,
        claim_id: str,
        audit_result,
        *,
        research_output=None,
    ) -> ProfileRecomputeResult:
        return ProfileRecomputeResult(
            project_id=project_id,
            claim_id=claim_id,
            profile={"profile_id": "profile.claim.a", "gate": "research_only"},
            promotion_state={"current_gate": "draft"},
        )

    def approve_promotion(
        self,
        project_id: str,
        claim_id: str,
        *,
        target_gate,
        actor: str,
        actor_role,
        override: bool = False,
        rationale: str = "",
        notes: str = "",
    ) -> PromotionCheckpointState:
        return PromotionCheckpointState(
            claim_id=claim_id,
            profile_id="profile.claim.a",
            profile_revision_id="rev.profile.claim.a",
            recommended_gate=Gate.research_only,
            current_gate=Gate(target_gate),
            required_actions=[],
            transitions=[
                PromotionTransition(
                    event_id="review.stub",
                    from_gate=Gate.draft,
                    to_gate=Gate(target_gate),
                    actor=actor,
                    actor_role=ReviewActorRole(str(actor_role)),
                    override=override,
                    rationale=rationale,
                    notes=notes,
                    profile_id="profile.claim.a",
                    profile_revision_id="rev.profile.claim.a",
                    recommended_gate=Gate.research_only,
                )
            ],
        )

    def export_bundle(self, project_id: str) -> ProjectBundleExport:
        return ProjectBundleExport(
            project=self.snapshot,
            claim_graph={"graph_id": "cg.project.mcp_parity"},
            assurance_profiles=[{"profile_id": "profile.claim.a"}],
            review_events={"claim.a": [{"event_id": "review.stub"}]},
            promotion_states={"claim.a": {"current_gate": "research_only"}},
        )

    async def trace_forward(self, project_id: str, claim_id: str) -> dict[str, object]:
        return {"trace": [claim_id], "summary": "forward"}

    async def trace_backward(self, project_id: str, claim_id: str) -> dict[str, object]:
        return {"trace": [claim_id], "summary": "backward"}

    async def detect_gaps(self, project_id: str) -> dict[str, object]:
        return {"gaps": [], "summary": "none"}

    async def assess_soundness(self, project_id: str, claim_id: str | None = None) -> dict[str, object]:
        return {"overall": 0.9, "summary": "sound"}

    def export_graph(self, project_id: str, format: str = "json") -> str:
        return json.dumps({"project_id": project_id, "format": format})


async def main() -> None:
    api = StubEngineAPI()
    reset_runtime(engine_api=api)

    created = parse_tool_payload(
        await server.mcp.call_tool(
            "project.create",
            {"name": "parity", "domain": "general", "description": "stub"},
        )
    )
    assert created["data"]["project"] == api.create_project(
        "parity",
        "general",
        "stub",
    ).model_dump(mode="json", exclude_none=True)

    ingest = parse_tool_payload(
        await server.mcp.call_tool(
            "document.ingest",
            {"project_id": api.project.project_id, "text": "ingest"},
        )
    )
    ingest_done = await wait_for_job(ingest["data"]["job"]["job_id"])
    assert ingest_done["data"]["job"]["result"] == (
        await api.ingest_document(api.project.project_id, "ingest")
    ).model_dump(mode="json", exclude_none=True)

    structured = parse_tool_payload(
        await server.mcp.call_tool(
            "claim.structure",
            {"project_id": api.project.project_id, "user_input": "structure"},
        )
    )
    structured_done = await wait_for_job(structured["data"]["job"]["job_id"])
    assert structured_done["data"]["job"]["result"] == (
        await api.run_claim_structuring(api.project.project_id, "structure")
    ).model_dump(mode="json", exclude_none=True)

    formalized = parse_tool_payload(
        await server.mcp.call_tool(
            "formalize.dual",
            {"project_id": api.project.project_id, "claim_id": "claim.a"},
        )
    )
    formalized_done = await wait_for_job(formalized["data"]["job"]["job_id"])
    assert formalized_done["data"]["job"]["result"] == (
        await api.run_dual_formalization(api.project.project_id, "claim.a")
    ).model_dump(mode="json", exclude_none=True)

    audited = parse_tool_payload(
        await server.mcp.call_tool(
            "audit.run",
            {"project_id": api.project.project_id, "claim_id": "claim.a"},
        )
    )
    audited_done = await wait_for_job(audited["data"]["job"]["job_id"])
    direct_audit = await api.run_audit(api.project.project_id, "claim.a")
    assert audited_done["data"]["job"]["result"] == direct_audit.model_dump(
        mode="json",
        exclude_none=True,
    )

    recomputed = parse_tool_payload(
        await server.mcp.call_tool(
            "profile.recompute",
            {
                "project_id": api.project.project_id,
                "claim_id": "claim.a",
                "audit_job_id": audited["data"]["job"]["job_id"],
            },
        )
    )
    assert recomputed["data"]["profile"] == api.recompute_profile(
        api.project.project_id,
        "claim.a",
        direct_audit.model_dump(mode="json", exclude_none=True),
    ).model_dump(mode="json", exclude_none=True)

    promoted = parse_tool_payload(
        await server.mcp.call_tool(
            "promotion.transition",
            {
                "project_id": api.project.project_id,
                "claim_id": "claim.a",
                "target_gate": "research_only",
                "actor": "reviewer@example.com",
                "actor_role": "reviewer",
                "override": False,
                "rationale": "",
                "notes": "ready",
            },
        )
    )
    expected_promotion = api.approve_promotion(
        api.project.project_id,
        "claim.a",
        target_gate="research_only",
        actor="reviewer@example.com",
        actor_role="reviewer",
        notes="ready",
    ).model_dump(mode="json", exclude_none=True)
    actual_promotion = promoted["data"]["promotion_state"]
    assert actual_promotion["claim_id"] == expected_promotion["claim_id"]
    assert actual_promotion["current_gate"] == expected_promotion["current_gate"]
    assert actual_promotion["recommended_gate"] == expected_promotion["recommended_gate"]
    assert actual_promotion["transitions"][-1]["to_gate"] == expected_promotion["transitions"][-1]["to_gate"]
    assert actual_promotion["transitions"][-1]["actor"] == expected_promotion["transitions"][-1]["actor"]
    assert (
        actual_promotion["transitions"][-1]["actor_role"]
        == expected_promotion["transitions"][-1]["actor_role"]
    )
    assert actual_promotion["transitions"][-1]["notes"] == expected_promotion["transitions"][-1]["notes"]

    bundle = parse_tool_payload(
        await server.mcp.call_tool(
            "bundle.export",
            {"project_id": api.project.project_id},
        )
    )
    assert bundle["data"]["bundle"] == api.export_bundle(api.project.project_id).model_dump(
        mode="json",
        exclude_none=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
