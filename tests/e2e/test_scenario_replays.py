"""Replay theorem, paper, and legal scenarios and verify MCP parity on the same projects."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "scripts" / "release" / "replay_scenarios.py").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from scenario replay test.")


REPO_ROOT = resolve_repo_root()
MCP_SRC = REPO_ROOT / "services" / "mcp-server" / "src"
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"
RELEASE_SCRIPTS = REPO_ROOT / "scripts" / "release"

for src in (MCP_SRC, ENGINE_SRC, RELEASE_SCRIPTS):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from formal_claim_engine.fixture_runtime import build_engine_api  # noqa: E402
from formal_claim_mcp_server import reset_runtime  # noqa: E402
from formal_claim_mcp_server import server  # noqa: E402
import replay_scenarios  # noqa: E402


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


async def verify_mcp(summary: dict[str, object]) -> None:
    fixture_path = str(summary["fixture_path"])
    data_dir = str(summary["data_dir"])
    project_id = str(summary["project_id"])
    claim_id = str(summary["claim_id"])
    reference_id = str(summary["reference_id"])
    final_gate = str(summary["final_promotion_gate"])
    claim_count = int(summary["claim_count"])
    evaluation_evidence_count = int(summary.get("evaluation_evidence_count", 0))

    reset_runtime(
        data_dir=data_dir,
        engine_api=build_engine_api(data_dir=data_dir, fixture_path=fixture_path),
    )

    listed = parse_tool_payload(await server.mcp.call_tool("project.list", {}))
    project_ids = {item["project_id"] for item in listed["data"]["projects"]}
    assert project_id in project_ids, listed

    opened = parse_tool_payload(
        await server.mcp.call_tool("project.open", {"project_id": project_id})
    )
    assert opened["data"]["project"]["project_id"] == project_id, opened

    exported = parse_tool_payload(
        await server.mcp.call_tool("bundle.export", {"project_id": project_id})
    )
    bundle = exported["data"]["bundle"]
    assert bundle["promotion_states"][claim_id]["current_gate"] == final_gate, bundle
    assert len(bundle.get("evaluation_evidence") or []) == evaluation_evidence_count, bundle

    project_resource = parse_resource_payload(
        await server.mcp.read_resource(f"project://{project_id}")
    )
    assert project_resource["payload"]["project_id"] == project_id, project_resource

    graph_resource = parse_resource_payload(
        await server.mcp.read_resource(f"claim-graph://{project_id}")
    )
    assert len(graph_resource["payload"]["claims"]) >= claim_count, graph_resource

    profile_resource = parse_resource_payload(
        await server.mcp.read_resource(f"profile://{project_id}/{claim_id}")
    )
    assert profile_resource["payload"]["claim_id"] == claim_id, profile_resource

    audit_resource = parse_resource_payload(
        await server.mcp.read_resource(f"audit-report://{project_id}/{claim_id}")
    )
    assert audit_resource["payload"]["event_type"] == "audit_workflow", audit_resource

    bundle_resource = parse_resource_payload(
        await server.mcp.read_resource(f"bundle://{project_id}")
    )
    assert (
        bundle_resource["payload"]["promotion_states"][claim_id]["current_gate"] == final_gate
    ), bundle_resource
    assert (
        len(bundle_resource["payload"].get("evaluation_evidence") or [])
        == evaluation_evidence_count
    ), bundle_resource

    evidence_items = server.runtime.engine_api.list_evaluation_evidence(project_id)
    assert len(evidence_items) == evaluation_evidence_count, evidence_items
    if evaluation_evidence_count:
        evidence_id = str(evidence_items[0]["evidence_id"])
        evidence = server.runtime.engine_api.get_evaluation_evidence(project_id, evidence_id)
        assert evidence["evidence_id"] == evidence_id, evidence
        claim_links = server.runtime.engine_api.get_claim_evidence_links(project_id, claim_id)
        assert claim_links, claim_links
        reference_links = server.runtime.engine_api.get_reference_evidence_links(project_id, reference_id)
        assert reference_links, reference_links


async def main() -> None:
    summaries = replay_scenarios.replay_scenarios(reuse_existing=True)
    assert len(summaries) == 3, summaries
    for summary in summaries:
        await verify_mcp(summary)


if __name__ == "__main__":
    asyncio.run(main())
