"""TRC-010 -- Propagation Traces & Vector-Score Deltas.

Captures propagation traces (how a change in one claim's profile affects
downstream claims) and vector-score deltas (before/after of vector scores
when an event occurs) during runtime.

These are wired into TraceExportBuilder's trace_results section so that
propagation data is included in canonical pipeline exports.

CNT-007: When propagation data is unavailable, an unavailable_reason
is provided instead of bare null.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Canonical unavailable_reason values (CNT-007)
UNAVAILABLE_REASONS = frozenset({
    "not_applicable",
    "computation_failed",
    "runtime_not_captured",
    "exporter_not_implemented",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PropagationCapture:
    """Captures propagation traces and vector-score deltas during runtime.

    Propagation = how a change in one claim's profile affects downstream claims.
    Vector-score delta = before/after of the vector scores when an event occurs.

    CNT-007 compliance: when propagation data is absent, to_trace_results_section
    includes unavailable_reason instead of bare null/empty.
    """

    def __init__(self) -> None:
        self._propagation_traces: list[dict[str, Any]] = []
        self._vector_score_deltas: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Propagation traces
    # ------------------------------------------------------------------

    def capture_propagation(
        self,
        source_claim_id: str,
        affected_claims: list[str],
        propagation_type: str,
    ) -> dict[str, Any]:
        """Record a propagation trace.

        Args:
            source_claim_id: The claim whose change triggers propagation.
            affected_claims: Downstream claims affected by the change.
            propagation_type: Category of propagation (e.g. "gate_change",
                "status_change", "profile_update", "relation_added").

        Returns:
            The recorded propagation trace dict with keys:
            source, affected, type, timestamp, depth.
        """
        depth = self._compute_propagation_depth(
            source_claim_id, affected_claims
        )
        record: dict[str, Any] = {
            "source": source_claim_id,
            "affected": list(affected_claims),
            "type": propagation_type,
            "timestamp": _now_iso(),
            "depth": depth,
        }
        self._propagation_traces.append(record)
        return record

    # ------------------------------------------------------------------
    # Vector-score deltas
    # ------------------------------------------------------------------

    def capture_vector_score_delta(
        self,
        claim_id: str,
        before_scores: dict[str, float],
        after_scores: dict[str, float],
    ) -> dict[str, Any]:
        """Record a vector-score change for a claim.

        Args:
            claim_id: The claim whose scores changed.
            before_scores: Score dict before the event (dimension -> value).
            after_scores: Score dict after the event (dimension -> value).

        Returns:
            The recorded delta dict with keys:
            claim_id, before, after, delta, changed_dimensions.
        """
        all_dims = sorted(set(before_scores) | set(after_scores))
        delta: dict[str, float] = {}
        changed_dimensions: list[str] = []

        for dim in all_dims:
            before_val = before_scores.get(dim, 0.0)
            after_val = after_scores.get(dim, 0.0)
            d = round(after_val - before_val, 10)
            delta[dim] = d
            if d != 0.0:
                changed_dimensions.append(dim)

        record: dict[str, Any] = {
            "claim_id": claim_id,
            "before": dict(before_scores),
            "after": dict(after_scores),
            "delta": delta,
            "changed_dimensions": changed_dimensions,
        }
        self._vector_score_deltas.append(record)
        return record

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_propagation_traces(self) -> list[dict[str, Any]]:
        """Return all captured propagation traces."""
        return list(self._propagation_traces)

    def get_vector_score_deltas(self) -> list[dict[str, Any]]:
        """Return all captured vector-score deltas."""
        return list(self._vector_score_deltas)

    @property
    def has_propagation_data(self) -> bool:
        """Whether any propagation traces have been captured."""
        return bool(self._propagation_traces)

    @property
    def has_vector_score_data(self) -> bool:
        """Whether any vector-score deltas have been captured."""
        return bool(self._vector_score_deltas)

    # ------------------------------------------------------------------
    # Export integration (CNT-007 compliant)
    # ------------------------------------------------------------------

    def to_trace_results_section(
        self,
        *,
        propagation_unavailable_reason: str | None = None,
        vector_score_unavailable_reason: str | None = None,
    ) -> dict[str, Any]:
        """Return data suitable for inclusion in trace_results.

        CNT-007: When data is absent and a reason is provided, the result
        includes the unavailable_reason instead of bare empty lists.
        """
        result: dict[str, Any] = {}

        if self._propagation_traces:
            result["propagation_traces"] = list(self._propagation_traces)
        elif propagation_unavailable_reason:
            result["propagation_traces"] = []
            result["propagation_traces_unavailable_reason"] = (
                propagation_unavailable_reason
            )
        else:
            result["propagation_traces"] = []
            result["propagation_traces_unavailable_reason"] = (
                "runtime_not_captured"
            )

        if self._vector_score_deltas:
            result["vector_score_deltas"] = list(self._vector_score_deltas)
        elif vector_score_unavailable_reason:
            result["vector_score_deltas"] = []
            result["vector_score_deltas_unavailable_reason"] = (
                vector_score_unavailable_reason
            )
        else:
            result["vector_score_deltas"] = []
            result["vector_score_deltas_unavailable_reason"] = (
                "runtime_not_captured"
            )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_propagation_depth(
        source_claim_id: str,
        affected_claims: list[str],
    ) -> int:
        """Depth = number of uniquely affected claims.

        A simple heuristic: each distinct downstream claim is one level of
        propagation depth from the source.
        """
        unique = {c for c in affected_claims if c != source_claim_id}
        return len(unique)
