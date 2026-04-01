"""Queries over canonical assurance graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from ._contracts import AssuranceGraph, Edge, Node, canonical_id, node_payload


@dataclass(frozen=True)
class AssuranceGraphProjection:
    node_ids: list[str]
    edge_ids: list[str]
    data: dict


@dataclass(frozen=True)
class AssuranceGraphDiff:
    added_node_ids: list[str]
    removed_node_ids: list[str]
    changed_node_ids: list[str]
    added_edge_ids: list[str]
    removed_edge_ids: list[str]
    changed_edge_ids: list[str]


class AssuranceGraphQueries:
    """Assurance-graph queries over the stored edge orientation."""

    def __init__(self, graph: AssuranceGraph):
        self.graph = graph
        self.nodes = {
            canonical_id(node_payload(node).node_id): node for node in graph.nodes
        }
        self.edges = {
            canonical_id(edge.edge_id): edge for edge in graph.edges
        }
        self.forward_index: dict[str, list[str]] = {}
        self.backward_index: dict[str, list[str]] = {}
        for edge in graph.edges:
            source_id = canonical_id(edge.source_id)
            target_id = canonical_id(edge.target_id)
            self.forward_index.setdefault(source_id, []).append(target_id)
            self.backward_index.setdefault(target_id, []).append(source_id)

    def node_ids(self) -> list[str]:
        return sorted(self.nodes)

    def edge_ids(self) -> list[str]:
        return sorted(self.edges)

    def roots(self) -> list[str]:
        all_targets = {target for targets in self.forward_index.values() for target in targets}
        return sorted(node_id for node_id in self.nodes if node_id not in all_targets)

    def leaves(self) -> list[str]:
        return sorted(node_id for node_id in self.nodes if node_id not in self.forward_index)

    def nodes_by_type(self, node_type: str) -> list[str]:
        return sorted(
            node_id
            for node_id, node in self.nodes.items()
            if getattr(node_payload(node), "node_type", None) == node_type
        )

    def formal_artifacts_for_claim(self, claim_id: str) -> list[str]:
        claim_node_ids = [
            node_id
            for node_id, node in self.nodes.items()
            if getattr(node_payload(node), "node_type", None) == "claim"
            and canonical_id(node_payload(node).claim_ref) == claim_id
        ]
        artifact_ids = {
            source_id
            for claim_node_id in claim_node_ids
            for source_id in self.backward_index.get(claim_node_id, [])
            if getattr(node_payload(self.nodes[source_id]), "node_type", None)
            == "formal_artifact"
        }
        return sorted(artifact_ids)

    def evidence_for_claim(self, claim_id: str) -> list[str]:
        claim_node_ids = [
            node_id
            for node_id, node in self.nodes.items()
            if getattr(node_payload(node), "node_type", None) == "claim"
            and canonical_id(node_payload(node).claim_ref) == claim_id
        ]
        evidence_ids = {
            source_id
            for claim_node_id in claim_node_ids
            for source_id in self.backward_index.get(claim_node_id, [])
            if getattr(node_payload(self.nodes[source_id]), "node_type", None)
            == "evidence"
        }
        return sorted(evidence_ids)

    def review_nodes_for_target(self, node_id: str) -> list[str]:
        review_ids = {
            source_id
            for source_id in self.backward_index.get(node_id, [])
            if getattr(node_payload(self.nodes[source_id]), "node_type", None) == "review"
        }
        return sorted(review_ids)

    def forward_slice(self, start_node_ids: Iterable[str], *, include_start: bool = True) -> list[str]:
        return self._walk(start_node_ids, self.forward_index, include_start=include_start)

    def backward_slice(self, start_node_ids: Iterable[str], *, include_start: bool = True) -> list[str]:
        return self._walk(start_node_ids, self.backward_index, include_start=include_start)

    def hotspot_node_ids(self, *, limit: int = 5) -> list[str]:
        ranked = sorted(
            self.nodes,
            key=lambda node_id: (
                len(self.forward_index.get(node_id, []))
                + len(self.backward_index.get(node_id, [])),
                node_id,
            ),
            reverse=True,
        )
        return ranked[:limit]

    def project(self, node_ids: Iterable[str]) -> AssuranceGraphProjection:
        node_id_set = set(node_ids)
        nodes = [
            node.model_dump(mode="json", exclude_none=True)
            for node_id, node in self.nodes.items()
            if node_id in node_id_set
        ]
        edges = [
            edge.model_dump(mode="json", exclude_none=True)
            for edge in self.edges.values()
            if canonical_id(edge.source_id) in node_id_set
            and canonical_id(edge.target_id) in node_id_set
        ]
        data = self.graph.model_dump(mode="json", exclude_none=True)
        data["nodes"] = nodes
        data["edges"] = edges
        return AssuranceGraphProjection(
            node_ids=sorted(node_id_set),
            edge_ids=sorted(canonical_id(edge["edge_id"]) for edge in edges),
            data=data,
        )

    def _walk(
        self,
        start_node_ids: Iterable[str],
        adjacency: dict[str, list[str]],
        *,
        include_start: bool,
    ) -> list[str]:
        ordered_start = list(dict.fromkeys(start_node_ids))
        visited = set(ordered_start)
        agenda = list(ordered_start)
        result = list(ordered_start if include_start else [])

        while agenda:
            node_id = agenda.pop(0)
            for neighbor in adjacency.get(node_id, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                result.append(neighbor)
                agenda.append(neighbor)

        return result


def diff_assurance_graphs(before: AssuranceGraph, after: AssuranceGraph) -> AssuranceGraphDiff:
    before_nodes = {
        canonical_id(node_payload(node).node_id): node.model_dump(mode="json", exclude_none=True)
        for node in before.nodes
    }
    after_nodes = {
        canonical_id(node_payload(node).node_id): node.model_dump(mode="json", exclude_none=True)
        for node in after.nodes
    }
    before_edges = {
        canonical_id(edge.edge_id): edge.model_dump(mode="json", exclude_none=True)
        for edge in before.edges
    }
    after_edges = {
        canonical_id(edge.edge_id): edge.model_dump(mode="json", exclude_none=True)
        for edge in after.edges
    }

    return AssuranceGraphDiff(
        added_node_ids=sorted(set(after_nodes) - set(before_nodes)),
        removed_node_ids=sorted(set(before_nodes) - set(after_nodes)),
        changed_node_ids=sorted(
            node_id
            for node_id in set(before_nodes).intersection(after_nodes)
            if json.dumps(before_nodes[node_id], sort_keys=True)
            != json.dumps(after_nodes[node_id], sort_keys=True)
        ),
        added_edge_ids=sorted(set(after_edges) - set(before_edges)),
        removed_edge_ids=sorted(set(before_edges) - set(after_edges)),
        changed_edge_ids=sorted(
            edge_id
            for edge_id in set(before_edges).intersection(after_edges)
            if json.dumps(before_edges[edge_id], sort_keys=True)
            != json.dumps(after_edges[edge_id], sort_keys=True)
        ),
    )


__all__ = [
    "AssuranceGraphDiff",
    "AssuranceGraphProjection",
    "AssuranceGraphQueries",
    "diff_assurance_graphs",
]
