"""Generated from sidecar-meta.schema.json — SidecarMetaV1 binding."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SidecarMeta(BaseModel):
    """Operator-only sidecar metadata. NOT for model consumption."""

    schema_: str = Field("SidecarMetaV1", alias="schema")
    version: str = "1.0.0"
    project_id: str
    exported_at: str
    source_domain: str = ""
    operator_notes: str = ""
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    warning: str = "OPERATOR-ONLY. Do NOT feed this file to model contexts."

    model_config = {"populate_by_name": True}
