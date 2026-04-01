"""Validation helpers for PipelineEventV1 event streams."""

from __future__ import annotations

from .event_normalizer import PipelineEventV1

_VALID_EVENT_CLASSES = {"controllable_action", "automatic_consequence"}

# Canonical unavailable_reason values (CNT-007)
VALID_UNAVAILABLE_REASONS = frozenset({
    "not_applicable",
    "computation_failed",
    "runtime_not_captured",
    "exporter_not_implemented",
})


class EventValidationError(ValueError):
    """Raised when an event or event stream fails validation."""


class EventValidator:
    """Stateful validator for event streams (TRC-009 / CNT-006)."""

    def __init__(self) -> None:
        self._seen_event_ids: set[str] = set()

    def validate(self, event: PipelineEventV1) -> list[str]:
        """Validate a single event, tracking seen event IDs."""
        errors = validate_event(event)

        # TRC-009: unique event_id
        if event.event_id in self._seen_event_ids:
            errors.append(f"duplicate event_id: {event.event_id}")
        self._seen_event_ids.add(event.event_id)

        return errors

    # Static method aliases for backwards compatibility with existing tests
    @staticmethod
    def validate_event(event: PipelineEventV1) -> list[str]:
        return validate_event(event)

    @staticmethod
    def validate_event_stream(events: list[PipelineEventV1]) -> list[str]:
        return validate_event_stream(events)


def _get(event, key, default=None):
    """Get a field from a PipelineEventV1 or dict."""
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def validate_event(event) -> list[str]:
    """Return a list of validation error strings (empty == valid).

    Accepts both PipelineEventV1 instances and plain dicts.
    """
    errors: list[str] = []

    if not _get(event, "trace_id"):
        errors.append("trace_id is required")

    step = _get(event, "step", 0)
    step_id = _get(event, "step_id", "")
    if step < 1 and not step_id:
        errors.append("step must be >= 1")

    if not _get(event, "phase"):
        errors.append("phase is required")
    if not _get(event, "event_type"):
        errors.append("event_type is required")
    if not _get(event, "actor"):
        errors.append("actor is required")
    if not _get(event, "before_hash"):
        errors.append("before_hash is required")
    if not _get(event, "after_hash"):
        errors.append("after_hash is required")
    if _get(event, "accepted", True) is False and not _get(event, "reject_reason"):
        errors.append("reject_reason must be non-empty when accepted is False")

    # TRC-009: event_class validation (skip if not present -- baseline events)
    event_class = _get(event, "event_class", "controllable_action")
    if event_class not in _VALID_EVENT_CLASSES:
        errors.append(
            f"event_class must be one of {_VALID_EVENT_CLASSES}, "
            f"got {event_class!r}"
        )

    # TRC-009: automatic consequences must have cause_event_id
    if event_class == "automatic_consequence" and not _get(event, "cause_event_id"):
        errors.append(
            "cause_event_id is required when event_class is 'automatic_consequence'"
        )

    # TRC-009: controllable actions must NOT have cause_event_id
    if event_class == "controllable_action" and _get(event, "cause_event_id"):
        errors.append(
            "cause_event_id must be null for controllable_action events"
        )

    # CNT-006: event_id must be non-empty
    if not _get(event, "event_id"):
        errors.append("event_id is required")

    return errors


def validate_event_strict(event: PipelineEventV1) -> None:
    """Raise EventValidationError if the event is invalid."""
    errors = validate_event(event)
    if errors:
        raise EventValidationError(
            f"Event {event.event_id} invalid: {'; '.join(errors)}"
        )


def validate_event_stream(events: list) -> list[str]:
    """Validate an ordered event stream.  Returns all error strings found.

    Accepts lists of PipelineEventV1 or plain dicts.
    """
    errors: list[str] = []
    if not events:
        return errors

    trace_id = _get(events[0], "trace_id")
    seen_steps: set[int] = set()
    seen_event_ids: set[str] = set()
    prev_step = 0

    for idx, event in enumerate(events):
        event_id = _get(event, "event_id", f"event-{idx}")

        # Per-event validation
        per_event = validate_event(event)
        for msg in per_event:
            errors.append(f"event[{idx}] ({event_id}): {msg}")

        # Consistent trace_id
        evt_trace_id = _get(event, "trace_id")
        if evt_trace_id != trace_id:
            errors.append(
                f"event[{idx}] ({event_id}): trace_id mismatch "
                f"(expected {trace_id!r}, got {evt_trace_id!r})"
            )

        # Unique event_id (CNT-006)
        if event_id in seen_event_ids:
            errors.append(
                f"event[{idx}] ({event_id}): duplicate event_id"
            )
        seen_event_ids.add(event_id)

        # Monotonic step ordering
        step = _get(event, "step", 0)
        if step in seen_steps:
            errors.append(
                f"event[{idx}] ({event_id}): duplicate step {step}"
            )
        if step <= prev_step and idx > 0:
            errors.append(
                f"event[{idx}] ({event_id}): step {step} is not "
                f"monotonically increasing (prev={prev_step})"
            )
        seen_steps.add(step)
        prev_step = step

        # TRC-009: cause_event_id must reference a previously seen event
        cause_id = _get(event, "cause_event_id")
        if cause_id and cause_id not in seen_event_ids:
            errors.append(
                f"event[{idx}] ({event_id}): cause_event_id "
                f"{cause_id!r} references unknown event"
            )

    return errors


def validate_event_stream_strict(events: list[PipelineEventV1]) -> None:
    """Raise EventValidationError if any event in the stream is invalid."""
    errors = validate_event_stream(events)
    if errors:
        raise EventValidationError(
            f"{len(errors)} validation error(s):\n" + "\n".join(errors)
        )
