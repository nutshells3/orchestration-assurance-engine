"""Dev agent."""

from __future__ import annotations

import json
from typing import Any

from . import BaseAgent
from .prompt_registry import get_system_prompt


class DevAgent(BaseAgent):
    role = "dev_agent"

    def system_prompt(self, context: dict[str, Any]) -> str:
        return get_system_prompt(self.prompt_role)

    def user_message(self, context: dict[str, Any]) -> str:
        parts = []
        if context.get("contract"):
            parts.append(
                "## Contract Pack\n```json\n"
                f"{json.dumps(context['contract'], indent=2, default=str)}\n```"
            )
        if context.get("claim"):
            parts.append(
                "## Related claim\n```json\n"
                f"{json.dumps(context['claim'], indent=2, default=str)}\n```"
            )
        if context.get("assurance_profile"):
            parts.append(
                "## Assurance Profile\n```json\n"
                f"{json.dumps(context['assurance_profile'], indent=2, default=str)}\n```"
            )
        if context.get("dev_task"):
            parts.append(f"## Task\n{context['dev_task']}")
        return "\n\n".join(parts) if parts else "No dev context."
