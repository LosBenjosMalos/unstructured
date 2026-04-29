# Module Reference

Generated from the current source tree on 2026-04-19.

## `main.py`

Streamlit UI for the application.

### Functions

- `env_status(name)`: reports whether an API key environment variable is set
  without exposing the value.
- `save_uploaded_pdf(uploaded_file)`: writes a Streamlit upload to a temporary
  PDF file and returns the path.
- `parse_profile_text(profile_text)`: parses profile JSON from the editor and
  validates it.
- `render_profile_editor()`: renders the profile configuration tab, including
  validation, preview, provider schema, and save action.
- `render_page_summary(page)`: renders page status, context pages, key manifest,
  diff, provider outputs, canonical outputs, and final records.
- `render_extraction_page()`: renders the extraction tab, validates API keys,
  runs the pipeline, renders the final run report, and displays download
  buttons.
- `render_sidebar()`: renders API key status, model names, and max-page limit.
- `main()`: configures Streamlit and renders the app tabs.

### Key Dependencies

- `config_store.list_profiles`, `load_profile`, `save_profile`
- `pipeline.PipelineConfig`, `process_document`, `to_pretty_json`
- `debug_monitor.ExtractionDebugMonitor`, `render_run_report`
- `schema.build_page_schema`, `output_preview`, `validate_profile`
- `streamlit`

## `debug_monitor.py`

Extraction timing and usage reporting.

### Classes

- `ModelCallTrace`: dataclass for one provider request, including provider,
  model, page, operation, status, elapsed time, usage, and error.
- `ExtractionDebugMonitor`: thread-safe collector for provider call traces and
  timing reports.

### Functions

- `extract_token_usage(response)`: normalizes usage metadata from OpenAI,
  Gemini, and Anthropic SDK response objects.
- `public_object_dict(value)`: converts SDK usage objects into primitive
  dictionaries.
- `first_numeric(data, keys)`: returns the first numeric token field from a
  usage dictionary.
- `build_timing_report(calls, run_started_at, run_finished_at)`: aggregates
  provider call counts, elapsed time, and token totals.
- `render_run_report(snapshot_or_report)`: renders provider timing and token
  totals after extraction completes.

### Key Dependencies

- `streamlit` is imported lazily inside render functions only.

## `pipeline.py`

End-to-end orchestration for document processing.

### Classes

- `PipelineConfig`: dataclass containing API keys, selected profile, model
  names, and max-page limit.

### Functions

- `provider_debug(result)`: converts a provider result into JSON-serializable
  debug data.
- `run_provider_tasks(...)`: runs provider calls concurrently while periodically
  refreshing progress callbacks.
- `canonical_debug(page)`: exposes canonical page details for debug output.
- `normalize_manifest_keys(parsed, profile)`: normalizes a provider key-manifest
  response using the selected key-field rules.
- `build_key_manifest(...)`: asks both baseline providers for anchor-page keys
  and merges their manifests.
- `empty_manual_review_record(key_display, profile, page_number)`: creates a
  minimal manual-review record.
- `best_manual_review_record(...)`: chooses the best available baseline record
  for unresolved review.
- `merge_page_records(...)`: merges agreed baseline records, mediated records,
  and manual-review fallbacks.
- `process_page(...)`: optionally builds a key manifest, runs both baselines,
  compares them, optionally mediates, and returns one anchor-page result.
- `find_duplicate_keys(records, key_field)`: finds record keys appearing on more
  than one source page.
- `build_document_export(document_id, profile, page_results)`: builds final
  document-level JSON.
- `process_document(pdf_path, document_id, config, progress_callback=None,
  debug_monitor=None)`: splits the PDF, processes pages, builds final and debug
  exports, and cleans temporary files.
- `to_pretty_json(data)`: serializes data with indentation and Unicode support.

### Key Dependencies

- `compare.CanonicalPage`, `canonicalize_page_output`, `compare_pages`,
  `key_sort_key`, `mark_record_source`, `normalize_key_value`
- `pdf_pages.split_pdf_into_pages`
- `providers.OpenAIExtractor`, `GeminiExtractor`, `ClaudeMediator`

## `providers.py`

Provider API adapters and JSON response parsing.

### Classes

- `ProviderResult`: dataclass for provider name, model, page number, parsed JSON,
  raw text, error, operation, prompt, duration, and usage metadata. The prompt is
  kept internal and is not exported in debug JSON.
- `OpenAIExtractor`: baseline extractor using the OpenAI Responses API.
- `GeminiExtractor`: baseline extractor using the Gemini API.
- `ClaudeMediator`: mediator using Claude with source PDF plus baseline outputs.

### Functions

- `parse_json_text(raw_text)`: parses provider text into a JSON object,
  accepting fenced JSON and surrounding prose.
- `extract_gemini_text_parts(response)`: extracts candidate text parts from a
  Gemini response.
- `claude_supports_temperature(model)`: returns whether the configured Claude
  model can receive an explicit temperature parameter.

### Key Dependencies

