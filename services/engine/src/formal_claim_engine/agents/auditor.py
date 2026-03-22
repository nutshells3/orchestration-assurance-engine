"""Audit / adversary agent."""

from __future__ import annotations

import json
from typing import Any

from . import BaseAgent
from .prompt_registry import get_system_prompt


class AuditorAgent(BaseAgent):
    role = "auditor"

    def system_prompt(self, context: dict[str, Any]) -> str:
        return get_system_prompt(self.prompt_role)

    def user_message(self, context: dict[str, Any]) -> str:
        parts = []
        if context.get("claim"):
            parts.append(
                f"## Claim\n```json\n{json.dumps(context['claim'], indent=2, default=str)}\n```"
            )
        if context.get("formalizer_a_output"):
            parts.append(
                "## Formalizer A output\n```json\n"
                f"{json.dumps(context['formalizer_a_output'], indent=2, default=str)}\n```"
            )
        if context.get("formalizer_b_output"):
            parts.append(
                "## Formalizer B output\n```json\n"
                f"{json.dumps(context['formalizer_b_output'], indent=2, default=str)}\n```"
            )
        if context.get("verifier_a_output"):
            parts.append(
                "## Verifier result (A)\n```json\n"
                f"{json.dumps(context['verifier_a_output'], indent=2, default=str)}\n```"
            )
        if context.get("verifier_b_output"):
            parts.append(
                "## Verifier result (B)\n```json\n"
                f"{json.dumps(context['verifier_b_output'], indent=2, default=str)}\n```"
            )
        if context.get("formalization_divergence"):
            parts.append(
                "## Formalization divergence\n```json\n"
                f"{json.dumps(context['formalization_divergence'], indent=2, default=str)}\n```"
            )
        if context.get("semantics_guard"):
            parts.append(
                "## Semantic guardrails\n```json\n"
                f"{json.dumps(context['semantics_guard'], indent=2)}\n```"
            )
        return "\n\n".join(parts) if parts else "No audit context provided."
