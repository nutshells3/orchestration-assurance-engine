"""Policy engine rationale surface."""

from __future__ import annotations

import json
from typing import Any

from ..audit_rules import validate_promotion_rules
from . import BaseAgent
from .prompt_registry import get_system_prompt


class PolicyEngineAgent(BaseAgent):
    role = "policy_engine"

    def system_prompt(self, context: dict[str, Any]) -> str:
        return get_system_prompt(self.prompt_role)

    def user_message(self, context: dict[str, Any]) -> str:
        parts = [
            f"## Project ID\n{context.get('project_id', 'project.default')}",
            f"## Claim ID\n{context.get('claim_id', 'unknown')}",
        ]
        if context.get("claim"):
            parts.append(
                f"## Claim\n```json\n{json.dumps(context['claim'], indent=2, default=str)}\n```"
            )
        if context.get("computed_profile"):
            parts.append(
                "## Deterministic profile\n```json\n"
                f"{json.dumps(context['computed_profile'], indent=2, default=str)}\n```"
            )
        return "\n\n".join(parts)


__all__ = ["PolicyEngineAgent", "validate_promotion_rules"]
