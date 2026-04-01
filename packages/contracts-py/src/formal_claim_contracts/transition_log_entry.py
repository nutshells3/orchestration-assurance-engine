"""Generated from transition-log-entry.schema.json — TransitionLogEntry binding."""

from __future__ import annotations

from pydantic import BaseModel


class TransitionLogEntry(BaseModel):
    """One entry in the transition_log.jsonl chronological event stream."""

    claim_id: str
    event_type: str
    artifact_id: str = ""
    actor: str = ""
    actor_role: str = ""
    timestamp: str = ""
    notes: str = ""
