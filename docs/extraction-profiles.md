# Extraction Profiles

Generated from the current source tree on 2026-04-19.

## Role

An extraction profile is the contract between the UI, prompts, provider schemas,
canonicalization, comparison, and final export. The current app is generic
because it reads this contract at runtime instead of hard-coding document fields.

Profiles are JSON files stored in `extraction_profiles/`. The built-in fallback
profile is defined as `DEFAULT_PROFILE` in `schema.py`.

## Profile Shape

```json
{
  "name": "profile_name",
  "description": "Human-readable description.",
  "record_label": "entry",
  "records_key": "records",
  "key_field": "no",
  "page_context": {
    "following_pages": 1
  },
  "fields": {
    "no": {
      "type": "string",
      "description": "Printed record number.",
      "normalizers": ["trim", "collapse_whitespace"]
    }
  }
}
```

### Required Top-Level Fields

- `name`: non-empty string, used in the UI and for saved profile selection.
- `records_key`: non-empty string, defaults to `records` when omitted in some
  helper paths.
- `key_field`: non-empty string that must exist in `fields`.
- `fields`: non-empty object defining every record field.

### Optional Top-Level Fields

- `description`: shown in profile files but not required by validation.
- `record_label`: used in prompts, defaults to `record`.
- `page_context`: enables following-page context. V1 supports
  `{ "following_pages": 1 }`; omit it or set `0` for one-page extraction.

## Supported Field Types

`schema.SUPPORTED_TYPES` currently allows:

- `string`
- `integer`
- `number`
- `boolean`
- `object`
- `array`

Provider-facing JSON Schema is intentionally permissive for scalar fields:
strings, numbers, booleans, and null are accepted. The app then applies
profile-level normalization and type checks during canonicalization.

Object fields can define nested `fields`. Array fields are represented as arrays
of strings in the generated provider schema.

## Supported Normalizers

Normalizers are applied in the order listed on each field.

- `trim`: strip leading and trailing whitespace.
- `collapse_whitespace`: replace whitespace runs with a single space.
- `normalize_dashes`: map common Unicode dash characters to `-`.
- `normalize_unicode`: apply Unicode NFKC normalization.
- `remove_all_whitespace`: remove all whitespace.
- `remove_soft_hyphen`: remove soft hyphen characters.
- `repair_hyphenated_line_breaks`: remove hyphenation across line breaks.
- `lower`: lowercase string values.
- `upper`: uppercase string values.
- `to_int`: convert to Python `int`.
- `to_float`: convert to Python `float`.
- `to_bool`: convert true/false-like text to Python `bool`.
- `yes_no`: normalize true-like values to `yes` and false-like values to `no`.

No normalizers are applied unless the selected profile explicitly includes them.

## Validation Rules

`validate_profile` checks:

- profile is a JSON object,
- `profile.name` is a non-empty string,
- `profile.records_key` is a non-empty string,
- `profile.key_field` is a non-empty string,
- `profile.fields` is a non-empty object,
- key field exists in fields,
- each field config is an object,
- each field type is supported,
- normalizers are a list when present,
- all listed normalizers are supported,
- nested object fields have valid nested field configs,
- `page_context.following_pages`, when present, is an integer from `0` to `1`.

The profile editor in Streamlit parses the JSON text and displays validation
errors before allowing save.

## Saved Profiles

Current saved profiles:

### `Model WT-500`

File: `extraction_profiles/Model_WT-500.json`

This profile preserves labels close to the source document:

- key field: `No`
- records key: `records`
- page context: one following page
- fields include `SupervisionID`, `Name`, `Log`, `Subsystem name`, `Type`,
  `TimeOut`, `Max Time`, `Max Time Eliminate`, `Category`,
  `Acknowledgement`, and `Criteria`.

### `wt500_supervision_entries_normalized`

File: `extraction_profiles/wt500_supervision_entries_normalized.json`

This profile uses normalized snake_case-style field names:

- key field: `no`
- records key: `records`
- page context: one following page
- fields include `supervision_id`, `name`, `log_text`, `subsystem_name`,
  `entry_type`, `timeout`, `max_time`, `eliminate_time`, `category`,
  `acknowledgement`, `criteria_text`, and `parameter_reference`.

## Persistence Behavior

`config_store.py` owns profile persistence:

- profiles are stored in `extraction_profiles/`,
- `slugify` converts profile names into safe JSON filenames,
- `list_profiles` loads valid saved profile names and adds the built-in default
  profile if it is not already present,
- `load_profile` returns the built-in default when the requested profile is not
  found,
- `save_profile` validates the profile before writing JSON.

## How Profiles Drive Providers

For every selected profile, `schema.py` builds a page-level JSON Schema:

```json
{
  "type": "object",
  "properties": {
    "page_number": { "type": "integer" },
    "records": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": false
      }
    }
  },
  "required": ["page_number", "records"],
  "additionalProperties": false
}
```

The real `properties` and `required` lists are filled from the profile fields.
The same schema is sent to provider APIs and used locally for validation.

Profiles with `page_context.following_pages` enabled also use a key-manifest
schema before full extraction:

```json
{
  "type": "object",
  "properties": {
    "page_number": { "type": "integer" },
    "record_keys": {
      "type": "array",
      "items": { "type": "string" }
    }
  },
  "required": ["page_number", "record_keys"],
  "additionalProperties": false
}
```

OpenAI and Gemini produce this manifest from the anchor page only. Full
extraction receives the anchor page plus the following page as context and is
prompted to return only the manifest keys.

## Prompt Safety

Both extraction and mediation prompts include a prompt-injection warning. The
models are instructed to treat visible PDF text as untrusted source content and
to ignore instructions inside the PDF.

## Editing Guidance

When creating or editing a profile:

- keep `key_field` stable and unique per record,
- include all fields that should appear in final JSON,
- use descriptions to guide the model when source labels are ambiguous,
- add normalizers only when they are needed for comparison or final quality,
- prefer string fields for source text that may contain units, punctuation, or
  mixed formatting,
- use `page_context.following_pages: 1` only for profiles where records may
  continue onto the next page,
- use `max_pages` in the UI while testing paid model calls.
