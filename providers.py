"""Provider adapters for OpenAI, Gemini, and Claude.

The adapters are schema/profile-aware but remain thin: each one performs a
single model call and returns parsed JSON plus debug metadata.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from debug_monitor import ExtractionDebugMonitor, extract_token_usage
from schema import (
    extraction_prompt,
    key_manifest_prompt,
    key_manifest_schema_copy,
    mediation_prompt,
    page_schema_copy,
)


@dataclass
class ProviderResult:
    """A provider response after best-effort JSON parsing."""

    provider: str
    model: str
    page_number: int
    parsed: dict[str, Any] | None
    raw_text: str | None
    error: str | None = None
    operation: str | None = None
    prompt: str | None = None
    duration_seconds: float | None = None
    usage: dict[str, Any] | None = None


def parse_json_text(raw_text: str | None) -> dict[str, Any]:
    """Parse provider text into JSON, accepting fences or surrounding prose."""

    if raw_text is None:
        raise ValueError("Provider returned no text.")

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Non-grammar-constrained providers can occasionally add a sentence
        # before or after the JSON. Decode the first complete JSON object so
        # mediation can still succeed without relaxing downstream validation.
        decoder = json.JSONDecoder()
        start = cleaned.find("{")
        if start == -1:
            raise
        parsed, _ = decoder.raw_decode(cleaned[start:])
        if not isinstance(parsed, dict):
            raise ValueError("Provider returned JSON, but not a JSON object.")
        return parsed


def extract_gemini_text_parts(response: Any) -> str:
    """Return Gemini text parts without calling warning-emitting SDK helpers."""

    text_parts: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if text:
                text_parts.append(text)

    if not text_parts:
        raise ValueError("Gemini returned no text parts.")
    return "\n".join(text_parts)


def claude_supports_temperature(model: str) -> bool:
    """Return whether the Claude model accepts explicit sampling parameters."""

    return not model.strip().startswith("claude-opus-4-7")


class OpenAIExtractor:
    """Baseline extractor that sends one-page PDFs to the OpenAI Responses API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4-mini",
        debug_monitor: ExtractionDebugMonitor | None = None,
    ) -> None:
        """Create the OpenAI client lazily so import errors stay provider-scoped."""

        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.debug_monitor = debug_monitor

    def extract_page(
        self,
        page_pdf_path: str | Path,
        page_number: int,
        profile: dict[str, Any],
        expected_keys: list[str] | None = None,
        context_page_numbers: list[int] | None = None,
    ) -> ProviderResult:
        """Upload a page or anchor/context PDF and request profile-shaped JSON."""

        operation = "baseline_extraction"
        prompt = extraction_prompt(
            page_number,
            profile,
            expected_keys=expected_keys,
            context_page_numbers=context_page_numbers,
        )
        call_id = (
            self.debug_monitor.start_model_call("openai", self.model, page_number, operation, prompt)
            if self.debug_monitor
            else None
        )
        started_at = time.perf_counter()
        response: Any | None = None
        usage: dict[str, Any] = {"available": False}
        raw_text: str | None = None
        try:
            with open(page_pdf_path, "rb") as page_file:
                uploaded = self.client.files.create(file=page_file, purpose="user_data")

            response = self.client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_file", "file_id": uploaded.id},
                            {
                                "type": "input_text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "page_extraction",
                        "strict": True,
                        "schema": page_schema_copy(profile),
                    }
                },
            )
            raw_text = response.output_text
            usage = extract_token_usage(response)
            parsed = parse_json_text(raw_text)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                )
            return ProviderResult(
                "openai",
                self.model,
                page_number,
                parsed,
                raw_text,
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )
        except Exception as exc:
            usage = extract_token_usage(response)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                    error=str(exc),
                )
            return ProviderResult(
                "openai",
                self.model,
                page_number,
                None,
                raw_text,
                str(exc),
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )

    def extract_key_manifest(
        self,
        page_pdf_path: str | Path,
        page_number: int,
        profile: dict[str, Any],
    ) -> ProviderResult:
        """Upload one anchor page and request only its record identity keys."""

        operation = "key_manifest"
        prompt = key_manifest_prompt(page_number, profile)
        call_id = (
            self.debug_monitor.start_model_call("openai", self.model, page_number, operation, prompt)
            if self.debug_monitor
            else None
        )
        started_at = time.perf_counter()
        response: Any | None = None
        usage: dict[str, Any] = {"available": False}
        raw_text: str | None = None
        try:
            with open(page_pdf_path, "rb") as page_file:
                uploaded = self.client.files.create(file=page_file, purpose="user_data")

            response = self.client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_file", "file_id": uploaded.id},
                            {"type": "input_text", "text": prompt},
                        ],
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "anchor_key_manifest",
                        "strict": True,
                        "schema": key_manifest_schema_copy(),
                    }
                },
            )
            raw_text = response.output_text
            usage = extract_token_usage(response)
            parsed = parse_json_text(raw_text)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                )
            return ProviderResult(
                "openai",
                self.model,
                page_number,
                parsed,
                raw_text,
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )
        except Exception as exc:
            usage = extract_token_usage(response)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                    error=str(exc),
                )
            return ProviderResult(
                "openai",
                self.model,
                page_number,
                None,
                raw_text,
                str(exc),
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )


