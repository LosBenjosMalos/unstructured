# Operations

Generated from the current source tree on 2026-04-29.

## Runtime Requirements

The current repository does not include `requirements.txt`, `pyproject.toml`, or
another dependency manifest. Based on imports, the app needs:

- Python 3.10 or newer, because the code uses `int | float` union syntax.
- `streamlit`
- `jsonschema`
- `openai`
- `google-genai`
- `anthropic`
- `qpdf` command line tool

Install Python packages:

```bash
python -m venv .venv
source .venv/bin/activate
pip install streamlit jsonschema openai google-genai anthropic
```

Install qpdf on macOS:

```bash
brew install qpdf
```

## Environment Variables

Required for baseline extraction:

```bash
export OPENAI_API_KEY="..."
export GEMINI_API_KEY="..."
```

Optional for mediation:

```bash
export ANTHROPIC_API_KEY="..."
```

When `ANTHROPIC_API_KEY` is missing, the app can still run baseline extraction,
but mismatches are marked for manual review instead of being mediated by Claude.

## Running the App

From the repository root:

```bash
streamlit run main.py
```

The app opens a Streamlit UI with:

- an Extraction tab,
- a Profile configuration tab,
- a sidebar for model names and max-page limit,
- progress status text and a final run report during extraction,
- a Stop extraction button for the current run,
- a durable local run folder for each extraction.

## Model Defaults

Current defaults:

- OpenAI baseline: `gpt-5.4-mini`
- Gemini baseline: `gemini-3-flash-preview`
- Claude mediator: `claude-opus-4-6`

The UI lets the user edit these names before running extraction.

## Recommended Test Run

Use the sidebar max-page limit before running paid model calls over a full
document:

1. Set "Max pages for test runs" to `1` or another small number.
2. Upload a representative PDF.
3. Run extraction.
4. Watch the progress status text for the current page and provider wait.
5. Inspect page details and debug output.
6. Increase or clear the page limit after the profile is producing expected
   output.

For profiles with `page_context.following_pages: 1`, the app may upload one
extra source page as context for the last processed anchor page. That context
page is not processed as its own anchor unless it is inside the max-page limit.

## Output Files

The app creates a local run directory for every extraction:

```text
runs/<run_id>/
```

Each run folder contains:

- `input.pdf`: the uploaded source PDF,
- `run.json`: run id, document id, original filename, selected profile, and
  non-secret model/config values,
- `status.json`: live status, page progress, timestamps, and the latest message,
- `pages/page_NNNN.json`: one checkpoint per completed page,
- `final.json`: final export after successful completion,
- `debug.json`: debug export after successful completion.

The `runs/` directory is ignored by git because it can contain source PDFs and
large debug data. The app also offers download buttons for:

- final JSON: `<document_id>_final.json`
- debug JSON: `<document_id>_debug.json`

Saved profiles are written to `extraction_profiles/<slugified-profile-name>.json`.

## Stopping a Run

During an active extraction, click **Stop extraction** to cancel the current
run. The app updates `status.json` to `cancelled`, keeps page checkpoints that
were already completed, and discards the active page.

In `status.json`, `completed_pages` means durable page checkpoints already
written, while `current_page` means the page currently being processed. During
page 12 of 100, a normal running status can show `completed_pages: 11` and
`current_page: 12`.

For example, if cancellation is requested while page 12 of 100 is active, the
run folder keeps completed checkpoints such as pages 1 through 11. Page 12 is
not written. Because provider SDK requests are blocking network calls, a request
that is already in flight may still finish remotely, but the pipeline ignores
that active-page result.

## Troubleshooting

### `qpdf is required for PDF splitting but was not found on PATH`

Install `qpdf` and restart the shell or Streamlit process:

```bash
brew install qpdf
```

Confirm availability:

```bash
qpdf --version
```

### Missing API Key Warning

The sidebar and extraction page report whether provider keys are set. OpenAI and
Gemini are required. Claude is optional.

Set keys in the same shell that starts Streamlit:

```bash
export OPENAI_API_KEY="..."
export GEMINI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
streamlit run main.py
```

### Profile JSON Is Invalid

Use the Profile configuration tab. It parses and validates the JSON before save.
Common issues:

- `key_field` does not exist in `fields`,
- unsupported field `type`,
- unsupported normalizer name,
- `normalizers` is not a list,
- `page_context.following_pages` is outside the supported `0` to `1` range,
- nested object field has invalid `fields`.

### Provider Returned No Parseable JSON

Check the debug JSON:

- raw provider text,
- provider error string,
- provider operation,
- provider duration and usage metadata,
- generated provider schema,
- selected profile field names and types.

Provider schemas should constrain the output, but malformed provider text can
still happen and will be surfaced as page errors.

### Token Counts Are Unknown

The run report uses token usage returned by provider SDK responses. Some errors
or SDK response shapes may not include usage metadata. In that case, the app
keeps the timing data and marks the token count for that call as unknown instead
of estimating it.

### Claude Opus 4.7 Mediation Fails

Use `claude-opus-4-7` as the model name. The Claude adapter omits
`temperature=0` for this model because Opus 4.7 rejects non-default sampling
parameters. If mediation still fails, inspect the Claude entry in the debug
page details for the provider error string.

### Page Needs Review

A page becomes `needs_review` when:

- OpenAI or Gemini fails,
- provider output fails schema validation,
- a record key is missing,
- duplicate keys occur on a page,
- an anchor key-manifest provider disagrees or fails in context mode,
- an extraction returns a key outside the anchor manifest in context mode,
- baselines disagree and Claude is unavailable,
- Claude is unavailable or unable to produce a usable record for a disputed key.

Inspect the page diff and canonical outputs in the Streamlit page details.

### Duplicate Records Across Pages

The final export marks duplicate keys with:

```json
{
  "needs_review": true,
  "duplicate_pages": [1, 2]
}
```

This detection happens after all page records are merged.

## Maintenance Notes

- Regenerate this wiki after source changes so it reflects current behavior.
- Consider adding a dependency manifest to make setup reproducible.
- Consider adding tests for profile validation, normalizers, canonicalization,
  diffing, and merge behavior.
- Keep extraction profiles under version control if this directory becomes a git
  repository later.
