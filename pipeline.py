"""End-to-end orchestration for configurable page-level extraction."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from compare import (
    CanonicalPage,
    canonicalize_page_output,
    compare_pages,
    key_sort_key,
    mark_record_source,
    normalize_key_value,
)
from debug_monitor import ExtractionDebugMonitor
from pdf_pages import PageWindow, split_pdf_into_pages
from providers import ClaudeMediator, GeminiExtractor, OpenAIExtractor, ProviderResult
from schema import following_context_pages


ProgressCallback = Callable[[str, int, int], None]
ProviderTask = Callable[[], ProviderResult]
PROVIDER_POLL_SECONDS = 0.25


@dataclass
class PipelineConfig:
    """Runtime configuration for providers, models, selected profile, and limits."""

    openai_api_key: str
    gemini_api_key: str
    anthropic_api_key: str | None
    extraction_profile: dict[str, Any]
    openai_model: str = "gpt-5.4-mini"
    gemini_model: str = "gemini-3-flash-preview"
    claude_model: str = "claude-opus-4-6"
    max_pages: int | None = None


def provider_debug(result: ProviderResult | None) -> dict[str, Any] | None:
    """Convert a provider result into JSON-serializable debug data."""

    if result is None:
        return None
    return {
        "provider": result.provider,
        "model": result.model,
        "page_number": result.page_number,
        "operation": result.operation,
        "duration_seconds": result.duration_seconds,
        "usage": result.usage,
        "parsed": result.parsed,
        "raw_text": result.raw_text,
        "error": result.error,
    }


def run_provider_tasks(
    tasks: dict[str, ProviderTask],
    *,
    page_number: int,
    operation: str,
    progress_callback: ProgressCallback | None = None,
    progress_index: int = 0,
    progress_total: int = 1,
    task_labels: dict[str, str] | None = None,
) -> dict[str, ProviderResult]:
    """Run provider calls concurrently while allowing Streamlit progress refreshes."""

    results: dict[str, ProviderResult] = {}
    if not tasks:
        return results

    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_names: dict[Future[ProviderResult], str] = {
            executor.submit(task): name for name, task in tasks.items()
        }
        pending = set(future_names)
        while pending:
            done, pending = wait(
                pending,
                timeout=PROVIDER_POLL_SECONDS,
                return_when=FIRST_COMPLETED,
            )
            if progress_callback:
                waiting_on = sorted(
                    (task_labels or {}).get(future_names[future], future_names[future])
                    for future in pending
                )
                if waiting_on:
                    progress_callback(
                        f"Page {page_number}: Waiting for {', '.join(waiting_on)} during {operation}",
                        progress_index,
                        progress_total,
                    )
            for future in done:
                results[future_names[future]] = future.result()

    return results


def canonical_debug(page: CanonicalPage | None) -> dict[str, Any] | None:
    """Expose canonicalized output details for Streamlit debugging."""

    if page is None:
        return None
    return {
        "provider": page.provider,
        "page_number": page.page_number,
        "order": [page.key_display.get(key_id, key_id) for key_id in page.order],
        "records_by_key": page.records_by_key,
        "errors": page.errors,
        "warnings": page.warnings,
    }


def normalize_manifest_keys(
    parsed: dict[str, Any] | None,
    profile: dict[str, Any],
) -> tuple[list[str], dict[str, str], list[str]]:
    """Normalize a provider key-manifest response into comparable key ids."""

    if parsed is None:
        return [], {}, ["provider returned no parseable JSON"]
    if not isinstance(parsed.get("record_keys"), list):
        return [], {}, ["key manifest is missing record_keys array"]

    key_ids: list[str] = []
    display_by_key: dict[str, str] = {}
    errors: list[str] = []
    for index, value in enumerate(parsed["record_keys"]):
        key_id, key_display, key_errors = normalize_key_value(value, profile)
        errors.extend(f"record_keys[{index}]: {error}" for error in key_errors)
        if not key_id or key_display is None:
            errors.append(f"record_keys[{index}] could not be normalized")
            continue
        if key_id not in display_by_key:
            key_ids.append(key_id)
            display_by_key[key_id] = key_display

    return key_ids, display_by_key, errors


def build_key_manifest(
    page: PageWindow,
    profile: dict[str, Any],
    openai_extractor: OpenAIExtractor,
    gemini_extractor: GeminiExtractor,
    progress_callback: ProgressCallback | None = None,
    progress_index: int = 0,
    progress_total: int = 1,
) -> dict[str, Any]:
    """Ask both baseline providers for anchor-page record keys and merge them."""

    manifest_results = run_provider_tasks(
        {
            "OpenAI": lambda: openai_extractor.extract_key_manifest(
                page.anchor_page_path,
                page.anchor_page_number,
                profile,
            ),
            "Gemini": lambda: gemini_extractor.extract_key_manifest(
                page.anchor_page_path,
                page.anchor_page_number,
                profile,
            ),
        },
        page_number=page.page_number,
        operation="key manifest",
        progress_callback=progress_callback,
        progress_index=progress_index,
        progress_total=progress_total,
        task_labels={
            "OpenAI": f"OpenAI model {openai_extractor.model}",
            "Gemini": f"Gemini model {gemini_extractor.model}",
        },
    )
    openai_result = manifest_results["OpenAI"]
    gemini_result = manifest_results["Gemini"]

    openai_key_ids, openai_display, openai_errors = normalize_manifest_keys(openai_result.parsed, profile)
    gemini_key_ids, gemini_display, gemini_errors = normalize_manifest_keys(gemini_result.parsed, profile)

    openai_keys = set(openai_key_ids)
    gemini_keys = set(gemini_key_ids)
    expected_key_ids = sorted(openai_keys | gemini_keys, key=key_sort_key)
    display_by_key = {**gemini_display, **openai_display}

    missing_openai_ids = sorted(gemini_keys - openai_keys, key=key_sort_key)
    missing_gemini_ids = sorted(openai_keys - gemini_keys, key=key_sort_key)

    return {
        "expected_key_ids": expected_key_ids,
        "expected_keys": [display_by_key.get(key_id, key_id) for key_id in expected_key_ids],
        "display_by_key": display_by_key,
        "agreed": not openai_errors
        and not gemini_errors
        and not missing_openai_ids
        and not missing_gemini_ids,
        "missing_openai": [display_by_key.get(key_id, key_id) for key_id in missing_openai_ids],
        "missing_gemini": [display_by_key.get(key_id, key_id) for key_id in missing_gemini_ids],
        "openai_errors": openai_errors,
        "gemini_errors": gemini_errors,
        "openai": provider_debug(openai_result),
        "gemini": provider_debug(gemini_result),
        "normalized": {
            "openai": [openai_display.get(key_id, key_id) for key_id in openai_key_ids],
            "gemini": [gemini_display.get(key_id, key_id) for key_id in gemini_key_ids],
        },
    }


def empty_manual_review_record(key_display: str, profile: dict[str, Any], page_number: int) -> dict[str, Any]:
    """Create a minimal manual-review record when every model misses details."""

    key_field = profile.get("key_field")
    return {
        key_field: key_display,
        "source_page": page_number,
        "resolution_source": "manual_review",
        "needs_review": True,
    }


def best_manual_review_record(
    key_id: str,
    key_display: str,
    openai_page: CanonicalPage,
    gemini_page: CanonicalPage,
    profile: dict[str, Any],
    page_number: int,
) -> dict[str, Any]:
    """Choose an inspectable fallback record when mediation cannot resolve a key."""

    candidate = openai_page.records_by_key.get(key_id) or gemini_page.records_by_key.get(key_id)
    if candidate is None:
        return empty_manual_review_record(key_display, profile, page_number)
    copied = mark_record_source(candidate, "manual_review")
    copied["source_page"] = page_number
    copied["needs_review"] = True
    return copied


def merge_page_records(
    page_number: int,
    profile: dict[str, Any],
    openai_page: CanonicalPage,
    gemini_page: CanonicalPage,
    diff: dict[str, Any],
    claude_page: CanonicalPage | None,
) -> tuple[list[dict[str, Any]], str]:
    """Merge agreed baseline records with Claude-mediated disputed records."""

    final_records: list[dict[str, Any]] = []
    status = "agreed" if diff["agreed"] else "mediated"
    expected_key_ids = list(diff["expected_key_ids"])
    disputed_key_ids = set(diff["disputed_key_ids"])
    display_by_key = {**gemini_page.key_display, **openai_page.key_display}

    # If both baselines fail to produce a manifest, use Claude's keys so a valid
    # mediation result can still be exported.
    if not expected_key_ids and claude_page is not None and not claude_page.errors:
        expected_key_ids = sorted(claude_page.records_by_key, key=key_sort_key)
        display_by_key.update(claude_page.key_display)
        disputed_key_ids.update(expected_key_ids)

    if openai_page.errors or gemini_page.errors or diff["missing_openai"] or diff["missing_gemini"]:
        disputed_key_ids.update(expected_key_ids)
    if diff.get("unexpected_openai") or diff.get("unexpected_gemini"):
        status = "needs_review"

    for key_id in expected_key_ids:
        key_display = display_by_key.get(key_id, key_id)
        openai_record = openai_page.records_by_key.get(key_id)
        gemini_record = gemini_page.records_by_key.get(key_id)

        if key_id not in disputed_key_ids and openai_record is not None and gemini_record is not None:
            record = mark_record_source(openai_record, "agreed")
            record["source_page"] = page_number
            final_records.append(record)
            continue

        if claude_page is not None and not claude_page.errors and key_id in claude_page.records_by_key:
            record = mark_record_source(claude_page.records_by_key[key_id], "claude")
            record["source_page"] = page_number
            final_records.append(record)
            continue

        status = "needs_review"
        final_records.append(
            best_manual_review_record(key_id, key_display, openai_page, gemini_page, profile, page_number)
        )

    unexpected_key_ids = sorted(
        set(diff.get("unexpected_openai_ids", [])) | set(diff.get("unexpected_gemini_ids", [])),
        key=key_sort_key,
    )
    for key_id in unexpected_key_ids:
        if key_id in expected_key_ids:
            continue
        key_display = display_by_key.get(key_id, key_id)
        record = best_manual_review_record(key_id, key_display, openai_page, gemini_page, profile, page_number)
        record["unexpected_key"] = True
        final_records.append(record)

    if not final_records and not diff["agreed"]:
        status = "needs_review"

    return final_records, status


def process_page(
    page: PageWindow,
    profile: dict[str, Any],
    openai_extractor: OpenAIExtractor,
    gemini_extractor: GeminiExtractor,
    claude_mediator: ClaudeMediator | None,
    use_context_manifest: bool = False,
    progress_callback: ProgressCallback | None = None,
    progress_index: int = 0,
    progress_total: int = 1,
) -> dict[str, Any]:
    """Run both baselines, compare them, optionally mediate, and merge one page."""

    key_manifest = None
    expected_key_ids: list[str] | None = None
    expected_key_display: dict[str, str] | None = None
    expected_keys_for_prompt: list[str] | None = None
    if use_context_manifest:
        key_manifest = build_key_manifest(
            page,
            profile,
            openai_extractor,
            gemini_extractor,
            progress_callback=progress_callback,
            progress_index=progress_index,
            progress_total=progress_total,
        )
        expected_key_ids = key_manifest["expected_key_ids"]
        expected_key_display = key_manifest["display_by_key"]
        expected_keys_for_prompt = key_manifest["expected_keys"]

    baseline_results = run_provider_tasks(
        {
            "OpenAI": lambda: openai_extractor.extract_page(
                page.extraction_pdf_path,
                page.page_number,
                profile,
                expected_keys=expected_keys_for_prompt,
                context_page_numbers=page.context_page_numbers,
            ),
            "Gemini": lambda: gemini_extractor.extract_page(
                page.extraction_pdf_path,
                page.page_number,
                profile,
                expected_keys=expected_keys_for_prompt,
                context_page_numbers=page.context_page_numbers,
            ),
        },
        page_number=page.page_number,
        operation="baseline extraction",
        progress_callback=progress_callback,
        progress_index=progress_index,
        progress_total=progress_total,
        task_labels={
            "OpenAI": f"OpenAI model {openai_extractor.model}",
            "Gemini": f"Gemini model {gemini_extractor.model}",
        },
    )
    openai_result = baseline_results["OpenAI"]
    gemini_result = baseline_results["Gemini"]

    openai_page = canonicalize_page_output(openai_result.parsed, "openai", page.page_number, profile)
    gemini_page = canonicalize_page_output(gemini_result.parsed, "gemini", page.page_number, profile)
    diff = compare_pages(
        openai_page,
        gemini_page,
        expected_key_ids=expected_key_ids,
        expected_key_display=expected_key_display,
    )

    claude_result: ProviderResult | None = None
    claude_page: CanonicalPage | None = None
    mediated = False

    if not diff["agreed"]:
        mediated = claude_mediator is not None
        if claude_mediator is not None:
            mediation_results = run_provider_tasks(
                {
                    "Claude": lambda: claude_mediator.mediate_page(
                        page_pdf_path=page.extraction_pdf_path,
                        page_number=page.page_number,
                        profile=profile,
                        openai_output=openai_result.parsed,
                        gemini_output=gemini_result.parsed,
                        diff=diff,
                        expected_keys=expected_keys_for_prompt,
                        context_page_numbers=page.context_page_numbers,
                    )
                },
                page_number=page.page_number,
                operation="mediation",
                progress_callback=progress_callback,
                progress_index=progress_index,
                progress_total=progress_total,
                task_labels={"Claude": f"Claude model {claude_mediator.model}"},
            )
            claude_result = mediation_results["Claude"]
            claude_page = canonicalize_page_output(claude_result.parsed, "claude", page.page_number, profile)

    final_records, status = merge_page_records(
        page_number=page.page_number,
        profile=profile,
        openai_page=openai_page,
        gemini_page=gemini_page,
        diff=diff,
        claude_page=claude_page,
    )

    if not diff["agreed"] and claude_mediator is None:
        status = "needs_review"
    if key_manifest is not None and not key_manifest["agreed"] and status == "agreed":
        for record in final_records:
            record["needs_review"] = True
        status = "needs_review"

    return {
        "page_number": page.page_number,
        "context_pages": page.context_page_numbers,
        "expected_keys": diff["expected_keys"],
        "baseline_status": status,
        "mediated": mediated,
        "records": final_records,
        "diff": diff,
        "debug": {
            "openai": provider_debug(openai_result),
            "gemini": provider_debug(gemini_result),
            "claude": provider_debug(claude_result),
            "key_manifest": key_manifest,
            "canonical": {
                "openai": canonical_debug(openai_page),
                "gemini": canonical_debug(gemini_page),
                "claude": canonical_debug(claude_page),
            },
        },
    }


def find_duplicate_keys(records: list[dict[str, Any]], key_field: str) -> dict[str, list[int]]:
    """Find configured record keys that appear on more than one source page."""

    pages_by_key: dict[str, list[int]] = {}
    for record in records:
        if key_field in record:
            pages_by_key.setdefault(str(record[key_field]), []).append(record.get("source_page"))
    return {key: pages for key, pages in pages_by_key.items() if len(pages) > 1}


def build_document_export(document_id: str, profile: dict[str, Any], page_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the final document-level JSON from page-level results."""

    records_key = profile.get("records_key", "records")
    key_field = profile.get("key_field")
    records = [record for page in page_results for record in page["records"]]
    records = sorted(records, key=lambda item: str(item.get(key_field, "")))
    duplicates = find_duplicate_keys(records, key_field)

    if duplicates:
        for record in records:
            if str(record.get(key_field)) in duplicates:
                record["needs_review"] = True
                record["duplicate_pages"] = duplicates[str(record.get(key_field))]

    pages = [
        {
            "page_number": page["page_number"],
            "context_pages": page.get("context_pages", []),
            "expected_keys": page["expected_keys"],
            "baseline_status": page["baseline_status"],
            "mediated": page["mediated"],
        }
        for page in page_results
    ]

    return {
        "document_id": document_id,
        "profile_name": profile.get("name"),
        "key_field": key_field,
        "pages": pages,
        records_key: records,
    }