class GeminiExtractor:
    """Baseline extractor that sends one-page PDFs to the Gemini API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-flash-preview",
        debug_monitor: ExtractionDebugMonitor | None = None,
    ) -> None:
        """Create the Gemini client with the selected extraction model."""

        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.debug_monitor = debug_monitor

    def extract_page(
        self,
        page_pdf_path: str | Path,
        page_number: int,
        profile: dict[str, Any],
        expected_keys: list[str] | None = None,
        context_page_numbers: list[int] | None = None,
    ) -> ProviderResult:
        """Upload a page or anchor/context PDF and request profile-shaped JSON."""

        operation = "baseline_extraction"
        prompt = extraction_prompt(
            page_number,
            profile,
            expected_keys=expected_keys,
            context_page_numbers=context_page_numbers,
        )
        call_id = (
            self.debug_monitor.start_model_call("gemini", self.model, page_number, operation, prompt)
            if self.debug_monitor
            else None
        )
        started_at = time.perf_counter()
        response: Any | None = None
        usage: dict[str, Any] = {"available": False}
        raw_text: str | None = None
        uploaded: Any | None = None
        try:
            uploaded = self.client.files.upload(
                file=page_pdf_path,
                config={"mime_type": "application/pdf"},
            )
            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    uploaded,
                    prompt,
                ],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": page_schema_copy(profile),
                },
            )
            raw_text = extract_gemini_text_parts(response)
            usage = extract_token_usage(response)
            parsed = parse_json_text(raw_text)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                )
            return ProviderResult(
                "gemini",
                self.model,
                page_number,
                parsed,
                raw_text,
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )
        except Exception as exc:
            usage = extract_token_usage(response)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                    error=str(exc),
                )
            return ProviderResult(
                "gemini",
                self.model,
                page_number,
                None,
                raw_text,
                str(exc),
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )
        finally:
            try:
                if uploaded is not None and getattr(uploaded, "name", None):
                    self.client.files.delete(name=uploaded.name)
            except Exception:
                pass

    def extract_key_manifest(
        self,
        page_pdf_path: str | Path,
        page_number: int,
        profile: dict[str, Any],
    ) -> ProviderResult:
        """Upload one anchor page and request only its record identity keys."""

        operation = "key_manifest"
        prompt = key_manifest_prompt(page_number, profile)
        call_id = (
            self.debug_monitor.start_model_call("gemini", self.model, page_number, operation, prompt)
            if self.debug_monitor
            else None
        )
        started_at = time.perf_counter()
        response: Any | None = None
        usage: dict[str, Any] = {"available": False}
        raw_text: str | None = None
        uploaded: Any | None = None
        try:
            uploaded = self.client.files.upload(
                file=page_pdf_path,
                config={"mime_type": "application/pdf"},
            )
            response = self.client.models.generate_content(
                model=self.model,
                contents=[uploaded, prompt],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": key_manifest_schema_copy(),
                },
            )
            raw_text = extract_gemini_text_parts(response)
            usage = extract_token_usage(response)
            parsed = parse_json_text(raw_text)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                )
            return ProviderResult(
                "gemini",
                self.model,
                page_number,
                parsed,
                raw_text,
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )
        except Exception as exc:
            usage = extract_token_usage(response)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                    error=str(exc),
                )
            return ProviderResult(
                "gemini",
                self.model,
                page_number,
                None,
                raw_text,
                str(exc),
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )
        finally:
            try:
                if uploaded is not None and getattr(uploaded, "name", None):
                    self.client.files.delete(name=uploaded.name)
            except Exception:
                pass


class ClaudeMediator:
    """Mediator that resolves baseline disagreements with a source PDF page."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-4-6",
        debug_monitor: ExtractionDebugMonitor | None = None,
    ) -> None:
        """Create the Anthropic client for structured JSON mediation."""

        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.debug_monitor = debug_monitor

    def mediate_page(
        self,
        page_pdf_path: str | Path,
        page_number: int,
        profile: dict[str, Any],
        openai_output: dict[str, Any] | None,
        gemini_output: dict[str, Any] | None,
        diff: dict[str, Any],
        expected_keys: list[str] | None = None,
        context_page_numbers: list[int] | None = None,
    ) -> ProviderResult:
        """Ask Claude to return corrected page JSON using the PDF as source truth."""

        operation = "mediation"
        prompt = mediation_prompt(
            page_number=page_number,
            profile=profile,
            openai_output=openai_output,
            gemini_output=gemini_output,
            diff=diff,
            expected_keys=expected_keys,
            context_page_numbers=context_page_numbers,
        )
        call_id = (
            self.debug_monitor.start_model_call("claude", self.model, page_number, operation, prompt)
            if self.debug_monitor
            else None
        )
        started_at = time.perf_counter()
        response: Any | None = None
        usage: dict[str, Any] = {"available": False}
        raw_text: str | None = None
        try:
            encoded_pdf = base64.b64encode(Path(page_pdf_path).read_bytes()).decode("ascii")
            request: dict[str, Any] = {
                "model": self.model,
                "max_tokens": 6000,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": encoded_pdf,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": page_schema_copy(profile),
                    }
                },
            }
            if claude_supports_temperature(self.model):
                request["temperature"] = 0

            response = self.client.messages.create(**request)
            raw_text = "\n".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )
            usage = extract_token_usage(response)
            parsed = parse_json_text(raw_text)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                )
            return ProviderResult(
                "claude",
                self.model,
                page_number,
                parsed,
                raw_text,
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )
        except Exception as exc:
            usage = extract_token_usage(response)
            duration = time.perf_counter() - started_at
            if self.debug_monitor and call_id is not None:
                usage = self.debug_monitor.finish_model_call(
                    call_id,
                    response=response,
                    usage=usage,
                    raw_text=raw_text,
                    error=str(exc),
                )
            return ProviderResult(
                "claude",
                self.model,
                page_number,
                None,
                raw_text,
                str(exc),
                operation=operation,
                prompt=prompt,
                duration_seconds=duration,
                usage=usage,
            )
