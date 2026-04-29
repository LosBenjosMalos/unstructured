# Configurable PDF to JSON Extractor

Generated from the current source tree on 2026-04-19.

This project is a Streamlit application for extracting structured JSON records
from PDFs. Users define an extraction profile, upload a PDF, and run a
page-by-page extraction workflow that compares two baseline model outputs before
optionally asking a third model to mediate disagreements.

The application is intentionally profile-driven. It does not hard-code one
document format into the pipeline. Instead, a JSON extraction profile defines:

- the top-level records array name,
- the record identity field,
- the fields each record should contain,
- optional field normalizers and type conversions,
- optional next-page context for records that continue across page boundaries.

## Wiki

The complete project wiki lives in [docs/wiki-index.md](docs/wiki-index.md).

- [Architecture](docs/architecture.md)
- [Runtime Data Flow](docs/data-flow.md)
- [Extraction Profiles](docs/extraction-profiles.md)
- [Provider Contracts](docs/provider-contracts.md)
- [Module Reference](docs/module-reference.md)
- [Operations](docs/operations.md)

## Quick Start

This repository currently has no dependency manifest, so install the inferred
runtime dependencies manually:

```bash
python -m venv .venv
source .venv/bin/activate
pip install streamlit jsonschema openai google-genai anthropic
```

Install `qpdf`, which is required for PDF page splitting:

```bash
brew install qpdf
```

Set provider API keys:

```bash
export OPENAI_API_KEY="..."
export GEMINI_API_KEY="..."
export ANTHROPIC_API_KEY="..." # optional, enables mediation
```

Run the app:

```bash
streamlit run main.py
```

## Current Application Shape

The app has two Streamlit tabs:

- **Extraction**: choose a profile, upload a PDF, run extraction, download final
  and debug JSON, and inspect the final run report.
- **Profile configuration**: edit, validate, preview, and save extraction
  profiles.

The baseline extraction requires OpenAI and Gemini keys. Claude is optional. If
Claude is not configured, disagreements are marked for manual review.

During extraction, the progress text reports the current page and provider the
app is waiting on. After extraction, the run report shows provider timings and
token counts when the provider SDK response includes usage metadata; otherwise
the affected calls are marked as unknown.

Profiles can opt into two-page continuation handling with:

```json
{
  "page_context": {
    "following_pages": 1
  }
}
```

When enabled, OpenAI and Gemini first extract only the anchor page's record keys,
then the full extraction receives the anchor page plus the next page as context.
The context page may complete fields for anchor-page records, but records that
start on the context page are marked for review instead of being accepted as
clean agreed records.

## Documentation Freshness

These docs were written by scanning the current codebase. When the code changes,
regenerate or update this wiki from the source again so the documentation stays
current.
