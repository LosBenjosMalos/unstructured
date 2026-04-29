"""Configurable validation and comparison for extraction outputs.

This module is intentionally generic. It does not know about FM codes, UIDs, or
any document-specific field. It only applies the normalization and validation
rules configured in the selected extraction profile, then compares records by
the configured identity key.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from schema import build_page_schema


DASH_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\ufe58": "-",
        "\ufe63": "-",
        "\uff0d": "-",
    }
)


@dataclass
class CanonicalPage:
    """A provider page output after profile-driven canonicalization."""

    page_number: int
    provider: str
    raw: dict[str, Any] | None
    records_by_key: dict[str, dict[str, Any]] = field(default_factory=dict)
    key_display: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def stable_key_id(value: Any) -> str:
    """Create the record identity key used for cross-model comparison.

    Model providers may emit a printed key as either `2` or `"2"`. For document
    extraction those should identify the same record, so keys are compared as
    stripped strings rather than type-aware JSON values.
    """

    return str(value).strip()


def normalize_key_value(value: Any, profile: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    """Normalize one record identity value using the selected key field rules."""

    if value is None:
        return None, None, ["key value is null"]

    key_field = profile.get("key_field")
    key_config = (profile.get("fields") or {}).get(key_field, {})
    normalized_value, errors = apply_field_rules(value, key_config if isinstance(key_config, dict) else {})
    if normalized_value is None:
        fallback = stable_key_id(value)
        return fallback, fallback, errors

    key_id = stable_key_id(normalized_value)
    return key_id, str(normalized_value), errors


def key_sort_key(key_id: str) -> str:
    """Sort generic record keys lexically without document-specific assumptions."""

    return key_id


def stringify_model_value(value: Any) -> Any:
    """Convert every non-null scalar model value to string before normalization."""

    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, list):
        return [stringify_model_value(item) for item in value]
    return str(value)


def apply_normalizer(value: Any, normalizer: str) -> Any:
    """Apply one user-configured normalization step to a value."""

    if normalizer == "trim":
        return str(value).strip()
    if normalizer == "collapse_whitespace":
        return re.sub(r"\s+", " ", str(value)).strip()
    if normalizer == "normalize_dashes":
        return str(value).translate(DASH_TRANSLATION)
    if normalizer == "normalize_unicode":
        return unicodedata.normalize("NFKC", str(value))
    if normalizer == "remove_all_whitespace":
        return re.sub(r"\s+", "", str(value))
    if normalizer == "remove_soft_hyphen":
        return str(value).replace("\u00ad", "")
    if normalizer == "repair_hyphenated_line_breaks":
        return re.sub(r"-\s*\n\s*", "", str(value))
    if normalizer == "lower":
        return str(value).lower()
    if normalizer == "upper":
        return str(value).upper()
    if normalizer == "to_int":
        return int(str(value).strip())
    if normalizer == "to_float":
        return float(str(value).strip())
    if normalizer == "to_bool":
        text = str(value).strip().lower()
        if text in {"true", "yes", "y", "1"}:
            return True
        if text in {"false", "no", "n", "0"}:
            return False
        raise ValueError(f"cannot parse {value!r} as boolean")
    if normalizer == "yes_no":
        text = str(value).strip().lower()
        if text in {"yes", "y", "true", "1"}:
            return "yes"
        if text in {"no", "n", "false", "0"}:
            return "no"
        return value
    return value


def apply_field_rules(value: Any, field_config: dict[str, Any]) -> tuple[Any, list[str]]:
    """Normalize and validate a field using only the selected profile rules."""

    errors: list[str] = []
    if value is None:
        return None, errors

    current = stringify_model_value(value)
    for normalizer in field_config.get("normalizers", []) or []:
        try:
            current = apply_normalizer(current, normalizer)
        except (TypeError, ValueError) as exc:
            errors.append(f"normalizer {normalizer!r} failed for value {value!r}: {exc}")
            return current, errors

    expected_type = field_config.get("type", "string")
    if expected_type == "integer" and (isinstance(current, bool) or not isinstance(current, int)):
        errors.append(f"value {current!r} is not an integer")
    elif expected_type == "number" and (isinstance(current, bool) or not isinstance(current, int | float)):
        errors.append(f"value {current!r} is not a number")
    elif expected_type == "boolean" and not isinstance(current, bool):
        errors.append(f"value {current!r} is not a boolean")
    elif expected_type == "string" and not isinstance(current, str):
        errors.append(f"value {current!r} is not a string")
    elif expected_type == "array" and not isinstance(current, list):
        errors.append(f"value {current!r} is not an array")
    elif expected_type == "object" and not isinstance(current, dict):
        errors.append(f"value {current!r} is not an object")

    return current, errors


def normalize_record(
    record: dict[str, Any],
    fields: dict[str, Any],
    path_prefix: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """Normalize one record recursively according to configured field rules."""

    normalized: dict[str, Any] = {}
    errors: list[str] = []

    for field_name, field_config in fields.items():
        if field_name not in record:
            continue

        path = f"{path_prefix}.{field_name}" if path_prefix else field_name
        value = stringify_model_value(record[field_name])
        if value is None:
            normalized[field_name] = None
            continue
        if field_config.get("type") == "object":
            if not isinstance(value, dict):
                errors.append(f"{path} is not an object")
                continue
            child_value, child_errors = normalize_record(
                value,
                field_config.get("fields", {}),
                path_prefix=path,
            )
            if child_value:
                normalized[field_name] = child_value
            errors.extend(child_errors)
            continue

        normalized_value, field_errors = apply_field_rules(value, field_config)
        normalized[field_name] = normalized_value
        errors.extend(f"{path}: {error}" for error in field_errors)

    return normalized, errors


def flatten_record(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested records into dotted paths for field-by-field comparison."""

    flattened: dict[str, Any] = {}
    for key, value in record.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(flatten_record(value, path))
        else:
            flattened[path] = value
    return flattened


