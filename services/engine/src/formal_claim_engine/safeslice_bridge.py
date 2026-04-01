"""PFX-004 — SafeSlice bridge for domain-free prefix policy enforcement.

Adapter that ensures SafeSlice consumers only receive domain-free data.
Sits between the PrefixSlice pipeline and any downstream SafeSlice
consumers, enforcing the domain-free prefix policy.

Spec references
---------------
* OAE Output Spec v1 -- SafeSlice policy
* ModelSafeSerializer REDACTED_FIELDS
* PrefixSliceBuilder REDACTED_FIELDS, _FUTURE_LEAK_FIELDS
"""

from __future__ import annotations

import json
import re
from typing import Any

from .model_safe_serializer import ModelSafeSerializer
from .prefix_slice_builder import REDACTED_FIELDS, _FUTURE_LEAK_FIELDS


# Fields from ModelSafeSerializer that must never appear in model input.
_MODEL_SAFE_BANNED: frozenset[str] = frozenset(ModelSafeSerializer.REDACTED_FIELDS)

# Combined ban list: prefix-slice redacted fields + model-safe fields.
_ALL_BANNED_FIELDS: frozenset[str] = REDACTED_FIELDS | _MODEL_SAFE_BANNED

# Sidecar metadata keys that belong in operator-only output, not model input.
_SIDECAR_KEYS: frozenset[str] = frozenset({
    "source_domain",
    "operator_notes",
    "corpus_name",
    "source_uri",
    "router_decision",
    "split",
    "sidecar_meta",
    "sidecar",
    "operator_metadata",
    "debug_info",
    "internal_metadata",
})


