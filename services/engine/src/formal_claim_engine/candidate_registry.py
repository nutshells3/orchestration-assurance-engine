"""TRC-007 / TRC-008 -- Stable proposal and candidate IDs for ledger entries.

Every claim or relation proposal that enters the engine pipeline receives a
deterministic, replay-stable identifier pair:

- ``proposal_id``: tracks the *proposal event* (prop-{trace_short}-{counter}).
- ``candidate_id``: tracks the *candidate object* (cand-{kind}-{trace_short}-{counter}).

Both IDs are derived from a shared monotonic counter scoped to the trace_id,
so replaying the same trace produces the same IDs in the same order.

CNT-004 enrichment fields: source_unit_refs, canonical_text, accepted_as,
merged_into, reject_reason, is_hard_negative.
"""

from __future__ import annotations

from typing import Any


class CandidateRegistry:
    """Assigns stable IDs to claim and relation proposals (TRC-007, TRC-008)."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self._counter = 0

    # ------------------------------------------------------------------
    # Core minting
    # ------------------------------------------------------------------

    def mint_proposal_id(self) -> str:
        """Generate stable proposal ID: prop-{trace_id_short}-{counter:04d}"""
        self._counter += 1
        return f"prop-{self.trace_id[:8]}-{self._counter:04d}"

    def mint_candidate_id(self, kind: str = "claim") -> str:
        """Generate stable candidate ID: cand-{kind}-{trace_id_short}-{counter:04d}"""
        self._counter += 1
        return f"cand-{kind}-{self.trace_id[:8]}-{self._counter:04d}"

    # ------------------------------------------------------------------
    # Ledger helpers (CNT-004 enrichment)
    # ------------------------------------------------------------------

    def enrich_ledger_entry(
        self,
        entry: dict[str, Any],
        kind: str = "claim",
        *,
        source_unit_refs: list[str] | None = None,
        canonical_text: str | None = None,
        accepted_as: str | None = None,
        merged_into: str | None = None,
        reject_reason: str | None = None,
        is_hard_negative: bool = False,
    ) -> dict[str, Any]:
        """Add stable IDs and CNT-004 enrichment fields to a ledger entry.

        Guarantees every ledger row carries proposal_id and candidate_id
        (TRC-007/TRC-008: MUST NOT be optional).  Also populates the
        CNT-004 lineage and hard-negative fields.
        """
        if "proposal_id" not in entry:
            entry["proposal_id"] = self.mint_proposal_id()
        if "candidate_id" not in entry:
            entry["candidate_id"] = self.mint_candidate_id(kind)

        # CNT-004: source unit references
        if source_unit_refs is not None:
            entry["source_unit_refs"] = list(source_unit_refs)

        # CNT-004: canonical text
        if canonical_text is not None:
            entry["canonical_text"] = canonical_text

        # CNT-004: acceptance / merge lineage
        if accepted_as is not None:
            entry["accepted_as"] = accepted_as
        if merged_into is not None:
            entry["merged_into"] = merged_into

        # TRC-008: rejection + hard-negative
        if reject_reason is not None:
            entry["reject_reason"] = reject_reason
        entry["is_hard_negative"] = is_hard_negative

        return entry

    def create_candidate_entry(
        self,
        kind: str = "claim",
        *,
        source_unit_refs: list[str] | None = None,
        canonical_text: str | None = None,
        accepted_as: str | None = None,
        merged_into: str | None = None,
        reject_reason: str | None = None,
        is_hard_negative: bool = False,
    ) -> dict[str, Any]:
        """Create a new CandidateEntry dict with all CNT-004 fields populated.

        Returns a dict matching the candidateEntry schema definition.
        """
        entry: dict[str, Any] = {
            "candidate_id": self.mint_candidate_id(kind),
            "proposal_id": self.mint_proposal_id(),
            "is_hard_negative": is_hard_negative,
        }
        if source_unit_refs is not None:
            entry["source_unit_refs"] = list(source_unit_refs)
        if canonical_text is not None:
            entry["canonical_text"] = canonical_text
        if accepted_as is not None:
            entry["accepted_as"] = accepted_as
        if merged_into is not None:
            entry["merged_into"] = merged_into
        if reject_reason is not None:
            entry["reject_reason"] = reject_reason
        return entry

    @property
    def counter(self) -> int:
        """Current counter value (useful for diagnostics)."""
        return self._counter