- `schema.extraction_prompt`
- `schema.key_manifest_prompt`
- `schema.key_manifest_schema_copy`
- `schema.mediation_prompt`
- `schema.page_schema_copy`
- `openai`
- `google-genai`
- `anthropic`

## `compare.py`

Profile-driven validation, normalization, canonicalization, and comparison.

### Classes

- `CanonicalPage`: dataclass representing one provider output after
  canonicalization.

### Functions

- `stable_key_id(value)`: converts key values into stripped strings for
  cross-provider identity matching.
- `normalize_key_value(value, profile)`: normalizes one key value using the
  selected profile's key-field rules.
- `key_sort_key(key_id)`: returns the lexical sort key for record identities.
- `stringify_model_value(value)`: converts non-null scalar values to strings.
- `apply_normalizer(value, normalizer)`: applies one configured normalizer.
- `apply_field_rules(value, field_config)`: applies normalizers and type checks
  for a field.
- `normalize_record(record, fields, path_prefix="")`: recursively normalizes a
  record.
- `flatten_record(record, prefix="")`: flattens nested objects into dotted paths.
- `validate_schema(raw, profile)`: validates raw provider JSON against the
  generated schema.
- `canonicalize_page_output(raw, provider, fallback_page_number, profile)`:
  validates, normalizes, and indexes records by key.
- `compare_records(key_id, key_display, openai_record, gemini_record)`:
  compares one keyed record field-by-field.
- `compare_pages(...)`: compares two canonical page outputs, optionally against
  a provided anchor key manifest.
- `mark_record_source(record, source)`: deep-copies a record and adds
  `resolution_source`.

### Key Dependencies

- `jsonschema.Draft202012Validator`
- `schema.build_page_schema`

## `schema.py`

Dynamic extraction profile, JSON Schema, preview, and prompt helpers.

### Constants

- `SUPPORTED_TYPES`: allowed profile field types.
- `SUPPORTED_NORMALIZERS`: allowed normalizer names.
- `MODEL_SCALAR_TYPES`: provider schema scalar union.
- `MAX_FOLLOWING_CONTEXT_PAGES`: current maximum following-page context depth.
- `DEFAULT_PROFILE`: built-in fallback extraction profile.
- `PROMPT_INJECTION_WARNING`: shared safety text included in prompts.

### Functions

- `profile_copy(profile=None)`: returns a defensive profile copy.
- `validate_profile(profile)`: validates app-level profile format.
- `following_context_pages(profile)`: returns the enabled following context page
  count, capped to the supported range.
- `json_type_for_field(config)`: returns a supported JSON Schema type.
- `build_field_schema(config)`: converts one profile field to JSON Schema.
- `build_record_schema(profile)`: builds schema for a single extracted record.
- `build_page_schema(profile)`: builds top-level page extraction schema.
- `page_schema_copy(profile)`: returns a fresh page schema.
- `build_key_manifest_schema()`: builds the provider schema for anchor key
  manifests.
- `key_manifest_schema_copy()`: returns a fresh key-manifest schema.
- `output_preview(profile)`: builds a sample output object for the UI.
- `profile_summary(profile)`: renders field summary text for prompts.
- `key_manifest_prompt(page_number, profile)`: builds the anchor key prompt.
- `extraction_prompt(...)`: builds one-page or anchor/context extraction prompts.
- `mediation_prompt(...)`: builds mediation prompt containing baseline outputs
  and diff.

## `config_store.py`

JSON-file store for extraction profiles.

### Constants

- `PROFILE_DIR`: path to `extraction_profiles/`.

### Functions

- `slugify(name)`: converts a profile name into a safe filename stem.
- `ensure_profile_dir()`: creates the profile directory.
- `profile_path(name)`: returns the path for a profile JSON file.
- `list_profiles()`: lists valid saved profile names plus the built-in default.
- `load_profile(name)`: loads a saved profile or returns the default profile.
- `save_profile(profile)`: validates and writes a profile JSON file.

## `pdf_pages.py`

PDF page preparation helpers backed by the `qpdf` CLI.

### Classes

- `PagePDF`: immutable dataclass containing source page number and temporary PDF
  path.
- `PageWindow`: immutable dataclass containing anchor page number, anchor page
  path, full extraction PDF path, and context page numbers.
- `PreparedPDF`: dataclass containing source path, temp directory, page list, and
  window list. It has `cleanup()` to remove temporary files.

### Functions

- `qpdf_available()`: checks whether `qpdf` is on `PATH`.
- `get_page_count(pdf_path)`: returns page count using `qpdf --show-npages`.
- `split_pdf_into_pages(pdf_path, max_pages=None, following_context_pages=0)`:
  creates one-page temporary PDFs plus anchor extraction windows and returns a
  `PreparedPDF`.

## `extraction_profiles/`

Saved extraction profile JSON files.

- `Model_WT-500.json`: label-preserving WT-500 profile.
- `wt500_supervision_entries_normalized.json`: normalized-field WT-500 profile.