class SafeSliceBridge:
    """Adapter that ensures SafeSlice consumers only receive domain-free data.

    This bridge sits between the PrefixSlice pipeline and any downstream
    SafeSlice consumers, enforcing the domain-free prefix policy.
    """

    BANNED_IN_MODEL_INPUT: frozenset[str] = _ALL_BANNED_FIELDS

    def __init__(self) -> None:
        self._serializer = ModelSafeSerializer()

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter_slice(self, prefix_slice: dict[str, Any]) -> dict[str, Any]:
        """Remove any domain/context metadata that leaked into a PrefixSlice.

        Applies ModelSafeSerializer.redact() to state_text content
        and strips any sidecar fields from the slice dict.
        """
        result = dict(prefix_slice)

        # 1. Strip sidecar / banned top-level keys from the slice dict
        for key in list(result.keys()):
            if key in _SIDECAR_KEYS or key in _ALL_BANNED_FIELDS:
                del result[key]

        # 2. If state_text is a dict (pre-serialization), redact it
        state_text = result.get("state_text")
        if isinstance(state_text, dict):
            result["state_text"] = ModelSafeSerializer.redact(state_text)

        # 3. If state_text is a string, verify no banned tokens remain.
        #    If found, strip them by replacing with "[REDACTED]".
        if isinstance(state_text, str):
            result["state_text"] = self._scrub_state_text(state_text)

        # 4. Redact any nested dict structures in legal_action_mask
        mask = result.get("legal_action_mask")
        if isinstance(mask, list):
            result["legal_action_mask"] = [
                ModelSafeSerializer.redact(item) if isinstance(item, dict) else item
                for item in mask
            ]

        # 5. Redact gold_action if it is a dict
        gold = result.get("gold_action")
        if isinstance(gold, dict):
            result["gold_action"] = ModelSafeSerializer.redact(gold)

        return result

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_slice(self, prefix_slice: dict[str, Any]) -> list[str]:
        """Validate that a PrefixSlice is domain-free and safe for model input.

        Checks:
        - No banned fields in state_text
        - No domain references in state_text content
        - No sidecar metadata mixed in
        - No raw JSON dump (must be canonical format)
        """
        violations: list[str] = []

        # Check 1: No sidecar/banned top-level keys
        for key in prefix_slice:
            if key in _SIDECAR_KEYS:
                violations.append(f"sidecar key '{key}' found in slice")
            if key in _ALL_BANNED_FIELDS:
                violations.append(f"banned field '{key}' found in slice")

        # Check 2: state_text content validation
        state_text = prefix_slice.get("state_text", "")
        if isinstance(state_text, str):
            violations.extend(self._validate_state_text(state_text))
        elif isinstance(state_text, dict):
            # state_text should be serialized, not raw dict
            violations.append(
                "state_text is a raw dict; must be canonical text format"
            )

        # Check 3: No banned fields in legal_action_mask dicts
        mask = prefix_slice.get("legal_action_mask")
        if isinstance(mask, list):
            for i, item in enumerate(mask):
                if isinstance(item, dict):
                    for key in item:
                        if key in _ALL_BANNED_FIELDS:
                            violations.append(
                                f"banned field '{key}' in legal_action_mask[{i}]"
                            )

        # Check 4: No banned fields in gold_action
        gold = prefix_slice.get("gold_action")
        if isinstance(gold, dict):
            for key in gold:
                if key in _ALL_BANNED_FIELDS:
                    violations.append(
                        f"banned field '{key}' in gold_action"
                    )

        return violations

    def batch_validate(
        self,
        slices: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Validate a batch of slices. Returns {step_id: [violations]}.

        Only entries with violations are included in the result.
        """
        results: dict[str, list[str]] = {}
        for s in slices:
            step_id = str(s.get("step_id") or s.get("trace_id") or "unknown")
            violations = self.validate_slice(s)
            if violations:
                results[step_id] = violations
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_state_text(self, state_text: str) -> list[str]:
        """Validate state_text string for banned content and format."""
        violations: list[str] = []
        lower = state_text.lower()

        # Check banned fields (multi-word with _ use substring, single-word
        # use word-boundary matching to avoid false positives).
        for field in _ALL_BANNED_FIELDS:
            fl = field.lower()
            if "." in fl:
                # Dotted path like "project.domain" -- check leaf
                leaf = fl.split(".")[-1]
                if "_" in leaf:
                    if leaf in lower:
                        violations.append(
                            f"banned field '{field}' (leaf '{leaf}') found in state_text"
                        )
                else:
                    # Short leaf: use word boundaries
                    if re.search(rf"(?<![a-z_]){re.escape(leaf)}(?![a-z_])", lower):
                        violations.append(
                            f"banned field '{field}' (leaf '{leaf}') found in state_text"
                        )
            elif "_" in fl:
                if fl in lower:
                    violations.append(
                        f"banned field '{field}' found in state_text"
                    )
            else:
                if re.search(rf"(?<![a-z_]){re.escape(fl)}(?![a-z_])", lower):
                    violations.append(
                        f"banned field '{field}' found in state_text"
                    )

        # Check future-leak fields
        for field in _FUTURE_LEAK_FIELDS:
            fl = field.lower()
            if fl in lower:
                violations.append(
                    f"future-leak field '{field}' found in state_text"
                )

        # Check for raw JSON dumps (a heuristic: if state_text looks like
        # it starts with { or [ and is valid JSON, that is suspicious).
        stripped = state_text.strip()
        if stripped and stripped[0] in "{[":
            try:
                json.loads(stripped)
                violations.append(
                    "state_text appears to be raw JSON; must be canonical text format"
                )
            except (json.JSONDecodeError, ValueError):
                pass

        return violations

    def _scrub_state_text(self, state_text: str) -> str:
        """Replace any banned field occurrences in state_text with [REDACTED]."""
        result = state_text
        for field in sorted(_ALL_BANNED_FIELDS, key=len, reverse=True):
            fl = field.lower()
            if "." in fl:
                leaf = fl.split(".")[-1]
                pattern = re.compile(re.escape(leaf), re.IGNORECASE)
                result = pattern.sub("[REDACTED]", result)
            elif "_" in fl:
                pattern = re.compile(re.escape(fl), re.IGNORECASE)
                result = pattern.sub("[REDACTED]", result)
            else:
                pattern = re.compile(
                    rf"(?<![a-z_]){re.escape(fl)}(?![a-z_])", re.IGNORECASE
                )
                result = pattern.sub("[REDACTED]", result)
        return result


__all__ = [
    "SafeSliceBridge",
]
