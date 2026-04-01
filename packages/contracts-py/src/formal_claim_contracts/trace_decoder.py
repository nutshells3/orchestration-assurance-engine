"""Decoder/validator for trace export artifacts.

Loads and validates trace.json, transition_log.jsonl, and sidecar_meta.json
against their JSON Schemas and Pydantic bindings.

v2 decoders: decode_trace_v2, decode_transition_log_v2, decode_sidecar_v2
validate against the frozen v2 dataset contract schemas and Pydantic models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .pipeline_trace import PipelineTraceV1
from .pipeline_event import PipelineEventV1
from .trace_sidecar_meta import TraceSidecarMeta
from .prefix_slice import PrefixSliceTextV1
from .prefix_slice_graph import PrefixSliceGraphV1

from .pipeline_trace_v2 import PipelineTraceV2
from .pipeline_event_v2 import PipelineEventV2
from .trace_sidecar_meta_v2 import TraceSidecarMetaV2
from .prefix_slice_text_v1 import PrefixSliceTextV1 as PrefixSliceTextV1Frozen
from .prefix_slice_graph_v1 import PrefixSliceGraphV1 as PrefixSliceGraphV1Frozen


def _resolve_schema_dir() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "packages" / "contracts" / "schemas"
        if candidate.exists():
            return candidate
    raise RuntimeError("Could not locate packages/contracts/schemas.")


SCHEMA_DIR = _resolve_schema_dir()


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text())


class TraceDecodeResult:
    """Result of decoding and validating a trace export artifact."""

    def __init__(
        self,
        *,
        valid: bool,
        errors: list[str] | None = None,
        data: Any = None,
    ) -> None:
        self.valid = valid
        self.errors = errors or []
        self.data = data


def decode_trace_json(path: Path) -> TraceDecodeResult:
    """Load and validate a trace.json file."""
    errors: list[str] = []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    # JSON Schema validation
    try:
        schema = _load_schema("pipeline-trace.schema.json")
        jsonschema.validate(raw, schema)
    except jsonschema.ValidationError as exc:
        errors.append(f"schema: {exc.message}")

    # Absolute rule: source_domain MUST NOT be present
    if "source_domain" in json.dumps(raw):
        errors.append("trace.json contains forbidden field: source_domain")

    # Pydantic binding validation
    try:
        trace = PipelineTraceV1.model_validate(raw)
    except Exception as exc:
        errors.append(f"pydantic: {exc}")
        trace = raw

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=trace)


def decode_transition_log(path: Path) -> TraceDecodeResult:
    """Load and validate a transition_log.jsonl file.

    Each line is validated against the PipelineEventV1 schema.
    """
    errors: list[str] = []
    entries: list[Any] = []
    try:
        text = path.read_text().strip()
    except FileNotFoundError as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    if not text:
        return TraceDecodeResult(valid=True, data=[])

    schema = _load_schema("pipeline-event.schema.json")
    for idx, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {idx + 1}: {exc}")
            continue
        try:
            jsonschema.validate(raw, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"line {idx + 1} schema: {exc.message}")
        try:
            entries.append(PipelineEventV1.model_validate(raw))
        except Exception as exc:
            errors.append(f"line {idx + 1} pydantic: {exc}")

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=entries)


def decode_sidecar_meta(path: Path) -> TraceDecodeResult:
    """Load and validate a sidecar_meta.json file."""
    errors: list[str] = []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    try:
        schema = _load_schema("trace-sidecar-meta.schema.json")
        jsonschema.validate(raw, schema)
    except jsonschema.ValidationError as exc:
        errors.append(f"schema: {exc.message}")

    try:
        sidecar = TraceSidecarMeta.model_validate(raw)
    except Exception as exc:
        errors.append(f"pydantic: {exc}")
        sidecar = raw

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=sidecar)


def decode_prefix_slices(path: Path, *, format: str = "jsonl") -> TraceDecodeResult:
    """Load and validate text-projection prefix slice output (PrefixSliceTextV1)."""
    errors: list[str] = []
    slices: list[PrefixSliceTextV1] = []
    try:
        text = path.read_text().strip()
    except FileNotFoundError as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    if not text:
        return TraceDecodeResult(valid=True, data=[])

    schema = _load_schema("prefix-slice.schema.json")

    if format == "json":
        try:
            raw_list = json.loads(text)
        except json.JSONDecodeError as exc:
            return TraceDecodeResult(valid=False, errors=[str(exc)])
        items = raw_list if isinstance(raw_list, list) else [raw_list]
    else:
        items = []
        for idx, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"line {idx + 1}: {exc}")

    for idx, item in enumerate(items):
        try:
            jsonschema.validate(item, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"item {idx} schema: {exc.message}")
        try:
            slices.append(PrefixSliceTextV1.model_validate(item))
        except Exception as exc:
            errors.append(f"item {idx} pydantic: {exc}")

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=slices)


def decode_prefix_graph_slices(path: Path, *, format: str = "jsonl") -> TraceDecodeResult:
    """Load and validate graph-projection prefix slice output (PrefixSliceGraphV1)."""
    errors: list[str] = []
    slices: list[PrefixSliceGraphV1] = []
    try:
        text = path.read_text().strip()
    except FileNotFoundError as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    if not text:
        return TraceDecodeResult(valid=True, data=[])

    schema = _load_schema("prefix-slice-graph.schema.json")

    if format == "json":
        try:
            raw_list = json.loads(text)
        except json.JSONDecodeError as exc:
            return TraceDecodeResult(valid=False, errors=[str(exc)])
        items = raw_list if isinstance(raw_list, list) else [raw_list]
    else:
        items = []
        for idx, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"line {idx + 1}: {exc}")

    for idx, item in enumerate(items):
        try:
            jsonschema.validate(item, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"item {idx} schema: {exc.message}")
        try:
            slices.append(PrefixSliceGraphV1.model_validate(item))
        except Exception as exc:
            errors.append(f"item {idx} pydantic: {exc}")

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=slices)


# ---------------------------------------------------------------------------
# v2 decoders
# ---------------------------------------------------------------------------


def decode_trace_v2(path: Path) -> TraceDecodeResult:
    """Load and validate a v2 trace.json file against PipelineTraceV2."""
    errors: list[str] = []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    # JSON Schema validation
    try:
        schema = _load_schema("pipeline-trace-v2.schema.json")
        jsonschema.validate(raw, schema)
    except jsonschema.ValidationError as exc:
        errors.append(f"schema: {exc.message}")

    # Absolute rule: source_domain MUST NOT be present in model-visible trace
    if "source_domain" in json.dumps(raw):
        errors.append("trace.json contains forbidden field: source_domain")

    # Pydantic binding validation
    try:
        trace = PipelineTraceV2.model_validate(raw)
    except Exception as exc:
        errors.append(f"pydantic: {exc}")
        trace = raw

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=trace)


def decode_transition_log_v2(path: Path) -> TraceDecodeResult:
    """Load and validate a v2 transition_log.jsonl file.

    Each line is validated against the PipelineEventV2 schema.
    """
    errors: list[str] = []
    entries: list[Any] = []
    try:
        text = path.read_text().strip()
    except FileNotFoundError as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    if not text:
        return TraceDecodeResult(valid=True, data=[])

    schema = _load_schema("pipeline-event-v2.schema.json")
    for idx, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {idx + 1}: {exc}")
            continue
        try:
            jsonschema.validate(raw, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"line {idx + 1} schema: {exc.message}")
        try:
            entries.append(PipelineEventV2.model_validate(raw))
        except Exception as exc:
            errors.append(f"line {idx + 1} pydantic: {exc}")

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=entries)


def decode_sidecar_v2(path: Path) -> TraceDecodeResult:
    """Load and validate a v2 sidecar_meta.json file against TraceSidecarMetaV2."""
    errors: list[str] = []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    try:
        schema = _load_schema("trace-sidecar-meta-v2.schema.json")
        jsonschema.validate(raw, schema)
    except jsonschema.ValidationError as exc:
        errors.append(f"schema: {exc.message}")

    try:
        sidecar = TraceSidecarMetaV2.model_validate(raw)
    except Exception as exc:
        errors.append(f"pydantic: {exc}")
        sidecar = raw

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=sidecar)


def decode_prefix_text_slices_v2(path: Path, *, format: str = "jsonl") -> TraceDecodeResult:
    """Load and validate v2-frozen text-projection prefix slice output (PrefixSliceTextV1Frozen)."""
    errors: list[str] = []
    slices: list[PrefixSliceTextV1Frozen] = []
    try:
        text = path.read_text().strip()
    except FileNotFoundError as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    if not text:
        return TraceDecodeResult(valid=True, data=[])

    schema = _load_schema("prefix-slice-text-v1.schema.json")

    if format == "json":
        try:
            raw_list = json.loads(text)
        except json.JSONDecodeError as exc:
            return TraceDecodeResult(valid=False, errors=[str(exc)])
        items = raw_list if isinstance(raw_list, list) else [raw_list]
    else:
        items = []
        for idx, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"line {idx + 1}: {exc}")

    for idx, item in enumerate(items):
        try:
            jsonschema.validate(item, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"item {idx} schema: {exc.message}")
        try:
            slices.append(PrefixSliceTextV1Frozen.model_validate(item))
        except Exception as exc:
            errors.append(f"item {idx} pydantic: {exc}")

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=slices)


def decode_prefix_graph_slices_v2(path: Path, *, format: str = "jsonl") -> TraceDecodeResult:
    """Load and validate v2-frozen graph-projection prefix slice output (PrefixSliceGraphV1Frozen)."""
    errors: list[str] = []
    slices: list[PrefixSliceGraphV1Frozen] = []
    try:
        text = path.read_text().strip()
    except FileNotFoundError as exc:
        return TraceDecodeResult(valid=False, errors=[str(exc)])

    if not text:
        return TraceDecodeResult(valid=True, data=[])

    schema = _load_schema("prefix-slice-graph-v1.schema.json")

    if format == "json":
        try:
            raw_list = json.loads(text)
        except json.JSONDecodeError as exc:
            return TraceDecodeResult(valid=False, errors=[str(exc)])
        items = raw_list if isinstance(raw_list, list) else [raw_list]
    else:
        items = []
        for idx, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"line {idx + 1}: {exc}")

    for idx, item in enumerate(items):
        try:
            jsonschema.validate(item, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"item {idx} schema: {exc.message}")
        try:
            slices.append(PrefixSliceGraphV1Frozen.model_validate(item))
        except Exception as exc:
            errors.append(f"item {idx} pydantic: {exc}")

    return TraceDecodeResult(valid=len(errors) == 0, errors=errors, data=slices)
