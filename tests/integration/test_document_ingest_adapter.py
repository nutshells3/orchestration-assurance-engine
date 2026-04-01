"""Integration smoke for canonical document ingest over claim-tracer style payloads."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from document ingest test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.claim_trace_service import ClaimTraceService  # noqa: E402
from formal_claim_engine.config import PipelineConfig  # noqa: E402
from formal_claim_engine.llm_client import LLMClient, LLMResponse  # noqa: E402
from formal_claim_engine.store import canonical_artifact_id  # noqa: E402


class StubLLM(LLMClient):
    def __init__(self, payload: dict):
        super().__init__()
        self.payload = payload

    async def complete(self, *args, **kwargs):
        return LLMResponse(text=json.dumps(self.payload), raw=None, usage=None)


class FailingLLM(LLMClient):
    async def complete(self, *args, **kwargs):
        raise RuntimeError("LLM unavailable")


def main() -> None:
    payload = {
        "claims": [
            {
                "id": "premise_a",
                "title": "Article 5 applies",
                "statement": "Article 5 governs the dispute under the stated jurisdictional facts.",
                "role": "statute",
                "source_location": "doc:complaint#p1",
                "source_text": "Article 5 governs the dispute.",
                "scope": "jurisdictional dispute",
                "depth": 0,
            },
            {
                "id": "claim_b",
                "title": "Termination was unauthorized",
                "statement": "The contract termination lacked a valid Article 5 basis.",
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
        service = ClaimTraceService(
            config=PipelineConfig(data_dir=tmp),
            llm=StubLLM(payload),
            data_dir=tmp,
        )
        project = service.create_project("legal-doc", "legal", "document ingest smoke")
        result = asyncio.run(
            service.ingest_document(
                project.id,
                "Article 5 governs the dispute. Therefore termination lacked a valid basis.",
            )
        )

        assert result["claims_added"] == 2, result
        assert result["relations_added"] == 1, result
        assert result["evidence_items_added"] == 2, result
        assert result["mapping_report"]["imported_claim_count"] == 2, result
        assert result["ingest_bundle"]["uncertainty"]["connector_is_not_claim_graph_owner"]
        assert len(result["ingest_bundle"]["raw_units"]) == 2, result["ingest_bundle"]
        assert result["evaluation_evidence_added"] == 0, result
        assert result["source_document"]["source_kind"] == "inline_text", result
        assert result["source_mapping_ref"]["artifact_kind"] == "source_mapping_bundle", result
        mappings = result["mapping_report"]["claim_mappings"]
        assert mappings[0]["source_role"] == "statute", mappings
        assert mappings[0]["proposed_claim_class"] == "assumption", mappings

        project_record, graph_data = service.repository.load(project.id)
        assert project_record is not None
        assert graph_data is not None
        assert graph_data["graph_policy"]["default_assumption_carrier"] == "premise"
        assert len(graph_data["claims"]) == 2, graph_data
        claim_ids = {
            canonical_artifact_id(claim["claim_id"]) for claim in graph_data["claims"]
        }
        assert all(
            claim_id.startswith(f"claim.{project.id.split('.')[-1]}.")
            for claim_id in claim_ids
        ), claim_ids
        assert graph_data["root_claim_ids"], graph_data
        source_documents = service.list_source_documents(project.id)
        assert len(source_documents) == 1, source_documents
        inline_document_id = source_documents[0]["document_id"]
        inline_bundle = service.load_source_mapping_bundle(project.id, inline_document_id)
        assert inline_bundle["artifact"]["source_document"]["document_id"] == inline_document_id

    local_payload = {
        "claims": [
            {
                "id": "premise_a",
                "title": "Article 5 applies",
                "statement": "Article 5 governs the dispute under the stated jurisdictional facts.",
                "role": "statute",
                "source_text": "Article 5 governs the dispute.",
                "scope": "jurisdictional dispute",
                "depth": 0,
            },
            {
                "id": "claim_b",
                "title": "Termination was unauthorized",
                "statement": "The contract termination lacked a valid Article 5 basis.",
                "role": "holding",
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
        document_path = Path(tmp) / "complaint.md"
        document_path.write_text(
            "Section 1. Article 5 governs the dispute.\n"
            "Section 2. Article 5 governs the dispute.\n"
            "Termination lacked a valid basis.\n",
            encoding="utf-8",
        )
        service = ClaimTraceService(
            config=PipelineConfig(data_dir=tmp),
            llm=StubLLM(local_payload),
            data_dir=tmp,
        )
        project = service.create_project("legal-doc-file", "legal", "document import smoke")
        first = asyncio.run(service.import_local_document(project.id, str(document_path)))
        second = asyncio.run(service.import_local_document(project.id, str(document_path)))

        assert first["source_document"]["source_kind"] == "local_file", first
        assert first["source_document"]["origin_path"].endswith("complaint.md"), first
        assert first["mapping_report"]["ambiguous_anchor_count"] == 1, first
        first_mapping = first["mapping_report"]["claim_mappings"][0]
        assert first_mapping["citation_anchor"]["status"] == "ambiguous", first_mapping
        assert first_mapping["citation_anchor"]["source_ref"] == first["source_document"]["document_ref"], first_mapping
        second_ref = second["source_mapping_ref"]
        assert second["source_document"]["document_id"] == first["source_document"]["document_id"], second
        assert second_ref["artifact_id"] == first["source_mapping_ref"]["artifact_id"], second_ref
        assert second_ref["revision_id"] != first["source_mapping_ref"]["revision_id"], second_ref
        assert second["evaluation_evidence_added"] == 0, second
        source_documents = service.list_source_documents(project.id)
        assert len(source_documents) == 1, source_documents
        bundle = service.load_source_mapping_bundle(
            project.id,
            first["source_document"]["document_id"],
        )
        assert bundle["artifact"]["source_document"]["document_id"] == first["source_document"]["document_id"], bundle
        assert bundle["artifact"]["project_id"] == project.id, bundle

    with tempfile.TemporaryDirectory() as tmp:
        service = ClaimTraceService(
            config=PipelineConfig(data_dir=tmp),
            llm=StubLLM(local_payload),
            data_dir=tmp,
        )
        project = service.create_project("uploaded-doc", "legal", "uploaded text smoke")
        uploaded = asyncio.run(
            service.import_uploaded_document(
                project.id,
                file_name="judgment.txt",
                raw_bytes=(
                    "Article 5 governs the dispute.\n"
                    "Termination lacked a valid basis.\n"
                ).encode("utf-8"),
                media_type="text/plain",
            )
        )
        assert uploaded["source_document"]["source_kind"] == "uploaded_file", uploaded
        assert uploaded["source_document"]["display_name"] == "judgment.txt", uploaded
        uploaded_again = asyncio.run(
            service.import_uploaded_document(
                project.id,
                file_name="judgment.txt",
                raw_bytes=(
                    "Article 5 governs the dispute.\n"
                    "Termination lacked a valid basis.\n"
                ).encode("utf-8"),
                media_type="text/plain",
            )
        )
        assert uploaded_again["source_document"]["document_id"] == uploaded["source_document"]["document_id"], uploaded_again
        assert uploaded_again["source_mapping_ref"]["artifact_id"] == uploaded["source_mapping_ref"]["artifact_id"], uploaded_again

    previous_pypdf = sys.modules.get("pypdf")
    fake_pypdf = type(sys)("pypdf")

    class FakePage:
        def __init__(self, text: str):
            self.text = text

        def extract_text(self):
            return self.text

    class FakeReader:
        def __init__(self, stream):
            self.pages = [
                FakePage("Article 5 governs the dispute."),
                FakePage("Termination lacked a valid basis."),
            ]

    fake_pypdf.PdfReader = FakeReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        with tempfile.TemporaryDirectory() as tmp:
            service = ClaimTraceService(
                config=PipelineConfig(data_dir=tmp),
                llm=StubLLM(local_payload),
                data_dir=tmp,
            )
            project = service.create_project("uploaded-pdf", "legal", "uploaded pdf smoke")
            uploaded_pdf = asyncio.run(
                service.import_uploaded_document(
                    project.id,
                    file_name="judgment.pdf",
                    raw_bytes=b"%PDF-1.4 mock payload",
                    media_type="application/pdf",
                )
            )
            assert uploaded_pdf["source_document"]["source_kind"] == "uploaded_file", uploaded_pdf
            assert uploaded_pdf["source_document"]["media_type"] == "application/pdf", uploaded_pdf
            assert uploaded_pdf["claims_added"] == 2, uploaded_pdf
    finally:
        if previous_pypdf is None:
            sys.modules.pop("pypdf", None)
        else:
            sys.modules["pypdf"] = previous_pypdf

    with tempfile.TemporaryDirectory() as tmp:
        service = ClaimTraceService(
            config=PipelineConfig(data_dir=tmp),
            llm=FailingLLM(),
            data_dir=tmp,
        )
        project = service.create_project("fallback-doc", "general", "heuristic fallback smoke")
        fallback = asyncio.run(
            service.ingest_document(
                project.id,
                "First claim. Therefore the second claim follows from the first claim.",
            )
        )
        assert fallback["claims_added"] >= 1, fallback
        first_claim_notes = (
            fallback["ingest_bundle"]["claim_candidates"][0].get("notes") or []
        )
        assert any("fallback_reason:" in str(item) for item in first_claim_notes), first_claim_notes

    labeled_depth_payload = {
        "claims": [
            {
                "id": "premise_a",
                "title": "Foundational premise",
                "statement": "A foundational legal premise appears in the source.",
                "role": "premise",
                "source_text": "A foundational legal premise appears in the source.",
                "scope": "jurisdictional dispute",
                "depth": "foundational",
            },
            {
                "id": "claim_b",
                "title": "Derived holding",
                "statement": "A derived holding follows from the premise.",
                "role": "holding",
                "source_text": "A derived holding follows from the premise.",
                "scope": "jurisdictional dispute",
                "depth": "conclusion",
            },
        ],
        "relations": [
            {
                "source_id": "premise_a",
                "target_id": "claim_b",
                "relation_type": "supports",
                "strength": "authoritative",
                "rationale": "The premise supports the holding.",
            }
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        service = ClaimTraceService(
            config=PipelineConfig(data_dir=tmp),
            llm=StubLLM(labeled_depth_payload),
            data_dir=tmp,
        )
        project = service.create_project("depth-label-doc", "legal", "string depth smoke")
        result = asyncio.run(
            service.ingest_document(
                project.id,
                "A foundational legal premise appears in the source. A derived holding follows from the premise.",
            )
        )
        assert result["claims_added"] == 2, result
        _, graph_data = service.repository.load(project.id)
        assert graph_data is not None
        depth_tags = {
            claim["title"]: next(
                (
                    tag.split(":", 1)[1]
                    for tag in (claim.get("tags") or [])
                    if str(tag).startswith("tracer_depth:")
                ),
                None,
            )
            for claim in graph_data["claims"]
        }
        assert depth_tags["Foundational premise"] == "0", depth_tags
        assert depth_tags["Derived holding"] == "2", depth_tags


if __name__ == "__main__":
    main()
