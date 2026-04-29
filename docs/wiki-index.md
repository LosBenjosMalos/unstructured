# Project Wiki

Generated from the current source tree on 2026-04-29.

This wiki documents the current implementation of the configurable PDF-to-JSON
extraction application. There were no existing Markdown docs in the repository
when this wiki was created.

## Pages

- [Architecture](architecture.md): system components, ownership boundaries, and
  high-level structure.
- [Runtime Data Flow](data-flow.md): what happens from PDF upload through final
  JSON export.
- [Extraction Profiles](extraction-profiles.md): profile format, built-in
  defaults, saved profiles, schema generation, and normalizers.
- [Provider Contracts](provider-contracts.md): OpenAI, Gemini, and Claude
  adapter behavior.
- [Module Reference](module-reference.md): file-by-file API and responsibility
  map.
- [Operations](operations.md): local setup, environment variables, qpdf, running
  the app, and troubleshooting.

## One-Screen Summary

The app is a Streamlit UI around a model-assisted extraction pipeline.

1. A user uploads a PDF and selects an extraction profile.
2. `run_store.py` creates a durable `runs/<run_id>/` folder with the uploaded
   PDF, run status, and non-secret profile/config snapshot.
3. `pdf_pages.py` splits the document into one-page temporary PDFs and optional
   anchor/context windows using `qpdf`.
4. `providers.py` can ask OpenAI and Gemini for anchor-page keys before sending
   each extraction window for baseline extraction.
5. `compare.py` canonicalizes both outputs using the selected profile and
   compares them by the configured key field.
6. `pipeline.py` accepts agreed records, asks Claude to mediate disagreements
   when available, or marks unresolved records for manual review.
7. Each completed page is checkpointed to the run folder before the pipeline
   moves on.
8. `debug_monitor.py` tracks provider timing and usage metadata.
9. `main.py` renders the run report, final JSON, debug JSON, page-level diffs,
   and downloads.

## Main Concepts

- **Extraction profile**: A JSON configuration that defines the target output
  shape and optional normalization rules.
- **Provider result**: One model response with parsed JSON, raw text, and error
  metadata.
- **Canonical page**: A normalized provider output keyed by record identity.
- **Anchor key manifest**: Optional model-derived key list from the anchor page
  only, used when a profile enables next-page context.
- **Context page**: A following page that may complete anchor-page records but
  should not contribute new clean records for that anchor page.
- **Diff**: Deterministic comparison between OpenAI and Gemini canonical pages.
- **Mediation**: Optional Claude pass that resolves baseline disagreements using
  the source PDF page.
- **Manual review**: Fallback status when disagreements or provider errors
  cannot be resolved automatically.
- **Run report**: Provider elapsed time and token metadata when providers
  return it.
- **Run folder**: Durable local `runs/<run_id>/` storage for the uploaded PDF,
  status, page checkpoints, and final exports.
- **Cancellation**: Current-run Stop action that marks the run `cancelled` and
  keeps only page checkpoints that were completed before the active page.

## Source Map

- `main.py`: Streamlit interface.
- `pipeline.py`: end-to-end orchestration and final export assembly.
- `providers.py`: OpenAI, Gemini, and Claude API adapters.
- `debug_monitor.py`: timing, token usage, and Streamlit run-report rendering.
- `run_store.py`: durable run directories, status JSON, page checkpoints, and
  final/debug export files.
- `compare.py`: validation, normalization, canonicalization, and diffing.
- `schema.py`: profile validation, JSON Schema generation, and prompts.
- `config_store.py`: JSON profile persistence.
- `pdf_pages.py`: qpdf-backed page splitting.
- `extraction_profiles/`: saved profile JSON files.
