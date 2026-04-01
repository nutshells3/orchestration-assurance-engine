"""Structured evaluation-evidence extractors over source-mapping bundles."""

from __future__ import annotations

import re
from typing import Any

from .document_ingest import TRACE_ADAPTER_VERSION, slugify
from .models import TraceDocumentIngestBundle, TraceEvaluationEvidence

_VALUE_PATTERN = r"-?\d+(?:\.\d+)?"
_MEASUREMENT_RE = re.compile(
    rf"(?P<metric>.+?)\s+(?P<verb>falls|drops|decreases|declines|improves|improved|reduced|reduction)\s+"
    rf"from\s+(?P<baseline>{_VALUE_PATTERN})\s*(?P<unit1>%|[A-Za-z]+)?\s+"
    rf"to\s+(?P<reported>{_VALUE_PATTERN})\s*(?P<unit2>%|[A-Za-z]+)?",
    re.IGNORECASE,
)
_CI_RE = re.compile(
    rf"(?P<label>\d+%\s*CI)\s*[\[\(]\s*(?P<low>{_VALUE_PATTERN})\s*,\s*(?P<high>{_VALUE_PATTERN})\s*[\]\)]",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(r"\b(Table|Tbl\.?)\s+(?P<label>[A-Za-z0-9.-]+)", re.IGNORECASE)
_FIGURE_RE = re.compile(r"\b(Figure|Fig\.?)\s+(?P<label>[A-Za-z0-9.-]+)", re.IGNORECASE)
_SPLIT_RE = re.compile(r"\b(train|training|dev|validation|test|holdout)\b", re.IGNORECASE)
_COMPARISON_RE = re.compile(r"\b(baseline|control|guarded|treatment)\b", re.IGNORECASE)


def _as_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _clean_metric_name(text: str) -> str | None:
    cleaned = re.sub(r"^(under|with|during|on)\b[^,]*,\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.rstrip(" .,:;")
    return cleaned or None


def _find_table_figure_label(text: str) -> tuple[str | None, str | None]:
    table = _TABLE_RE.search(text)
    if table:
        label = str(table.group("label"))
        return f"Table {label}", None
    figure = _FIGURE_RE.search(text)
    if figure:
        label = str(figure.group("label"))
        return None, f"Figure {label}"
    return None, None


def _parse_measurement(text: str) -> dict[str, Any]:
    match = _MEASUREMENT_RE.search(text)
    if not match:
        return {}
    baseline_value = _as_float(match.group("baseline"))
    reported_value = _as_float(match.group("reported"))
    unit = match.group("unit2") or match.group("unit1")
    metric_name = _clean_metric_name(str(match.group("metric")))
    verb = str(match.group("verb")).lower()
    if baseline_value is not None and reported_value is not None:
        delta_value = reported_value - baseline_value
    else:
        delta_value = None
    if any(token in verb for token in ("improv",)):
        direction = "higher_is_better"
    elif baseline_value is not None and reported_value is not None and reported_value < baseline_value:
        direction = "lower_is_better"
    else:
        direction = "higher_is_better"
    return {
        "metric_name": metric_name,
        "baseline_value": baseline_value,
        "baseline_value_text": match.group("baseline"),
        "reported_value": reported_value,
        "reported_value_text": match.group("reported"),
        "delta_value": delta_value,
        "unit": unit,
        "direction": direction,
    }


def _parse_confidence_interval(text: str) -> dict[str, Any]:
    match = _CI_RE.search(text)
    if not match:
        return {}
    return {
        "label": str(match.group("label")),
        "lower": _as_float(match.group("low")),
        "upper": _as_float(match.group("high")),
    }


def _extract_split(text: str) -> str | None:
    match = _SPLIT_RE.search(text)
    if not match:
        return None
    token = str(match.group(1)).lower()
    if token == "training":
        return "train"
    return token


def _extract_comparison_target(text: str) -> str | None:
    match = _COMPARISON_RE.search(text)
    if not match:
        return None
    token = str(match.group(1)).lower()
    if token == "treatment":
        return "baseline"
    return token


def extract_evaluation_evidence(
    bundle: TraceDocumentIngestBundle,
) -> list[TraceEvaluationEvidence]:
    evidence_by_source_claim = {
        str((item.provenance or {}).get("source_claim_id") or ""): item
        for item in bundle.evidence_items
        if (item.provenance or {}).get("source_claim_id")
    }
    evaluations: list[TraceEvaluationEvidence] = []
    for mapping in bundle.mapping_report.claim_mappings:
        if mapping.source_role != "observation" and mapping.proposed_claim_class != "metric":
            continue
        evidence_item = evidence_by_source_claim.get(mapping.source_claim_id)
        anchor = evidence_item.citation_anchor if evidence_item else mapping.citation_anchor
        excerpt = ""
        if evidence_item and evidence_item.excerpt:
            excerpt = str(evidence_item.excerpt)
        elif anchor and anchor.excerpt:
            excerpt = str(anchor.excerpt)
        if not excerpt:
            continue
        measurement = _parse_measurement(excerpt)
        confidence_interval = _parse_confidence_interval(excerpt)
        table_label, figure_label = _find_table_figure_label(
            str(anchor.source_location if anchor else "") or excerpt
        )
        comparison_target = _extract_comparison_target(excerpt)
        evaluation = TraceEvaluationEvidence(
            evidence_id=f"evaluation.{slugify(mapping.proposed_claim_id)}",
            title=evidence_item.title if evidence_item else mapping.source_claim_id,
            summary=(
                evidence_item.summary
                if evidence_item
                else f"Structured evaluation evidence for {mapping.proposed_claim_id}."
            ),
            source_ref=str(anchor.source_ref if anchor else bundle.document_ref),
            source_claim_id=mapping.source_claim_id,
            claim_candidate_id=mapping.proposed_claim_id,
            dataset=bundle.source_document.display_name or bundle.source_document.title,
            metric_name=measurement.get("metric_name"),
            split=_extract_split(excerpt),
            comparison_target=comparison_target or "baseline",
            baseline_value=measurement.get("baseline_value"),
            baseline_value_text=measurement.get("baseline_value_text"),
            reported_value=measurement.get("reported_value"),
            reported_value_text=measurement.get("reported_value_text"),
            delta_value=measurement.get("delta_value"),
            unit=measurement.get("unit"),
            direction=measurement.get("direction"),
            table_figure_anchor=str(anchor.source_location if anchor else "") or None,
            cited_table_label=table_label,
            cited_figure_label=figure_label,
            confidence_interval=confidence_interval,
            citation_anchor=anchor,
            provenance={
                "adapter": "evaluation_evidence",
                "adapter_version": TRACE_ADAPTER_VERSION,
                "source_mapping_bundle_document_id": bundle.source_document.document_id,
                "source_claim_id": mapping.source_claim_id,
            },
            uncertainty={
                "confidence": 0.7 if measurement else 0.45,
                "requires_human_admission": True,
                "comparison_target": comparison_target or "baseline",
                "anchor_status": getattr(anchor, "status", None),
            },
        )
        evaluations.append(evaluation)
    return evaluations


__all__ = ["extract_evaluation_evidence"]