def validate_schema(raw: dict[str, Any] | None, profile: dict[str, Any]) -> list[str]:
    """Validate a raw provider response against the selected profile schema."""

    if raw is None:
        return ["provider returned no parseable JSON"]

    validator = Draft202012Validator(build_page_schema(profile))
    return [error.message for error in sorted(validator.iter_errors(raw), key=str)]


def canonicalize_page_output(
    raw: dict[str, Any] | None,
    provider: str,
    fallback_page_number: int,
    profile: dict[str, Any],
) -> CanonicalPage:
    """Convert provider JSON into records keyed by the configured identity field."""

    page = CanonicalPage(
        page_number=raw.get("page_number", fallback_page_number) if isinstance(raw, dict) else fallback_page_number,
        provider=provider,
        raw=raw,
    )
    page.errors.extend(validate_schema(raw, profile))
    if raw is None:
        return page

    records_key = profile.get("records_key", "records")
    key_field = profile.get("key_field")
    fields = profile.get("fields", {})

    for index, record in enumerate(raw.get(records_key) or []):
        if not isinstance(record, dict):
            page.errors.append(f"record {index} is not an object")
            continue
        if key_field not in record or record.get(key_field) is None:
            page.errors.append(f"record {index} is missing key field {key_field!r}")
            continue

        normalized, rule_errors = normalize_record(record, fields)
        page.errors.extend(f"record {index}: {error}" for error in rule_errors)
        if key_field not in normalized or normalized.get(key_field) is None:
            page.errors.append(f"record {index} key field {key_field!r} could not be normalized")
            continue

        key_value = normalized[key_field]
        key_id = stable_key_id(key_value)
        page.order.append(key_id)
        page.key_display[key_id] = str(key_value)

        if key_id in page.records_by_key:
            page.errors.append(f"duplicate record for key {key_value!r}")
            continue

        page.records_by_key[key_id] = normalized

    return page


