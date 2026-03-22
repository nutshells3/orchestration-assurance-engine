"""
Planner / Orchestrator Agent.

Authority: user-facing interpretation, claim admission, merge policy,
           final promotion decisions.
"""

from __future__ import annotations
from typing import Any
from . import BaseAgent
from .prompt_registry import get_system_prompt


class PlannerAgent(BaseAgent):
    role = "planner"

    def system_prompt(self, context: dict[str, Any]) -> str:
        return get_system_prompt(self.role)

    def user_message(self, context: dict[str, Any]) -> str:
        parts = []
        if context.get("user_input"):
            parts.append(f"## User input\n{context['user_input']}")
        if context.get("claim_graph"):
            parts.append(
                f"## Current Claim Graph\n```json\n"
                f"{_dump(context['claim_graph'])}\n```"
            )
        if context.get("assurance_profiles"):
            parts.append(
                f"## Current Assurance Profiles\n```json\n"
                f"{_dump(context['assurance_profiles'])}\n```"
            )
        if context.get("pending_promotions"):
            parts.append(
                f"## Pending promotion requests\n```json\n"
                f"{_dump(context['pending_promotions'])}\n```"
            )
        return "\n\n".join(parts) if parts else "No context provided."


def _dump(obj):
    import json
    if hasattr(obj, "model_dump"):
        return json.dumps(obj.model_dump(mode="json"), indent=2, default=str)
    return json.dumps(obj, indent=2, default=str)
