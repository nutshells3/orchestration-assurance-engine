"""Research agent."""

from __future__ import annotations

import json
from typing import Any

from . import BaseAgent
from .prompt_registry import get_system_prompt


class ResearchAgent(BaseAgent):
    role = "research_agent"

    def system_prompt(self, context: dict[str, Any]) -> str:
        return get_system_prompt(self.prompt_role)

    def user_message(self, context: dict[str, Any]) -> str:
        parts = []
        if context.get("claim"):
            parts.append(
                "## Claim to research\n```json\n"
                f"{json.dumps(context['claim'], indent=2, default=str)}\n```"
            )
        if context.get("research_task"):
            parts.append(f"## Research task\n{context['research_task']}")
        if context.get("existing_evidence"):
            parts.append(
                "## Existing evidence\n```json\n"
                f"{json.dumps(context['existing_evidence'], indent=2, default=str)}\n```"
            )
        return "\n\n".join(parts) if parts else "No research context."
