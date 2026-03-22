"""Fixture-aware engine runtime builder for end-to-end scenario replay."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .claim_trace_service import ClaimTraceService
from .config import PipelineConfig
from .engine_api import FormalClaimEngineAPI
from .llm_client import LLMClient, LLMResponse
from .orchestrator import PipelineOrchestrator
from .proof_protocol import FilesystemProofAdapter
from .store import ArtifactStore


SCENARIO_FIXTURE_ENV = "FORMAL_CLAIM_SCENARIO_FIXTURE"
FIXTURE_TIMESTAMP = "2026-03-21T00:00:00Z"
LATEST_SOURCE_DOCUMENT_REF = "__LATEST_SOURCE_DOCUMENT_REF__"
LATEST_DOCUMENT_ID = "__LATEST_DOCUMENT_ID__"
LATEST_DOCUMENT_TITLE = "__LATEST_DOCUMENT_TITLE__"

DEFAULT_GRAPH_POLICY = {
    "default_assumption_carrier": "premise",
    "allow_global_axioms": False,
    "require_backtranslation_review": True,
    "require_dual_formalization_for_core_claims": False,
}
DEFAULT_CLAIM_POLICY = {
    "allowed_assumption_carriers": ["premise", "locale"],
    "global_axiom_allowed": False,
    "sorry_allowed_in_scratch": True,
    "sorry_allowed_in_mainline": False,
}
DEFAULT_REVIEWER_ROLES = ["claim_graph_agent", "human_reviewer"]


class FixtureLLM(LLMClient):
    """Deterministic document-ingest responder for scenario fixtures."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = copy.deepcopy(payload)

    async def complete(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(
            text=json.dumps(self.payload, ensure_ascii=True),
            raw=None,
            usage=None,
        )


class FixtureAgent:
    """Repeatable stub agent that returns deep-copied fixture payloads."""

    def __init__(self, name: str, outputs: list[dict[str, Any]] | dict[str, Any]) -> None:
        payloads = outputs if isinstance(outputs, list) else [outputs]
        self.name = name
        self.outputs = [copy.deepcopy(item) for item in payloads]
        if not self.outputs:
            raise ValueError(f"FixtureAgent {name!r} requires at least one output payload.")

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        del context
        output = copy.deepcopy(self.outputs[0])
        return {
            "role": self.name,
            "output": output,
            "raw_text": json.dumps(output, ensure_ascii=True, default=str),
            "usage": None,
            "lineage": {
                "prompt_identifier": f"fixture:{self.name}",
                "prompt_template_version": "scenario-fixture-v1",
                "provider_adapter_version": "fixture-adapter-v1",
                "response_schema_version": "scenario-fixture-v1",
            },
        }


class FixtureIsabelle:
    """Filesystem-only Isabelle shim used by scenario fixtures."""

    def write_theory(self, session_dir: str, theory_name: str, content: str):
        path = Path(session_dir)
        path.mkdir(parents=True, exist_ok=True)
        theory_path = path / f"{theory_name}.thy"
        theory_path.write_text(content, encoding="utf-8")
        return theory_path

    def write_root(self, session_dir: str, session_name: str, theories: list[str]):
        path = Path(session_dir)
        path.mkdir(parents=True, exist_ok=True)
        root_path = path / "ROOT"
        theories_block = "\n".join(f'    "{theory}"' for theory in theories)
        root_path.write_text(
            f'session "{session_name}" = HOL +\n  theories\n{theories_block}\n',
            encoding="utf-8",
        )
        return root_path

    def build(self, session_name: str, session_dir: str):
        return type(
            "BuildResult",
            (),
            {
                "success": True,
                "stdout": f"theorem {session_name}_theorem\ndefinition helper\nlocale ctx",
                "stderr": "",
                "sorry_count": 0,
                "oops_count": 0,
                "sorry_locations": [],
                "theorems": [f"{session_name}_theorem"],
                "definitions": ["helper"],
                "locales": ["ctx"],
                "session_fingerprint": f"fixture-fp-{session_name}",
            },
        )()


class FixtureRunnerCli:
    """Structured proof-audit shim built from a compact scenario surface."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture

    def run_audit(self, request_path: Path) -> dict[str, Any]:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        surface = dict(self.fixture.get("runner_surface") or {})
        session_name = str(request["session_name"])
        target_theorem = str(request["target_theorem"])
        session_dir = str(request["session_dir"])
        proof_variants = dict(surface.get("proof_variants") or {})
        counterexample_outcome = str(
            surface.get("counterexample_outcome")
            or surface.get("nitpick_outcome")
            or "no_countermodel_found"
        )
        proof_search_hints = list(
            surface.get("proof_search_hints")
            or surface.get("sledgehammer_hints")
            or ["by simp"]
        )
        return {
            "success": True,
            "session_name": session_name,
            "session_dir": session_dir,
            "target_theorem": target_theorem,
            "trust": {
                "success": True,
                "session": session_name,
                "target_theorem": target_theorem,
                "surface": {
                    "session": session_name,
                    "target_theorem": target_theorem,
                    "direct_theorem_dependencies": list(
                        surface.get("direct_theorem_dependencies")
                        or ["fixture.context.intro"]
                    ),
                    "transitive_theorem_dependencies": list(
                        surface.get("transitive_theorem_dependencies")
                        or ["fixture.context.intro", "fixture.measure"]
                    ),
                    "dependency_edges": list(surface.get("dependency_edges") or []),
                    "imported_theories": list(
                        surface.get("imported_theories")
                        or [request.get("target_theory") or "Fixture_Theory", "Main"]
                    ),
                    "imported_theory_hotspots": list(
                        surface.get("imported_theory_hotspots") or []
                    ),
                    "oracle_ids": list(surface.get("oracle_ids") or []),
                    "global_axiom_ids": list(surface.get("global_axiom_ids") or []),
                    "reviewed_global_axiom_ids": list(
                        surface.get("reviewed_global_axiom_ids") or []
                    ),
                    "reviewed_exception_ids": list(
                        surface.get("reviewed_exception_ids") or []
                    ),
                    "locale_assumptions": list(surface.get("locale_assumptions") or []),
                    "premise_assumptions": list(surface.get("premise_assumptions") or []),
                    "notes": list(surface.get("notes") or ["fixture trust surface present"]),
                },
                "export_output_dir": str(request_path.parent / "exports"),
                "dump_output_dir": str(request_path.parent / "dump"),
                "notes": list(surface.get("trust_notes") or []),
            },
            "probe_results": [
                {
                    "kind": "counterexample",
                    "session": f"{session_name}_counterexample",
                    "target_theorem": target_theorem,
                    "outcome": counterexample_outcome,
                    "summary": str(
                        surface.get("counterexample_summary")
                        or surface.get("nitpick_summary")
                        or "Counterexample probe found no countermodel."
                    ),
                },
                {
                    "kind": "proofSearch",
                    "session": f"{session_name}_proofsearch",
                    "target_theorem": target_theorem,
                    "outcome": str(
                        surface.get("proof_search_outcome")
                        or surface.get("sledgehammer_outcome")
                        or "hints_available"
                    ),
                    "summary": str(
                        surface.get("proof_search_summary")
                        or surface.get("sledgehammer_summary")
                        or "Proof-search probe returned candidate hints."
                    ),
                    "hints": proof_search_hints,
                },
            ],
            "robustness_harness": {
                "session": session_name,
                "target_theorem": target_theorem,
                "premise_sensitivity": str(
                    proof_variants.get("premise_sensitivity") or "stable"
                ),
                "conclusion_perturbation": str(
                    proof_variants.get("conclusion_perturbation") or "stable"
                ),
                "notes": list(proof_variants.get("notes") or []),
            },
        }


def resolve_fixture_path(fixture_path: str | Path | None = None) -> Path | None:
    candidate = fixture_path or os.environ.get(SCENARIO_FIXTURE_ENV)
    if not candidate:
        return None
    path = Path(candidate).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Scenario fixture not found: {path}")
    return path


def load_scenario_fixture(fixture_path: str | Path) -> dict[str, Any]:
    path = resolve_fixture_path(fixture_path)
    if path is None:
        raise FileNotFoundError("Scenario fixture path is required.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Scenario fixture {path} must contain a JSON object.")
    payload["_fixture_path"] = str(path)
    payload["_fixture_dir"] = str(path.parent)
    return payload


def build_engine_api(
    *,
    data_dir: str | None = None,
    fixture_path: str | Path | None = None,
) -> FormalClaimEngineAPI:
    resolved_fixture = resolve_fixture_path(fixture_path)
    if resolved_fixture is None:
        return FormalClaimEngineAPI(data_dir=data_dir)
    return build_fixture_engine_api(resolved_fixture, data_dir=data_dir)


def build_fixture_engine_api(
    fixture_path: str | Path,
    *,
    data_dir: str | None = None,
) -> FormalClaimEngineAPI:
    fixture = load_scenario_fixture(fixture_path)
    config = PipelineConfig(data_dir=data_dir or "./pipeline_data")
    llm = FixtureLLM(dict(fixture.get("ingest_payload") or {}))

    def orchestrator_factory(
        project_config: PipelineConfig,
        llm_client: LLMClient,
        store: ArtifactStore,
    ) -> PipelineOrchestrator:
        del llm_client
        orchestrator = PipelineOrchestrator(project_config, llm=llm, store=store)
        orchestrator.proof_client = FilesystemProofAdapter(
            project_config,
            builder=FixtureIsabelle(),
            audit_client=FixtureRunnerCli(fixture),
        )
        planner_graph = _build_claim_graph_candidate(project_config, fixture)
        planner_rationale = str(
            fixture.get("planner_rationale")
            or "Fixture planner admitted the canonical scenario claim graph."
        )
        planner_action = str(fixture.get("planner_action") or "admit_claims")
        orchestrator.planner = FixtureAgent(
            "fixture-planner",
            {
                "action": planner_action,
                "rationale": planner_rationale,
                "warnings": list(fixture.get("planner_warnings") or []),
                "claim_graph_update": planner_graph,
            },
        )
        orchestrator.claim_graph_agent = FixtureAgent(
            "fixture-claim-graph-agent",
            planner_graph,
        )
        formalizer_outputs = dict(fixture.get("formalizer_outputs") or {})
        verifier_outputs = dict(fixture.get("verifier_outputs") or {})
        orchestrator.formalizer_a = FixtureAgent(
            "fixture-formalizer-a",
            dict(formalizer_outputs.get("A") or {}),
        )
        orchestrator.formalizer_b = FixtureAgent(
            "fixture-formalizer-b",
            dict(formalizer_outputs.get("B") or {}),
        )
        orchestrator.verifier = FixtureAgent(
            "fixture-verifier",
            [
                dict(verifier_outputs.get("A") or {}),
                dict(verifier_outputs.get("B") or {}),
            ],
        )
        return orchestrator

    return FormalClaimEngineAPI(
        config=config,
        llm=llm,
        data_dir=config.data_dir,
        orchestrator_factory=orchestrator_factory,
    )


def _latest_source_document_context(
    config: PipelineConfig,
    fixture: dict[str, Any],
) -> dict[str, str]:
    service = ClaimTraceService(
        config=config,
        llm=FixtureLLM(dict(fixture.get("ingest_payload") or {})),
        data_dir=config.data_dir,
    )
    documents = service.list_source_documents(config.project_id)
    latest = documents[-1] if documents else {}
    return {
        LATEST_SOURCE_DOCUMENT_REF: str(latest.get("document_ref") or ""),
        LATEST_DOCUMENT_ID: str(latest.get("document_id") or ""),
        LATEST_DOCUMENT_TITLE: str(latest.get("title") or latest.get("display_name") or ""),
    }


def _build_claim_graph_candidate(
    config: PipelineConfig,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    outline = copy.deepcopy(dict(fixture.get("claim_graph_outline") or {}))
    context = _latest_source_document_context(config, fixture)
    claims = [_build_claim_entry(item, context) for item in list(outline.get("claims") or [])]
    relations = [
        {
            "relation_id": str(item["relation_id"]),
            "from_claim_id": str(item["from_claim_id"]),
            "to_claim_id": str(item["to_claim_id"]),
            "relation_type": str(item.get("relation_type") or "depends_on"),
            "status": str(item.get("status") or "active"),
            "required_for_promotion": bool(
                item.get("required_for_promotion", True)
            ),
            "rationale": str(item.get("rationale") or ""),
        }
        for item in list(outline.get("relations") or [])
    ]
    graph_policy = copy.deepcopy(DEFAULT_GRAPH_POLICY)
    if str(fixture.get("domain") or "general") == "formal_proof":
        graph_policy["default_assumption_carrier"] = "locale"
        graph_policy["require_dual_formalization_for_core_claims"] = True
    graph_policy.update(copy.deepcopy(dict(outline.get("graph_policy") or {})))
    root_claim_ids = list(outline.get("root_claim_ids") or [])
    if not root_claim_ids and claims:
        root_claim_ids = [str(claims[-1]["claim_id"])]
    return {
        "schema_version": "1.0.0",
        "graph_id": str(outline.get("graph_id") or f"cg.{fixture['scenario_id'].replace('-', '.')}"),
        "project_id": config.project_id,
        "created_at": FIXTURE_TIMESTAMP,
        "updated_at": FIXTURE_TIMESTAMP,
        "description": str(outline.get("description") or fixture.get("description") or ""),
        "root_claim_ids": root_claim_ids,
        "graph_policy": graph_policy,
        "claims": claims,
        "relations": relations,
    }


def _build_claim_entry(
    spec: dict[str, Any],
    context: dict[str, str],
) -> dict[str, Any]:
    policy = copy.deepcopy(DEFAULT_CLAIM_POLICY)
    policy.update(copy.deepcopy(dict(spec.get("policy") or {})))
    reviewer_roles = list(spec.get("reviewer_roles") or DEFAULT_REVIEWER_ROLES)
    created_by_role = str(spec.get("created_by_role") or "planner")
    source_anchors = [
        _substitute_placeholders(dict(anchor), context)
        for anchor in list(spec.get("source_anchors") or [])
    ]
    return {
        "claim_id": str(spec["claim_id"]),
        "title": str(spec["title"]),
        "nl_statement": str(spec["nl_statement"]),
        "normalized_statement": str(
            spec.get("normalized_statement") or spec["nl_statement"]
        ),
        "intent_gloss": str(spec.get("intent_gloss") or spec["nl_statement"]),
        "claim_class": str(spec.get("claim_class") or "core_claim"),
        "claim_kind": str(spec.get("claim_kind") or "theorem_candidate"),
        "status": str(spec.get("status") or "queued_for_formalization"),
        "formalization_required": bool(spec.get("formalization_required", True)),
        "downstream_kind": str(spec.get("downstream_kind") or "research_then_dev"),
        "priority": int(spec.get("priority") or 50),
        "tags": [str(item) for item in list(spec.get("tags") or [])],
        "notes": [str(item) for item in list(spec.get("notes") or [])],
        "scope": {
            "domain": str(spec.get("scope_domain") or "scenario fixture"),
            "modality": str(spec.get("scope_modality") or "universal"),
            "included_conditions": [
                str(item) for item in list(spec.get("included_conditions") or [])
            ],
            "excluded_conditions": [
                str(item) for item in list(spec.get("excluded_conditions") or [])
            ],
        },
        "semantics_guard": {
            "must_preserve": [
                str(item) for item in list(spec.get("must_preserve") or [])
            ],
            "allowed_weakenings": [
                str(item) for item in list(spec.get("allowed_weakenings") or [])
            ],
            "forbidden_weakenings": [
                str(item) for item in list(spec.get("forbidden_weakenings") or [])
            ],
            "forbidden_strengthenings": [
                str(item) for item in list(spec.get("forbidden_strengthenings") or [])
            ],
            "backtranslation_required": bool(
                spec.get("backtranslation_required", True)
            ),
            "independent_formalizations_required": int(
                spec.get("independent_formalizations_required") or 1
            ),
        },
        "policy": policy,
        "provenance": {
            "created_by_role": created_by_role,
            "source_anchors": source_anchors,
            "last_reviewed_by_role": str(
                spec.get("last_reviewed_by_role") or created_by_role
            ),
            "review_notes": [
                str(item) for item in list(spec.get("review_notes") or [])
            ],
        },
        "owner_role": str(spec.get("owner_role") or created_by_role),
        "reviewer_roles": reviewer_roles,
    }


def _substitute_placeholders(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for key, replacement in context.items():
            result = result.replace(key, replacement)
        return result
    if isinstance(value, list):
        return [_substitute_placeholders(item, context) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _substitute_placeholders(item, context)
            for key, item in value.items()
        }
    return value


__all__ = [
    "SCENARIO_FIXTURE_ENV",
    "build_engine_api",
    "build_fixture_engine_api",
    "load_scenario_fixture",
    "resolve_fixture_path",
]
