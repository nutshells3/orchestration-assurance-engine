"""
Claim Graph Agent.

Decomposes free-form input into atomic claims, proposes dependencies,
surfaces hidden assumptions, and drafts semantic guardrails.
"""

from __future__ import annotations
import json
from typing import Any
from . import BaseAgent
from .prompt_registry import get_system_prompt


class ClaimGraphAgent(BaseAgent):
    role = "claim_graph_agent"

    def system_prompt(self, context: dict[str, Any]) -> str:
        return get_system_prompt(self.role)

    def user_message(self, context: dict[str, Any]) -> str:
        parts = [f"## Project ID\n{context.get('project_id', 'project.default')}"]
        if context.get("user_input"):
            parts.append(f"## User input to decompose\n{context['user_input']}")
        if context.get("existing_claims"):
            parts.append(
                f"## Existing claims (do not duplicate)\n```json\n"
                f"{json.dumps(context['existing_claims'], indent=2, default=str)}\n```"
            )
        if context.get("planner_guidance"):
            parts.append(f"## Planner guidance\n{context['planner_guidance']}")
        return "\n\n".join(parts)