def process_document(
    pdf_path: str | Path,
    document_id: str,
    config: PipelineConfig,
    progress_callback: ProgressCallback | None = None,
    debug_monitor: ExtractionDebugMonitor | None = None,
) -> dict[str, Any]:
    """Process a full PDF page by page and return final plus debug JSON."""

    profile = config.extraction_profile
    context_pages = following_context_pages(profile)
    prepared_pdf = split_pdf_into_pages(
        pdf_path,
        max_pages=config.max_pages,
        following_context_pages=context_pages,
    )
    openai_extractor = OpenAIExtractor(config.openai_api_key, config.openai_model, debug_monitor)
    gemini_extractor = GeminiExtractor(config.gemini_api_key, config.gemini_model, debug_monitor)
    claude_mediator = (
        ClaudeMediator(config.anthropic_api_key, config.claude_model, debug_monitor)
        if config.anthropic_api_key
        else None
    )

    page_results: list[dict[str, Any]] = []
    try:
        total_pages = len(prepared_pdf.windows)
        for index, page in enumerate(prepared_pdf.windows, start=1):
            if progress_callback:
                progress_callback(f"Processing page {page.page_number}", index - 1, total_pages)
            page_results.append(
                process_page(
                    page,
                    profile,
                    openai_extractor,
                    gemini_extractor,
                    claude_mediator,
                    use_context_manifest=context_pages > 0,
                    progress_callback=progress_callback,
                    progress_index=index - 1,
                    progress_total=total_pages,
                )
            )
            if progress_callback:
                progress_callback(f"Completed page {page.page_number}", index, total_pages)

        final_json = build_document_export(document_id, profile, page_results)
        if debug_monitor:
            debug_monitor.finish_run()
        run_debug = debug_monitor.snapshot() if debug_monitor else None
        debug_json = {
            "document_id": document_id,
            "page_count": len(page_results),
            "profile": profile,
            "config": {
                "openai_model": config.openai_model,
                "gemini_model": config.gemini_model,
                "claude_model": config.claude_model,
                "claude_enabled": bool(config.anthropic_api_key),
                "max_pages": config.max_pages,
                "following_context_pages": context_pages,
            },
            "pages": page_results,
            "run_debug": run_debug,
        }
        return {"final": final_json, "debug": debug_json}
    finally:
        if debug_monitor:
            debug_monitor.finish_run()
        prepared_pdf.cleanup()


def to_pretty_json(data: Any) -> str:
    """Serialize dictionaries for download buttons and debug display."""

    return json.dumps(data, indent=2, ensure_ascii=False)
