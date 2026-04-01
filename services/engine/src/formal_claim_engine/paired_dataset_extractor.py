"""PFX-005 / PFX-007 -- Paired Dataset Extraction (v2).

Extracts aligned pairs of PrefixSliceTextV1 and PrefixSliceGraphV1 from
trace data for multimodal training.  Each pair shares the same step_id,
trace_id, and available_artifacts, built from the same temporal prefix.

PFX-005: Updated to use v2 builder outputs with consistent trace_id,
step_id, available_artifacts, and gold_action across both projections.

Output formats
--------------
* text_slices.jsonl  -- one PrefixSliceTextV1 per line
* graph_slices.jsonl -- one PrefixSliceGraphV1 per line
* pairs_manifest.json -- alignment index with metadata

Invariants
----------
* Same temporal / domain rules as PrefixSliceBuilder and
  PrefixSliceGraphBuilder: no future data, no source_domain.
* Both projections at the same step MUST have identical trace_id,
  step_id, and available_artifacts.
* gold_action comes from the event stream, not invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from .prefix_slice_builder import PrefixSliceBuilder
except ImportError:
    PrefixSliceBuilder = None  # type: ignore[assignment,misc]

from .prefix_slice_graph_builder import PrefixSliceGraphBuilder


class PairedDatasetExtractor:
    """Extracts paired (text, graph, action_mask) samples from trace.

    Produces aligned pairs of PrefixSliceTextV1 and PrefixSliceGraphV1
    for multimodal training.
    """

    def __init__(
        self,
        trace: dict[str, Any],
        transition_log: list[dict[str, Any]],
    ) -> None:
        """
        Args:
            trace: PipelineTraceV1 dict (model-safe, no domain).
            transition_log: list of pipeline-event dicts.
        """
        self.trace = trace
        self.transition_log = list(transition_log)
        if PrefixSliceBuilder is None:
            raise ImportError(
                "PairedDatasetExtractor requires PrefixSliceBuilder "
                "(prefix_slice_builder.py).  Install or merge PFX-001 first."
            )
        self._text_builder = PrefixSliceBuilder(trace, transition_log)
        self._graph_builder = PrefixSliceGraphBuilder(trace, transition_log)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_pairs(self) -> list[dict[str, Any]]:
        """Extract paired samples (PFX-005 v2).

        Each pair is a dict with:
            text_slice:  PrefixSliceTextV1 dict
            graph_slice: PrefixSliceGraphV1 dict
            trace_id:    str  (shared)
            step_id:     str  (shared)
            gold_action: dict | None (shared)
            available_artifacts: list[str] (shared)

        PFX-005 invariants enforced:
        - Both projections share identical trace_id, step_id, available_artifacts
        - gold_action is consistent across both projections
        """
        text_slices = self._text_builder.extract_slices()
        graph_slices = self._graph_builder.extract_graph_slices()

        # B10: Both builders use event_seq order and controllable-only
        # filtering, so indices are aligned by controllable cutoff.
        pairs: list[dict[str, Any]] = []
        for text_slice, graph_slice in zip(text_slices, graph_slices):
            step_id = text_slice["step_id"]
            trace_id = text_slice["trace_id"]

            # PFX-005: enforce parity between text and graph projections
            assert step_id == graph_slice["step_id"], (
                f"Slice alignment error: text step_id={text_slice['step_id']} "
                f"vs graph step_id={graph_slice['step_id']}"
            )
            assert trace_id == graph_slice["trace_id"], (
                f"Slice alignment error: text trace_id={text_slice['trace_id']} "
                f"vs graph trace_id={graph_slice['trace_id']}"
            )
            assert text_slice["available_artifacts"] == graph_slice["available_artifacts"], (
                f"Artifact alignment error at {step_id}: "
                f"text={text_slice['available_artifacts']} "
                f"vs graph={graph_slice['available_artifacts']}"
            )

            # gold_action should be consistent -- use text builder's as canonical
            gold_action = text_slice.get("gold_action")

            pairs.append({
                "text_slice": text_slice,
                "graph_slice": graph_slice,
                "trace_id": trace_id,
                "step_id": step_id,
                "gold_action": gold_action,
                "available_artifacts": text_slice["available_artifacts"],
            })

        return pairs

    def write_paired_dataset(
        self,
        output_dir: str,
        format: str = "jsonl",
    ) -> dict[str, Any]:
        """Write paired dataset to files.

        Produces:
          - text_slices.jsonl   -- one text slice per line
          - graph_slices.jsonl  -- one graph slice per line
          - pairs_manifest.json -- alignment index

        Args:
            output_dir: Directory to write files to (created if needed).
            format: Output format (currently only "jsonl" supported).

        Returns:
            Manifest dict with metadata about the written dataset.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        pairs = self.extract_pairs()

        # Write text slices
        text_path = out / "text_slices.jsonl"
        with text_path.open("w", encoding="utf-8") as f:
            for pair in pairs:
                f.write(json.dumps(pair["text_slice"], default=str) + "\n")

        # Write graph slices
        graph_path = out / "graph_slices.jsonl"
        with graph_path.open("w", encoding="utf-8") as f:
            for pair in pairs:
                f.write(json.dumps(pair["graph_slice"], default=str) + "\n")

        # Build and write manifest
        manifest = self._build_manifest(pairs)
        manifest_path = out / "pairs_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, default=str),
            encoding="utf-8",
        )

        return manifest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_manifest(pairs: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the pairs_manifest.json content (PFX-005 v2).

        The manifest records:
        - total_pairs: number of aligned pairs
        - trace_id: the trace ID (consistent across all pairs)
        - pairs: list of {index, step_id, trace_id, has_gold_action} entries
        - files: the filenames produced
        """
        trace_id = ""
        if pairs:
            trace_id = pairs[0].get("trace_id") or pairs[0]["text_slice"].get("trace_id", "")

        entries: list[dict[str, Any]] = []
        for i, pair in enumerate(pairs):
            entries.append({
                "index": i,
                "step_id": pair["step_id"],
                "trace_id": pair.get("trace_id", trace_id),
                "has_gold_action": pair["gold_action"] is not None,
            })

        return {
            "schema_version": "PairedDatasetManifestV2",
            "total_pairs": len(pairs),
            "trace_id": trace_id,
            "pairs": entries,
            "files": {
                "text_slices": "text_slices.jsonl",
                "graph_slices": "graph_slices.jsonl",
                "manifest": "pairs_manifest.json",
            },
        }