def compare_records(
    key_id: str,
    key_display: str,
    openai_record: dict[str, Any] | None,
    gemini_record: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Compare one keyed record from both baselines."""

    if openai_record is None or gemini_record is None:
        return [
            {
                "key": key_display,
                "key_id": key_id,
                "field": "__record__",
                "openai": openai_record,
                "gemini": gemini_record,
            }
        ]

    openai_flat = flatten_record(openai_record)
    gemini_flat = flatten_record(gemini_record)
    paths = sorted(set(openai_flat) | set(gemini_flat))

    mismatches: list[dict[str, Any]] = []
    for path in paths:
        openai_has = path in openai_flat
        gemini_has = path in gemini_flat
        if not openai_has or not gemini_has or openai_flat[path] != gemini_flat[path]:
            mismatches.append(
                {
                    "key": key_display,
                    "key_id": key_id,
                    "field": path,
                    "openai": openai_flat.get(path) if openai_has else "<missing>",
                    "gemini": gemini_flat.get(path) if gemini_has else "<missing>",
                }
            )

    return mismatches


def compare_pages(
    openai_page: CanonicalPage,
    gemini_page: CanonicalPage,
    expected_key_ids: list[str] | None = None,
    expected_key_display: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compare two canonical page outputs by configured record key."""

    openai_keys = set(openai_page.records_by_key)
    gemini_keys = set(gemini_page.records_by_key)
    manifest_expected = expected_key_ids is not None
    if expected_key_ids is None:
        expected_key_ids = sorted(openai_keys | gemini_keys, key=key_sort_key)
    else:
        expected_key_ids = sorted(set(expected_key_ids), key=key_sort_key)
    expected_keys = set(expected_key_ids)
    display_by_key = {**gemini_page.key_display, **openai_page.key_display, **(expected_key_display or {})}

    missing_openai_ids = sorted(expected_keys - openai_keys, key=key_sort_key)
    missing_gemini_ids = sorted(expected_keys - gemini_keys, key=key_sort_key)
    unexpected_openai_ids = sorted(openai_keys - expected_keys, key=key_sort_key) if manifest_expected else []
    unexpected_gemini_ids = sorted(gemini_keys - expected_keys, key=key_sort_key) if manifest_expected else []

    field_mismatches: list[dict[str, Any]] = []
    for key_id in expected_key_ids:
        field_mismatches.extend(
            compare_records(
                key_id,
                display_by_key.get(key_id, key_id),
                openai_page.records_by_key.get(key_id),
                gemini_page.records_by_key.get(key_id),
            )
        )

    order_warning = openai_page.order != gemini_page.order and set(openai_page.order) == set(gemini_page.order)
    disputed_key_ids = sorted({item["key_id"] for item in field_mismatches}, key=key_sort_key)

    return {
        "expected_keys": [display_by_key.get(key_id, key_id) for key_id in expected_key_ids],
        "expected_key_ids": expected_key_ids,
        "agreed": not openai_page.errors
        and not gemini_page.errors
        and not missing_openai_ids
        and not missing_gemini_ids
        and not unexpected_openai_ids
        and not unexpected_gemini_ids
        and not field_mismatches,
        "missing_openai": [display_by_key.get(key_id, key_id) for key_id in missing_openai_ids],
        "missing_gemini": [display_by_key.get(key_id, key_id) for key_id in missing_gemini_ids],
        "unexpected_openai": [display_by_key.get(key_id, key_id) for key_id in unexpected_openai_ids],
        "unexpected_gemini": [display_by_key.get(key_id, key_id) for key_id in unexpected_gemini_ids],
        "unexpected_openai_ids": unexpected_openai_ids,
        "unexpected_gemini_ids": unexpected_gemini_ids,
        "field_mismatches": field_mismatches,
        "disputed_key_ids": disputed_key_ids,
        "disputed_keys": [display_by_key.get(key_id, key_id) for key_id in disputed_key_ids],
        "order_warning": order_warning,
        "openai_errors": openai_page.errors,
        "gemini_errors": gemini_page.errors,
        "openai_warnings": openai_page.warnings,
        "gemini_warnings": gemini_page.warnings,
    }


def mark_record_source(record: dict[str, Any], source: str) -> dict[str, Any]:
    """Copy a record and attach final-resolution metadata."""

    copied = json.loads(json.dumps(record, ensure_ascii=False))
    copied["resolution_source"] = source
    return copied
