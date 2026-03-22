"""Integration smoke for the canonical MCP thin facade surface."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "mcp-server" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from MCP server test.")


REPO_ROOT = resolve_repo_root()
MCP_SRC = REPO_ROOT / "services" / "mcp-server" / "src"
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

for src in (MCP_SRC, ENGINE_SRC):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from formal_claim_engine.engine_api import FormalClaimEngineAPI  # noqa: E402
from formal_claim_engine.llm_client import LLMClient, LLMResponse  # noqa: E402
from formal_claim_mcp_server import reset_runtime  # noqa: E402
from formal_claim_mcp_server.jobs import McpJobStore  # noqa: E402
from formal_claim_mcp_server import server  # noqa: E402


class StubLLM(LLMClient):
    def __init__(self, payload: dict[str, object], *, delay_seconds: float = 0.0):
        super().__init__()
        self.payload = payload
        self.delay_seconds = delay_seconds

    async def complete(self, *args, **kwargs):
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return LLMResponse(text=json.dumps(self.payload), raw=None, usage=None)


def parse_tool_payload(result):
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    raise AssertionError(f"Unexpected MCP tool result: {result!r}")


def parse_resource_payload(contents):
    assert contents, contents
    content = contents[0].content
    if isinstance(content, str):
        return json.loads(content)
    return content


async def wait_for_job(job_id: str) -> dict[str, object]:
    for _ in range(300):
        payload = parse_tool_payload(await server.mcp.call_tool("job.get", {"job_id": job_id}))
        job = payload["data"]["job"]
        if job["status"] in {"completed", "failed", "cancelled", "killed", "timed_out"} and job.get("completed_at"):
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish.")


def make_session(root: Path, session_name: str) -> str:
    session_dir = root / session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "ROOT").write_text(
        f'session "{session_name}" = HOL +\n  theories\n    "{session_name}"\n',
        encoding="utf-8",
    )
    (session_dir / f"{session_name}.thy").write_text(
        f"theory {session_name} imports Main\nbegin\n\nlemma smoke: \"True\" by simp\n\nend\n",
        encoding="utf-8",
    )
    return str(session_dir)


async def main() -> None:
    ingest_payload = {
        "claims": [
            {
                "id": "premise_a",
                "title": "Article 5 applies",
                "statement": "Article 5 governs the dispute under the stated facts.",
                "role": "statute",
                "source_location": "doc:complaint#p1",
                "source_text": "Article 5 governs the dispute.",
                "scope": "jurisdictional dispute",
                "depth": 0,
            },
            {
                "id": "claim_b",
                "title": "Termination was unauthorized",
                "statement": "The termination lacked a valid Article 5 basis.",
                "role": "holding",
                "source_location": "doc:complaint#p2",
                "source_text": "Termination lacked a valid basis.",
                "scope": "jurisdictional dispute",
                "depth": 1,
            },
        ],
        "relations": [
            {
                "source_id": "premise_a",
                "target_id": "claim_b",
                "relation_type": "supports",
                "strength": "authoritative",
                "rationale": "Article 5 is the cited basis for the holding.",
            }
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        api = FormalClaimEngineAPI(data_dir=tmp, llm=StubLLM(ingest_payload))
        reset_runtime(engine_api=api)

        tools = await server.mcp.list_tools()
        tool_names = {tool.name for tool in tools}
        assert "project.create" in tool_names, tool_names
        assert "document.ingest" in tool_names, tool_names
        assert "claim.structure" in tool_names, tool_names
        assert "proof.run.start" in tool_names, tool_names
        assert "job.get" in tool_names, tool_names
        assert "job.cancel" in tool_names, tool_names
        assert "job.kill" in tool_names, tool_names
        assert "add_claim" not in tool_names, tool_names
        assert "link_claims" not in tool_names, tool_names

        resources = await server.mcp.list_resource_templates()
        resource_uris = {template.uriTemplate for template in resources}
        assert "project://{project_id}" in resource_uris, resource_uris
        assert "claim-graph://{project_id}" in resource_uris, resource_uris
        assert "profile://{project_id}/{claim_id}" in resource_uris, resource_uris

        created = parse_tool_payload(
            await server.mcp.call_tool(
                "project.create",
                {"name": "mcp-smoke", "domain": "general", "description": "surface"},
            )
        )
        assert created["ok"] is True, created
        project_id = created["data"]["project"]["project_id"]

        opened = parse_tool_payload(
            await server.mcp.call_tool("project.open", {"project_id": project_id})
        )
        assert opened["ok"] is True, opened
        assert opened["data"]["project"]["project_id"] == project_id

        ingest_started = parse_tool_payload(
            await server.mcp.call_tool(
                "document.ingest",
                {
                    "project_id": project_id,
                    "text": "Article 5 governs the dispute. Therefore termination lacked a valid basis.",
                },
            )
        )
        assert ingest_started["ok"] is True, ingest_started
        ingest_job_id = ingest_started["data"]["job"]["job_id"]
        ingest_done = await wait_for_job(ingest_job_id)
        ingest_job = ingest_done["data"]["job"]
        assert ingest_job["status"] == "completed", ingest_job
        assert ingest_job["result"]["claims_added"] == 2, ingest_job
        assert ingest_job["artifact_refs"], ingest_job

        project_resource = parse_resource_payload(
            await server.mcp.read_resource(f"project://{project_id}")
        )
        assert project_resource["payload"]["project_id"] == project_id, project_resource

        graph_resource = parse_resource_payload(
            await server.mcp.read_resource(f"claim-graph://{project_id}")
        )
        assert graph_resource["revision_status"] == "latest", graph_resource
        assert len(graph_resource["payload"]["claims"]) == 2, graph_resource

        missing_project = parse_tool_payload(
            await server.mcp.call_tool("project.open", {"project_id": "proj.missing"})
        )
        assert missing_project["ok"] is False, missing_project
        assert missing_project["error"]["code"] == "artifact_missing", missing_project

        missing_job = parse_tool_payload(
            await server.mcp.call_tool("job.get", {"job_id": "job.missing"})
        )
        assert missing_job["ok"] is False, missing_job
        assert missing_job["error"]["code"] == "job_failed", missing_job

        success_session = make_session(Path(tmp), "McpSuccess")
        proof_started = parse_tool_payload(
            await server.mcp.call_tool(
                "proof.run.start",
                {
                    "session_name": "McpSuccess",
                    "session_dir": success_session,
                    "target_theory": "McpSuccess",
                    "target_theorem": "demo",
                    "wall_timeout_seconds": 5,
                    "idle_timeout_seconds": 0,
                },
            )
        )
        assert proof_started["ok"] is True, proof_started
        proof_done = await wait_for_job(proof_started["data"]["job"]["job_id"])
        assert proof_done["data"]["job"]["status"] == "completed", proof_done

        cancel_session = make_session(Path(tmp), "McpCancel")
        cancel_started = parse_tool_payload(
            await server.mcp.call_tool(
                "proof.run.start",
                {
                    "session_name": "McpCancel",
                    "session_dir": cancel_session,
                    "target_theory": "McpCancel",
                    "target_theorem": "stubborn",
                    "wall_timeout_seconds": 20,
                    "idle_timeout_seconds": 0,
                    "cancel_grace_seconds": 1,
                },
            )
        )
        cancel_job_id = cancel_started["data"]["job"]["job_id"]
        for _ in range(40):
            job_payload = parse_tool_payload(await server.mcp.call_tool("job.get", {"job_id": cancel_job_id}))
            if job_payload["data"]["job"]["status"] == "running":
                break
            await asyncio.sleep(0.05)
        cancel_requested = parse_tool_payload(
            await server.mcp.call_tool("job.cancel", {"job_id": cancel_job_id})
        )
        assert cancel_requested["ok"] is True, cancel_requested
        cancel_done = await wait_for_job(cancel_job_id)
        assert cancel_done["data"]["job"]["status"] in {"cancelled", "killed"}, cancel_done

    with tempfile.TemporaryDirectory() as tmp:
        slow_api = FormalClaimEngineAPI(
            data_dir=tmp,
            llm=StubLLM(ingest_payload, delay_seconds=0.2),
        )
        reset_runtime(engine_api=slow_api)
        server.runtime.jobs = McpJobStore(max_concurrent_jobs=1, max_queued_jobs=1)

        created = parse_tool_payload(
            await server.mcp.call_tool(
                "project.create",
                {"name": "mcp-capacity", "domain": "general", "description": "limits"},
            )
        )
        project_id = created["data"]["project"]["project_id"]

        first = parse_tool_payload(
            await server.mcp.call_tool(
                "document.ingest",
                {"project_id": project_id, "text": "First queued job."},
            )
        )
        assert first["ok"] is True, first

        second = parse_tool_payload(
            await server.mcp.call_tool(
                "document.ingest",
                {"project_id": project_id, "text": "Second queued job."},
            )
        )
        assert second["ok"] is False, second
        assert second["error"]["code"] == "capacity_exceeded", second

        await wait_for_job(first["data"]["job"]["job_id"])


if __name__ == "__main__":
    asyncio.run(main())
