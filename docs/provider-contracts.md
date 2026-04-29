# Provider Contracts

Generated from the current source tree on 2026-04-19.

## Shared Result Object

All provider adapters return `ProviderResult`:

```python
ProviderResult(
    provider="openai",
    model="gpt-5.4-mini",
    page_number=1,
    parsed={},
    raw_text="{}",
    error=None,
    operation="baseline_extraction",
    prompt="Extract all visible ...",
    duration_seconds=4.2,
    usage={"available": True, "input_tokens": 1000, "output_tokens": 200},
)
```

Fields:

- `provider`: provider identifier.
- `model`: model name used for the call.
- `page_number`: 1-based PDF page number.
- `parsed`: parsed JSON object, or `None`.
- `raw_text`: raw provider text, or `None`.
- `error`: exception message, or `None`.
- `operation`: `key_manifest`, `baseline_extraction`, or `mediation`.
- `prompt`: exact instruction text sent with the source PDF. This is retained
  internally but is not included in the debug JSON export.
- `duration_seconds`: elapsed adapter time for upload, model call, and parsing.
- `usage`: normalized provider token metadata when available.

Adapters catch exceptions and return an error-bearing `ProviderResult` instead
of raising to the pipeline.

When the debug monitor is attached, providers also record timing, status, and
usage information as model-call traces. Usage normalization supports the common
fields returned by OpenAI, Gemini, and Anthropic SDK responses. A call is marked
with `{"available": false}` when the SDK response or error does not expose token
usage.

## JSON Parsing

`parse_json_text` accepts:

- plain JSON object text,
- fenced JSON blocks,
- provider text that contains prose before a JSON object.

If direct `json.loads` fails, it scans for the first `{` and uses
`json.JSONDecoder().raw_decode` to parse the first complete JSON object. The
parsed value must be a JSON object.

## OpenAI Baseline

Class: `OpenAIExtractor`

Default model: `gpt-5.4-mini`

Behavior:

1. Creates an `OpenAI` client with the configured API key.
2. Uploads the one-page PDF or anchor/context PDF with purpose `user_data`.
3. Calls `client.responses.create`.
4. Sends two content items:
   - `input_file` with the uploaded file id,
   - `input_text` with the extraction prompt.
5. Requests strict JSON Schema output using `text.format`.
6. Parses `response.output_text`.

The OpenAI baseline is one of the two providers required for automatic
agreement. When page context is enabled, `extract_key_manifest` first uploads
the anchor page alone and requests `{ "page_number": N, "record_keys": [...] }`.

## Gemini Baseline

Class: `GeminiExtractor`

Default model: `gemini-3-flash-preview`

Behavior:

1. Creates a `google.genai.Client` with the configured API key.
2. Uploads the one-page PDF or anchor/context PDF with MIME type
   `application/pdf`.
3. Calls `client.models.generate_content`.
4. Sends the uploaded file and extraction prompt.
5. Requests JSON output with `response_mime_type` and `response_json_schema`.
6. Extracts text from response candidates and parts without using SDK helpers
   that may emit warnings.
7. Deletes the uploaded file in a `finally` block when possible.

The Gemini baseline is the second required provider for automatic agreement.
When page context is enabled, `extract_key_manifest` first uploads the anchor
page alone and requests `{ "page_number": N, "record_keys": [...] }`.

## Claude Mediator

Class: `ClaudeMediator`

Default model: `claude-opus-4-6`

Behavior:

1. Creates an `anthropic.Anthropic` client with the configured API key.
2. Base64-encodes the one-page PDF or anchor/context PDF.
3. Calls `client.messages.create`.
4. Sends:
   - the source PDF page as a document block,
   - a mediation prompt containing the profile, baseline outputs, and diff.
5. Requests JSON Schema output through `output_config`.
6. Joins text blocks from the response and parses JSON.

Claude is optional. It is only called when OpenAI and Gemini do not agree and an
Anthropic API key is configured.

For `claude-opus-4-7`, the adapter omits explicit sampling parameters such as
`temperature`. Opus 4.7 rejects non-default sampling parameters, while older
configured Claude model names keep the app's historical `temperature=0`
setting.

## Provider Concurrency

For each page, the pipeline runs OpenAI and Gemini baseline calls concurrently.
When a context-enabled profile needs anchor key manifests, those OpenAI and
Gemini manifest calls are also run concurrently. Claude mediation remains
conditional and is only started after the baseline comparison proves a
disagreement.

## Provider Inputs

Each extraction provider receives:

- a one-page PDF path or anchor/context PDF path,
- the anchor page number,
- the selected extraction profile,
- optional expected anchor keys,
- optional context page numbers.

Each key-manifest provider call receives:

- a one-page anchor PDF path,
- the anchor page number,
- the selected extraction profile.

The mediator receives:

- a one-page PDF path or anchor/context PDF path,
- the anchor page number,
- the selected extraction profile,
- parsed OpenAI output,
- parsed Gemini output,
- deterministic diff,
- optional expected anchor keys,
- optional context page numbers.

## Provider Output Expectations

Providers are prompted and schema-constrained to return:

- a top-level `page_number`,
- the configured top-level records array,
- exactly one object per visible record identity in one-page mode,
- exactly one object per expected anchor-page identity in context mode,
- every configured field for each record,
- `null` for missing or unreadable non-key values,
- no record when the identity key is missing or unreadable,
- no commentary, Markdown, confidence scores, or placeholders.

In context mode, providers are told that attached PDF page 1 is the anchor page
and attached PDF page 2 is context only. Context-page records must not be
accepted as clean records for the anchor page.

The app still validates provider output locally. Provider schemas improve output
quality, but local validation decides whether the result is trusted.

## Error Handling

Provider errors do not stop the entire pipeline immediately. Instead:

- the adapter returns `parsed=None` and `error=<message>`,
- canonicalization records `provider returned no parseable JSON`,
- the page diff cannot be agreed,
- Claude may still attempt mediation when configured,
- otherwise the page becomes `needs_review`.

This design keeps debug output available even when one provider fails.
