# Agent Instructions

## Project Overview

This is a Streamlit app for configurable PDF-to-JSON extraction.

The app:

- lets users define extraction profiles,
- splits uploaded PDFs into pages with qpdf,
- runs OpenAI and Gemini baseline extraction,
- optionally uses Claude to mediate disagreements,
- exports final and debug JSON.

## Important Files

- `main.py`: Streamlit UI.
- `pipeline.py`: end-to-end extraction orchestration.
- `providers.py`: OpenAI, Gemini, and Claude adapters.
- `compare.py`: normalization, validation, canonicalization, and diffing.
- `schema.py`: extraction profile validation, JSON Schema generation, prompts.
- `config_store.py`: saved profile handling.
- `pdf_pages.py`: qpdf-based PDF splitting.
- `README.md`: project entry documentation.
- `docs/`: project wiki and architecture documentation.
- `extraction_profiles/`: saved JSON extraction profiles.

## Development Rules

- Keep the app profile-driven. Do not hard-code WT-500-specific logic into the
  generic pipeline.
- Prefer small, focused changes.
- Preserve existing provider boundaries: UI in `main.py`, orchestration in
  `pipeline.py`, provider calls in `providers.py`.
- Do not expose API keys or secrets in logs, docs, commits, or debug output.
- Use ASCII unless editing existing text that already uses Unicode.
- When making any code, behavior, architecture, runtime, or profile change,
  update the relevant documentation in the same iteration. Documentation should
  stay current with the change, not be left for a separate cleanup pass.

## Runtime

The project currently has no dependency manifest.

Inferred runtime dependencies:

- Python 3.10+
- streamlit
- jsonschema
- openai
- google-genai
- anthropic
- qpdf CLI

Run locally with:

```bash
streamlit run main.py
```

Compile check:

```bash
python3 -m compileall .
```

## Documentation

When changing architecture, extraction flow, provider behavior, runtime setup,
profiles, or user-facing behavior, update the relevant docs immediately:

- `README.md`
- `docs/wiki-index.md`
- `docs/architecture.md`
- `docs/data-flow.md`
- `docs/extraction-profiles.md`
- `docs/provider-contracts.md`
- `docs/module-reference.md`
- `docs/operations.md`

If the docs describe generated-current-state information, update the date or
wording when regenerating or substantially revising them.

## Testing Expectations

There are currently no automated tests.

For now, at minimum:

- run `python3 -m compileall .`,
- manually verify Streamlit startup after UI or pipeline changes,
- use a small max-page limit before testing paid model calls.
