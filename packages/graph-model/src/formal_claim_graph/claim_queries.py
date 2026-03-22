"""Queries over canonical claim graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from ._contracts import Claim, ClaimGraph, Relation, canonical_id


@dataclass(frozen=True)
class ClaimGraphProjection:
    claim_ids: list[str]
    relation_ids: list[str]
    data: dict


@dataclass(frozen=True)
class ClaimImpactAnalysis:
    changed_claim_ids: list[str]
    directly_affected_claim_ids: list[str]
    transitively_affected_claim_ids: list[str]


@dataclass(frozen=True)
class ClaimGraphDiff:
    added_claim_ids: list[str]
    removed_claim_ids: list[str]
    changed_claim_ids: list[str]
    added_relation_ids: list[str]
    removed_relation_ids: list[str]
    changed_relation_ids: list[str]


class ClaimGraphQueries:
    """Claim-graph queries over the stored relation orientation."""

    def __init__(self, graph: ClaimGraph):
        self.graph = graph
        self.claims = {
            canonical_id(claim.claim_id): claim for claim in graph.claims
        }
        self.relations = {
            canonical_id(relation.relation_id): relation for relation in graph.relations
        }
        self.forward_index: dict[str, list[str]] = {}
        self.backward_index: dict[str, list[str]] = {}
        for relation in graph.relations:
            source_id = canonical_id(relation.from_claim_id)
            target_id = canonical_id(relation.to_claim_id)
            self.forward_index.setdefault(source_id, []).append(target_id)
            self.backward_index.setdefault(target_id, []).append(source_id)

    def claim_ids(self) -> list[str]:
        return sorted(self.claims)

    def relation_ids(self) -> list[str]:
        return sorted(self.relations)

    def get_claim(self, claim_id: str) -> Claim:
        return self.claims[claim_id]

    def roots(self) -> list[str]:
        all_targets = {target for targets in self.forward_index.values() for target in targets}
        return sorted(claim_id for claim_id in self.claims if claim_id not in all_targets)

    def leaves(self) -> list[str]:
        return sorted(
            claim_id for claim_id in self.claims if claim_id not in self.forward_index
        )

    def dependencies_of(self, claim_id: str) -> list[str]:
        return sorted(self.forward_index.get(claim_id, []))

    def dependents_of(self, claim_id: str) -> list[str]:
        return sorted(self.backward_index.get(claim_id, []))

    def forward_slice(
        self, start_claim_ids: Iterable[str], *, include_start: bool = True
    ) -> list[str]:
        return self._walk(start_claim_ids, self.forward_index, include_start=include_start)

    def backward_slice(
        self, start_claim_ids: Iterable[str], *, include_start: bool = True
    ) -> list[str]:
        return self._walk(start_claim_ids, self.backward_index, include_start=include_start)

    def dependency_closure(self, claim_ids: Iterable[str]) -> list[str]:
        return self.forward_slice(claim_ids)

    def impact_analysis(self, changed_claim_ids: Iterable[str]) -> ClaimImpactAnalysis:
        changed = sorted(dict.fromkeys(changed_claim_ids))
        directly_affected = sorted(
            {
                dependent
                for claim_id in changed
                for dependent in self.backward_index.get(claim_id, [])
            }
        )
        transitive = self.backward_slice(changed, include_start=False)
        return ClaimImpactAnalysis(
            changed_claim_ids=changed,
            directly_affected_claim_ids=directly_affected,
            transitively_affected_claim_ids=transitive,
        )

    def hotspot_claim_ids(self, *, limit: int = 5) -> list[str]:
        ranked = sorted(
            self.claims,
            key=lambda claim_id: (
                len(self.forward_index.get(claim_id, []))
                + len(self.backward_index.get(claim_id, [])),
                claim_id,
            ),
            reverse=True,
        )
        return ranked[:limit]

    def project(self, claim_ids: Iterable[str]) -> ClaimGraphProjection:
        claim_id_set = set(claim_ids)
        claims = [
            claim.model_dump(mode="json", exclude_none=True)
            for claim_id, claim in self.claims.items()
            if claim_id in claim_id_set
        ]
        relations = [
            relation.model_dump(mode="json", exclude_none=True)
            for relation in self.relations.values()
            if canonical_id(relation.from_claim_id) in claim_id_set
            and canonical_id(relation.to_claim_id) in claim_id_set
        ]
        data = self.graph.model_dump(mode="json", exclude_none=True)
        data["claims"] = claims
        data["relations"] = relations
        data["root_claim_ids"] = [
            claim_id for claim_id in self.roots() if claim_id in claim_id_set
        ] or None
        return ClaimGraphProjection(
            claim_ids=sorted(claim_id_set),
            relation_ids=sorted(
                canonical_id(relation["relation_id"]) for relation in relations
            ),
            data=data,
        )

    def diff(self, other: ClaimGraph) -> ClaimGraphDiff:
        other_queries = ClaimGraphQueries(other)
        return diff_claim_graphs(self.graph, other_queries.graph)

    def _walk(
        self,
        start_claim_ids: Iterable[str],
        adjacency: dict[str, list[str]],
        *,
        include_start: bool,
    ) -> list[str]:
        ordered_start = list(dict.fromkeys(start_claim_ids))
        visited = set(ordered_start)
        agenda = list(ordered_start)
        result = list(ordered_start if include_start else [])

        while agenda:
            claim_id = agenda.pop(0)
            for neighbor in adjacency.get(claim_id, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                result.append(neighbor)
                agenda.append(neighbor)

        return result


def diff_claim_graphs(before: ClaimGraph, after: ClaimGraph) -> ClaimGraphDiff:
    before_claims = {
        canonical_id(claim.claim_id): claim.model_dump(mode="json", exclude_none=True)
        for claim in before.claims
    }
    after_claims = {
        canonical_id(claim.claim_id): claim.model_dump(mode="json", exclude_none=True)
        for claim in after.claims
    }
    before_relations = {
        canonical_id(relation.relation_id): relation.model_dump(
            mode="json", exclude_none=True
        )
        for relation in before.relations
    }
    after_relations = {
        canonical_id(relation.relation_id): relation.model_dump(
            mode="json", exclude_none=True
        )
        for relation in after.relations
    }

    return ClaimGraphDiff(
        added_claim_ids=sorted(set(after_claims) - set(before_claims)),
        removed_claim_ids=sorted(set(before_claims) - set(after_claims)),
        changed_claim_ids=sorted(
            claim_id
            for claim_id in set(before_claims).intersection(after_claims)
            if json.dumps(before_claims[claim_id], sort_keys=True)
            != json.dumps(after_claims[claim_id], sort_keys=True)
        ),
        added_relation_ids=sorted(set(after_relations) - set(before_relations)),
        removed_relation_ids=sorted(set(before_relations) - set(after_relations)),
        changed_relation_ids=sorted(
            relation_id
            for relation_id in set(before_relations).intersection(after_relations)
            if json.dumps(before_relations[relation_id], sort_keys=True)
            != json.dumps(after_relations[relation_id], sort_keys=True)
        ),
    )


__all__ = [
    "ClaimGraphDiff",
    "ClaimGraphProjection",
    "ClaimGraphQueries",
    "ClaimImpactAnalysis",
    "diff_claim_graphs",
]
