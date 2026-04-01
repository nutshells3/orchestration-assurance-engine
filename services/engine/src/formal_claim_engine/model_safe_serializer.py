"""SFE-002 / SAFE-001: Model-Safe Serialization.

Ensures all model-visible output is domain-free and redacted.  Domain-
sensitive fields are stripped from trace.json and relegated to the
operator-only sidecar_meta.json written by SidecarMetaWriter.

SAFE-001: Expanded v2 forbidden field list covering top-level, nested,
infra, and value-level leak patterns.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# v2 forbidden field list (SAFE-001)
# ---------------------------------------------------------------------------

# Top-level forbidden fields
_TOP_LEVEL_FORBIDDEN: set[str] = {
    "source_domain",
    "prompt_id",
    "router_decision",
    "corpus_name",
    "split",
    "source_uri",
    "operator_notes",
    "license",
}

# Nested forbidden fields (dotted paths)
_NESTED_FORBIDDEN: set[str] = {
    "scope.domain",
    "project.domain",
}

# Infrastructure fields that must never appear in model-visible output
_INFRA_FORBIDDEN: set[str] = {
    "api_key",
    "api_key_env",
    "api_base",
    "provider",
    "model",
    "temperature",
    "max_tokens",
    "reasoning_effort",
    "raw_llm_response",
    "raw_text",
    "usage",
}

# Module-level alias for test imports -- union of all forbidden categories
REDACTED_FIELDS: set[str] = _TOP_LEVEL_FORBIDDEN | _NESTED_FORBIDDEN | _INFRA_FORBIDDEN

# Value-level pattern: strings matching "tracer_domain:*" anywhere in keys
# or values indicate a domain-tracing leak.
_TRACER_DOMAIN_PATTERN: re.Pattern[str] = re.compile(r"tracer_domain:")


class ModelSafeSerializer:
    """Ensures all model-visible output is domain-free and redacted."""

    REDACTED_FIELDS: set[str] = REDACTED_FIELDS

    # Flat leaf keys that must be stripped regardless of nesting depth.
    _FLAT_REDACTED_KEYS: set[str] = set()

    # Dotted keys where only the *last* segment matters (simple leaf match).
    _LEAF_KEYS: dict[str, set[str]] = {}

    # Track whether lookup tables have been initialized
    _lookup_initialized: bool = False

    @classmethod
    def _init_lookup(cls) -> None:
        """Pre-compute lookup structures from REDACTED_FIELDS (once)."""
        if cls._lookup_initialized:
            return
        for field in cls.REDACTED_FIELDS:
            parts = field.split(".")
            # Every dotted path's leaf is a candidate for removal.
            cls._FLAT_REDACTED_KEYS.add(parts[-1])
            if len(parts) > 1:
                parent = ".".join(parts[:-1])
                cls._LEAF_KEYS.setdefault(parent, set()).add(parts[-1])
        cls._lookup_initialized = True

    @classmethod
    def _reset_lookup(cls) -> None:
        """Reset lookup tables (for testing after field set changes)."""
        cls._FLAT_REDACTED_KEYS = set()
        cls._LEAF_KEYS = {}
        cls._lookup_initialized = False

    @staticmethod
    def redact(data: dict[str, Any]) -> dict[str, Any]:
        """Remove all redacted fields from a dict tree recursively."""
        ModelSafeSerializer._init_lookup()
        return _redact_node(data, path_parts=[])

    @staticmethod
    def validate_model_safe(data: dict[str, Any]) -> list[str]:
        """Return list of redaction violations found in data."""
        ModelSafeSerializer._init_lookup()
        violations: list[str] = []
        _scan_violations(data, path_parts=[], violations=violations)
        return violations

    @staticmethod
    def validate_no_runtime_leaks(data: dict[str, Any]) -> list[str]:
        """Return list of runtime/provider leak violations in model-visible data.

        B40/SAFE-002: Scans reject_reason and other string values for known
        runtime diagnostic patterns (provider names, model IDs, codex/session
        references).  This supplements field-level redaction with value-level
        leak detection, targeted at known runtime error shapes.
        """
        violations: list[str] = []
        _scan_runtime_leaks(data, path_parts=[], violations=violations)
        return violations


# ---------------------------------------------------------------------------
# Internal recursive helpers
# ---------------------------------------------------------------------------


def _should_redact(key: str, path_parts: list[str]) -> bool:
    """Determine whether *key* at *path_parts* should be removed.

    Matching rules (evaluated in order):
    1. Full dotted path match: ``scope.domain`` matches at path
       ``["scope"]`` with key ``"domain"``.
    2. Direct key match: ``source_domain`` matches the key anywhere
       in the tree (no path context needed).
    3. Suffix match for nested dotted paths: if the key is the leaf of
       a dotted redacted field, check whether the immediate parent in
       path_parts matches.  E.g. ``scope.domain`` fires when key is
       ``"domain"`` and path_parts ends with ``"scope"`` -- regardless
       of how deeply nested.
    4. tracer_domain:* key pattern match.
    """
    # 1. Full dotted path
    full_path = ".".join([*path_parts, key])
    if full_path in ModelSafeSerializer.REDACTED_FIELDS:
        return True
    # 2. Direct key match (top-level field names like "source_domain")
    if key in ModelSafeSerializer.REDACTED_FIELDS:
        return True
    # 3. Suffix match: for dotted fields like "scope.domain", check if
    #    the parent.key suffix matches any REDACTED_FIELDS entry.  This
    #    catches "scope.domain" at any nesting depth.
    if path_parts:
        parent_key = path_parts[-1]
        candidate = f"{parent_key}.{key}"
        if candidate in ModelSafeSerializer.REDACTED_FIELDS:
            return True
    # 4. SAFE-001: catch keys matching the tracer_domain:* pattern
    if _TRACER_DOMAIN_PATTERN.search(key):
        return True
    return False


def _has_tracer_domain_value(value: Any) -> bool:
    """Check if a string value matches the tracer_domain:* pattern."""
    if isinstance(value, str) and _TRACER_DOMAIN_PATTERN.search(value):
        return True
    return False


def _redact_node(node: Any, *, path_parts: list[str]) -> Any:
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if _should_redact(key, path_parts):
                continue
            redacted_value = _redact_node(value, path_parts=[*path_parts, key])
            # SAFE-001: skip string values matching tracer_domain:* pattern
            if _has_tracer_domain_value(redacted_value):
                continue
            result[key] = redacted_value
        return result
    if isinstance(node, list):
        redacted_items: list[Any] = []
        for item in node:
            redacted_item = _redact_node(item, path_parts=path_parts)
            # SAFE-001: filter out tracer_domain:* string values in lists
            if _has_tracer_domain_value(redacted_item):
                continue
            redacted_items.append(redacted_item)
        return redacted_items
    return node


def _scan_violations(
    node: Any,
    *,
    path_parts: list[str],
    violations: list[str],
) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if _should_redact(key, path_parts):
                violations.append(".".join([*path_parts, key]))
            # SAFE-001: check string values for tracer_domain:* pattern
            if isinstance(value, str) and _has_tracer_domain_value(value):
                violations.append(
                    f"{'.'.join([*path_parts, key])}=<tracer_domain:* value>"
                )
            _scan_violations(value, path_parts=[*path_parts, key], violations=violations)
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            # SAFE-001: check string items in lists for tracer_domain:* pattern
            if isinstance(item, str) and _has_tracer_domain_value(item):
                violations.append(
                    f"{'.'.join([*path_parts])}[{idx}]=<tracer_domain:* value>"
                )
            _scan_violations(
                item,
                path_parts=[*path_parts, f"[{idx}]"],
                violations=violations,
            )


# ---------------------------------------------------------------------------
# B40/SAFE-002: Runtime leak detection patterns
# ---------------------------------------------------------------------------
# These patterns target known runtime diagnostic shapes in string values.
# They are NOT a naive global blacklist -- they target specific provider,
# model, codex, and session identifiers that should never appear in
# model-visible artifacts.

_RUNTIME_LEAK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bopenai\b", re.IGNORECASE), "provider_leak"),
    (re.compile(r"\banthropic\b", re.IGNORECASE), "provider_leak"),
    (re.compile(r"\bazure\b", re.IGNORECASE), "provider_leak"),
    (re.compile(r"\bgpt-\d", re.IGNORECASE), "model_leak"),
    (re.compile(r"\bgpt4\b", re.IGNORECASE), "model_leak"),
    (re.compile(r"\bclaude[\s-]", re.IGNORECASE), "model_leak"),
    (re.compile(r"\bo[134]-", re.IGNORECASE), "model_leak"),
    (re.compile(r"\bcodex\b", re.IGNORECASE), "runtime_leak"),
    (re.compile(r"\bsession[\s_-]?id\b", re.IGNORECASE), "runtime_leak"),
]

# Keys where runtime leak detection should be applied (model-visible fields
# that commonly carry error diagnostics).
_RUNTIME_LEAK_CHECK_KEYS: frozenset[str] = frozenset({
    "reject_reason",
    "error",
    "error_message",
    "message",
    "detail",
    "reason",
})


def _check_runtime_leak(value: str) -> str | None:
    """Return leak category if value contains a known runtime pattern, else None."""
    for pattern, category in _RUNTIME_LEAK_PATTERNS:
        if pattern.search(value):
            return category
    return None


def _scan_runtime_leaks(
    node: Any,
    *,
    path_parts: list[str],
    violations: list[str],
) -> None:
    """Recursively scan for runtime leak patterns in string values."""
    if isinstance(node, dict):
        for key, value in node.items():
            current_path = [*path_parts, key]
            # Check string values in known diagnostic keys
            if isinstance(value, str) and key in _RUNTIME_LEAK_CHECK_KEYS:
                leak = _check_runtime_leak(value)
                if leak:
                    violations.append(
                        f"{'.'.join(current_path)}=<{leak}: {value[:50]}>"
                    )
            _scan_runtime_leaks(value, path_parts=current_path, violations=violations)
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            _scan_runtime_leaks(
                item,
                path_parts=[*path_parts, f"[{idx}]"],
                violations=violations,
            )


__all__ = [
    "ModelSafeSerializer",
]
