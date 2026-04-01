"""Formalizer agent."""

from __future__ import annotations

import json

from . import BaseAgent
from .prompt_registry import render_system_prompt


class FormalizerAgent(BaseAgent):
    """Instantiate with label="A" or label="B"."""

    def __init__(self, config, llm, label: str = "A"):
        super().__init__(config, llm)
        self.label = label
        self.other = "B" if label == "A" else "A"

    @property
    def role(self):
        return f"formalizer_{self.label.lower()}"

    @property
    def prompt_role(self) -> str:
        return "formalizer"

    def system_prompt(self, context: dict[str, object]) -> str:
        return render_system_prompt(
            self.prompt_role,
            label=self.label,
            other=self.other,
        )

    def user_message(self, context: dict[str, object]) -> str:
        claim = context["claim"]
        target_backend = context.get("target_backend") or "lean-local"
        parts = [
            f"## Target backend\n`{target_backend}`\n\n"
            "You MUST produce code that compiles in this backend.",
            f"## Claim to formalize\n```json\n{json.dumps(claim, indent=2, default=str)}\n```",
        ]
        if context.get("claim_policy"):
            parts.append(
                "## Claim policy\n```json\n"
                f"{json.dumps(context['claim_policy'], indent=2)}\n```"
            )
        if context.get("existing_theories"):
            parts.append(
                "## Existing theories in session (for imports)\n"
                f"{json.dumps(context['existing_theories'])}"
            )
        if context.get("graph_policy"):
            parts.append(
                "## Graph-level policy\n```json\n"
                f"{json.dumps(context['graph_policy'], indent=2)}\n```"
            )
        return "\n\n".join(parts)
