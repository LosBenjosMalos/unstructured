"""Extraction timing and usage reporting.

This module is intentionally isolated from the extraction logic. The pipeline
and provider adapters only call the small monitor hooks; usage normalization,
timing aggregation, and Streamlit report rendering live here so the feature can
be removed with minimal changes elsewhere.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
import threading
import time
from typing import Any


TOKEN_FIELDS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "prompt_token_count",
    "candidates_token_count",
    "total_token_count",
    "cached_content_token_count",
    "thoughts_token_count",
}


@dataclass
class ModelCallTrace:
    """One provider request with timing, usage, and result metadata."""

    call_id: int
    provider: str
    model: str
    page_number: int
    operation: str
    prompt: str
    started_at: float
    status: str = "running"
    finished_at: float | None = None
    elapsed_seconds: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    raw_text_characters: int | None = None


class ExtractionDebugMonitor:
    """Thread-safe collector for extraction timing and final reports."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_call_id = 1
        self._calls: list[ModelCallTrace] = []
        self._run_started_at = time.perf_counter()
        self._run_finished_at: float | None = None

    def finish_run(self) -> None:
        """Mark the run as finished for final elapsed-time reporting."""

        with self._lock:
            if self._run_finished_at is None:
                self._run_finished_at = time.perf_counter()

    def start_model_call(
        self,
        provider: str,
        model: str,
        page_number: int,
        operation: str,
        prompt: str,
    ) -> int:
        """Record a provider request just before the SDK call is made."""

        now = time.perf_counter()
        with self._lock:
            call_id = self._next_call_id
            self._next_call_id += 1
            trace = ModelCallTrace(
                call_id=call_id,
                provider=provider,
                model=model,
                page_number=page_number,
                operation=operation,
                prompt=prompt,
                started_at=now,
            )
            self._calls.append(trace)
            return call_id

    def finish_model_call(
        self,
        call_id: int,
        *,
        response: Any = None,
        usage: dict[str, Any] | None = None,
        raw_text: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Mark a provider request complete and return normalized usage data."""

        now = time.perf_counter()
        normalized_usage = usage if usage is not None else extract_token_usage(response)
        with self._lock:
            for trace in self._calls:
                if trace.call_id == call_id:
                    trace.finished_at = now
                    trace.elapsed_seconds = now - trace.started_at
                    trace.usage = normalized_usage
                    trace.error = error
                    trace.raw_text_characters = len(raw_text) if raw_text is not None else None
                    trace.status = "error" if error else "complete"
                    break
        return normalized_usage

    def snapshot(self) -> dict[str, Any]:
        """Return a report-only JSON-serializable snapshot for export."""

        with self._lock:
            calls = [_trace_to_dict(call) for call in self._calls]
            run_finished_at = self._run_finished_at
            run_started_at = self._run_started_at

        return {
            "report": build_timing_report(calls, run_started_at, run_finished_at),
        }


def _trace_to_dict(trace: ModelCallTrace) -> dict[str, Any]:
    """Convert a trace dataclass to a stable debug dictionary."""

    return {
        "call_id": trace.call_id,
        "provider": trace.provider,
        "model": trace.model,
        "page_number": trace.page_number,
        "operation": trace.operation,
        "status": trace.status,
        "elapsed_seconds": trace.elapsed_seconds,
        "usage": deepcopy(trace.usage),
        "error": trace.error,
        "raw_text_characters": trace.raw_text_characters,
    }


def extract_token_usage(response: Any) -> dict[str, Any]:
    """Normalize token usage metadata from OpenAI, Gemini, or Anthropic objects."""

    if response is None:
        return {"available": False}

    usage = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
    if usage is None:
        return {"available": False}

    raw_usage = public_object_dict(usage)
    normalized: dict[str, Any] = {"available": True, "raw": raw_usage}

    input_tokens = first_numeric(raw_usage, ["input_tokens", "prompt_tokens", "prompt_token_count"])
    output_tokens = first_numeric(
        raw_usage,
        ["output_tokens", "completion_tokens", "candidates_token_count"],
    )
    total_tokens = first_numeric(raw_usage, ["total_tokens", "total_token_count"])
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    if input_tokens is not None:
        normalized["input_tokens"] = input_tokens
    if output_tokens is not None:
        normalized["output_tokens"] = output_tokens
    if total_tokens is not None:
        normalized["total_tokens"] = total_tokens

    for field_name in TOKEN_FIELDS:
        if field_name in raw_usage and field_name not in normalized:
            normalized[field_name] = raw_usage[field_name]

    return normalized


def public_object_dict(value: Any) -> dict[str, Any]:
    """Best-effort conversion of SDK usage objects into primitive dictionaries."""

    if isinstance(value, dict):
        items = value.items()
    elif hasattr(value, "model_dump"):
        dumped = value.model_dump()
        items = dumped.items() if isinstance(dumped, dict) else []
    elif hasattr(value, "to_dict"):
        dumped = value.to_dict()
        items = dumped.items() if isinstance(dumped, dict) else []
    else:
        pairs = []
        for name in dir(value):
            if name.startswith("_"):
                continue
            try:
                item = getattr(value, name)
            except Exception:
                continue
            if not callable(item):
                pairs.append((name, item))
        items = pairs

    result: dict[str, Any] = {}
    for key, item in items:
        if key.startswith("_"):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = item
        elif isinstance(item, dict):
            result[key] = {
                nested_key: nested_value
                for nested_key, nested_value in item.items()
                if isinstance(nested_value, (str, int, float, bool)) or nested_value is None
            }
    return result


def first_numeric(data: dict[str, Any], keys: list[str]) -> int | float | None:
    """Return the first numeric value in a dictionary for the provided keys."""

    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
    return None


def build_timing_report(
    calls: list[dict[str, Any]],
    run_started_at: float,
    run_finished_at: float | None,
) -> dict[str, Any]:
    """Aggregate provider timing and token usage for the run report."""

    provider_totals: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "provider": "",
            "model": "",
            "calls": 0,
            "completed_calls": 0,
            "errored_calls": 0,
            "total_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "unknown_token_calls": 0,
        }
    )

    for call in calls:
        key = (call["provider"], call["model"])
        total = provider_totals[key]
        total["provider"] = call["provider"]
        total["model"] = call["model"]
        total["calls"] += 1
        if call["status"] == "complete":
            total["completed_calls"] += 1
        if call["status"] == "error":
            total["errored_calls"] += 1
        if isinstance(call.get("elapsed_seconds"), (int, float)):
            total["total_seconds"] += call["elapsed_seconds"]

        usage = call.get("usage") or {}
        if usage.get("available"):
            for token_field in ["input_tokens", "output_tokens", "total_tokens"]:
                value = usage.get(token_field)
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    total[token_field] += value
        else:
            total["unknown_token_calls"] += 1

    finished_at = run_finished_at or time.perf_counter()
    return {
        "run_elapsed_seconds": finished_at - run_started_at,
        "providers": list(provider_totals.values()),
    }


def render_run_report(snapshot_or_report: dict[str, Any] | None) -> None:
    """Render provider totals for elapsed time and token usage."""

    import streamlit as st

    snapshot_or_report = snapshot_or_report or {}
    report = snapshot_or_report.get("report", snapshot_or_report)
    providers = report.get("providers", [])
    st.subheader("Run report")
    st.write({"run_elapsed_seconds": round(report.get("run_elapsed_seconds", 0.0), 2)})
    if not providers:
        st.caption("No provider calls recorded yet.")
        return

    rows = []
    for provider in providers:
        rows.append(
            {
                "provider": provider["provider"],
                "model": provider["model"],
                "calls": provider["calls"],
                "errors": provider["errored_calls"],
                "seconds": round(provider["total_seconds"], 2),
                "input_tokens": provider["input_tokens"] or None,
                "output_tokens": provider["output_tokens"] or None,
                "total_tokens": provider["total_tokens"] or None,
                "unknown_token_calls": provider["unknown_token_calls"],
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
