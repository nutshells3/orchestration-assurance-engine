"""
Base agent: shared infrastructure for all pipeline roles.

Each agent has:
  - a role name (used to look up its ModelSlot)
  - a system prompt
  - a `run()` method that calls the LLM, parses JSON output, validates it
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from ..config import PipelineConfig
from ..llm_client import LLMClient, LLMResponse
from .prompt_registry import build_prompt_lineage
from .provider_adapters import prepare_completion_request
from .response_schema_registry import validate_response_output

log = logging.getLogger(__name__)


def extract_json(text: str) -> dict | list:
    """
    Extract JSON from LLM output.
    Tries the whole string first, then looks for ```json fences,
    then falls back to the first { ... } block.
    """
    text = text.strip()
    # try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # try fenced block
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # try first { ... }
    start = text.find("{")
    if start >= 0:
        depth, end = 0, start
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from LLM output:\n{text[:500]}")


class BaseAgent(ABC):
    """Abstract base for every pipeline agent."""

    role: str  # matches key in config.model_routing

    def __init__(self, config: PipelineConfig, llm: LLMClient):
        self.config = config
        self.llm = llm

    @property
    def slot(self):
        return self.config.model_routing[self.role]

    @property
    def prompt_role(self) -> str:
        return self.role

    @abstractmethod
    def system_prompt(self, context: dict[str, Any]) -> str:
        """Build the system prompt, optionally using pipeline context."""
        ...

    @abstractmethod
    def user_message(self, context: dict[str, Any]) -> str:
        """Build the user message for this step."""
        ...

    async def run(self, context: dict[str, Any]) -> dict:
        """
        Execute one agent turn:
          1. Build prompts
          2. Call LLM
          3. Parse JSON from response
          4. Return parsed dict
        """
        system = self.system_prompt(context)
        user_msg = self.user_message(context)
        messages = [{"role": "user", "content": user_msg}]
        request = prepare_completion_request(
            slot=self.slot,
            system=system,
            messages=messages,
            expect_json=True,
        )
        lineage = build_prompt_lineage(self.prompt_role, self.slot)

        log.info(f"[{self.role}] calling {self.slot.provider}/{self.slot.model}")
        resp: LLMResponse = await self.llm.complete(
            slot=self.slot,
            system=request.system,
            messages=request.messages,
            response_format=request.response_format,
        )
        log.debug(f"[{self.role}] raw output length={len(resp.text)}")

        parsed = extract_json(resp.text)
        validate_response_output(self.prompt_role, parsed)
        return {
            "role": self.role,
            "output": parsed,
            "raw_text": resp.text,
            "usage": resp.usage,
            "lineage": lineage,
        }
