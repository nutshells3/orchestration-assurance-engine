"""Backend-neutral proof verifier agent."""

from __future__ import annotations

import json
from typing import Any

from . import BaseAgent
from .prompt_registry import get_system_prompt


class ProofVerifierAgent(BaseAgent):
    role = "proof_verifier"

    def system_prompt(self, context: dict[str, Any]) -> str:
        return get_system_prompt(
            self.prompt_role,
            proof_language=str(context.get("proof_language") or "proof"),
        )

    def user_message(self, context: dict[str, Any]) -> str:
        parts = []
        if context.get("claim_id"):
            parts.append(f"## Claim ID\n{context['claim_id']}")
        if context.get("formalizer_label"):
            parts.append(f"## Formalizer\n{context['formalizer_label']}")
        proof_language = str(context.get("proof_language") or "text")
        if context.get("proof_source"):
            parts.append(
                f"## Proof source\n```{proof_language}\n{context['proof_source']}\n```"
            )
        if context.get("build_output"):
            parts.append(f"## Build output\n```\n{context['build_output']}\n```")
        if context.get("dependency_data"):
            parts.append(
                "## Dependency export\n```json\n"
                f"{json.dumps(context['dependency_data'], indent=2)}\n```"
            )
        return "\n\n".join(parts) if parts else "No build data provided."
