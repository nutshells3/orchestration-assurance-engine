"""
Relation Repair Agent.

Second-pass agent that proposes relations given a fixed set of admitted
claims.  Runs after the ClaimGraphAgent when the semantic adequacy gate
detects that all relations are fallback-sourced.
"""

from __future__ import annotations
import json
from typing import Any
from . import BaseAgent
from .prompt_registry import get_system_prompt


class RelationRepairAgent(BaseAgent):
    role = "claim_graph_agent"  # reuses same model slot

    def system_prompt(self, context: dict[str, Any]) -> str:
        return get_system_prompt("relation_repair_agent")

    def user_message(self, context: dict[str, Any]) -> str:
        parts = []

        # Source document
        source_text = context.get("source_text") or ""
        if source_text:
            parts.append(f"## Source document\n{source_text[:8000]}")

        # Admitted claims (stable IDs)
        claims = context.get("claims") or []
        if claims:
            claims_block = json.dumps(claims, indent=2, default=str, ensure_ascii=False)
            parts.append(f"## Admitted claims (do NOT modify)\n```json\n{claims_block}\n```")

        # Previous rejection info
        rejected = context.get("rejected_relations") or []
        if rejected:
            rej_block = json.dumps(rejected, indent=2, default=str, ensure_ascii=False)
            parts.append(
                f"## Previously rejected relations (avoid these patterns)\n"
                f"```json\n{rej_block}\n```"
            )

        parts.append(
            "## Task\n"
            "Propose relations between the admitted claims above. "
            "Use the exact claim_ids provided. "
            "Output a JSON object with a single key `relations`."
        )
        return "\n\n".join(parts)
