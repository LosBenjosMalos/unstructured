"""Streamlit UI for configurable PDF-to-JSON extraction."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

from config_store import list_profiles, load_profile, save_profile
from debug_monitor import ExtractionDebugMonitor, render_run_report
from pipeline import PipelineConfig, process_document, to_pretty_json
from schema import build_page_schema, output_preview, validate_profile


def env_status(name: str) -> str:
    """Return a safe status for an API key without exposing the secret value."""

    return "set" if os.getenv(name) else "missing"


def save_uploaded_pdf(uploaded_file: st.runtime.uploaded_file_manager.UploadedFile) -> Path:
    """Persist a Streamlit upload to a temporary PDF path for qpdf and SDK uploads."""

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.getbuffer())
        return Path(tmp.name)


def parse_profile_text(profile_text: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse and validate profile JSON entered in the configuration editor."""

    try:
        profile = json.loads(profile_text)
    except json.JSONDecodeError as exc:
        return None, [f"Profile JSON is invalid: {exc}"]
    return profile, validate_profile(profile)


def render_profile_editor() -> None:
    """Render the profile configuration page and save valid profiles to disk."""

    st.header("Extraction profile configuration")
    st.write(
        "Define the JSON structure, the record identity key, and optional field-level normalization rules."
    )

    profile_names = list_profiles()
    selected_name = st.selectbox("Load saved profile", profile_names, key="config_profile_name")
    selected_profile = load_profile(selected_name)

    default_text = json.dumps(selected_profile, indent=2, ensure_ascii=False)
    profile_text = st.text_area(
        "Profile JSON",
        value=default_text,
        height=520,
        key=f"profile_editor_{selected_name}",
        help="Default behavior applies no normalization. Add normalizers per field only where needed.",
    )

    profile, errors = parse_profile_text(profile_text)
    if errors:
        st.error("Profile has errors:")
        for error in errors:
            st.write(f"- {error}")
    elif profile is not None:
        st.success("Profile is valid.")

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Output preview")
            st.json(output_preview(profile))
        with col_b:
            st.subheader("Provider JSON Schema")
            st.json(build_page_schema(profile))

        if st.button("Save profile", type="primary"):
            try:
                path = save_profile(profile)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(f"Saved profile to {path}")

    with st.expander("Supported field options"):
        st.markdown(
            """
Each field supports:

```json
{
  "type": "string | integer | number | boolean | object | array",
  "description": "optional model guidance",
  "normalizers": ["trim", "collapse_whitespace"]
}
```

Supported normalizers: `trim`, `collapse_whitespace`, `normalize_dashes`,
`normalize_unicode`, `remove_all_whitespace`, `remove_soft_hyphen`,
`repair_hyphenated_line_breaks`, `lower`, `upper`, `to_int`, `to_float`,
`to_bool`, `yes_no`.

No normalizers are applied unless you add them to a field.
            """.strip()
        )


def render_page_summary(page: dict) -> None:
    """Render one page's status, diff, baseline outputs, and optional Claude output."""

    status = page["baseline_status"]
    if status == "agreed":
        st.success(f"Page {page['page_number']}: agreed")
    elif status == "mediated":
        st.info(f"Page {page['page_number']}: mediated by Claude")
    else:
        st.warning(f"Page {page['page_number']}: needs review")

    if page.get("context_pages"):
        st.write("Context pages:", page["context_pages"])
    st.write("Expected record keys:", page["expected_keys"])

    key_manifest = page.get("debug", {}).get("key_manifest")
    if key_manifest is not None:
        with st.expander(f"Page {page['page_number']} anchor key manifest"):
            st.json(key_manifest)

    with st.expander(f"Page {page['page_number']} diff", expanded=status != "agreed"):
        st.json(page["diff"])

    with st.expander(f"Page {page['page_number']} raw baseline and mediator outputs"):
        st.subheader("OpenAI")
        st.json(page["debug"]["openai"])
        st.subheader("Gemini")
        st.json(page["debug"]["gemini"])
        if page["debug"]["claude"] is not None:
            st.subheader("Claude")
            st.json(page["debug"]["claude"])

    with st.expander(f"Page {page['page_number']} normalized outputs used for comparison"):
        st.subheader("OpenAI normalized")
        st.json(page["debug"]["canonical"]["openai"])
        st.subheader("Gemini normalized")
        st.json(page["debug"]["canonical"]["gemini"])
        if page["debug"]["canonical"]["claude"] is not None:
            st.subheader("Claude normalized")
            st.json(page["debug"]["canonical"]["claude"])

    with st.expander(f"Page {page['page_number']} final records"):
        st.json(page["records"])


def render_extraction_page() -> None:
    """Render the PDF upload page and run extraction with a selected profile."""

    st.header("Run extraction")
    profile_names = list_profiles()
    selected_name = st.selectbox("Extraction profile", profile_names, key="extract_profile_name")
    selected_profile = load_profile(selected_name)

    with st.expander("Selected profile preview", expanded=False):
        st.json(output_preview(selected_profile))

    uploaded = st.file_uploader("Upload a PDF", type=["pdf"])
    document_id = st.text_input(
        "Document ID",
        uploaded.name if uploaded is not None else "uploaded-document",
    )

    required_missing = [
        name for name in ["OPENAI_API_KEY", "GEMINI_API_KEY"] if not os.getenv(name)
    ]
    if required_missing:
        st.warning(f"Missing required API keys: {', '.join(required_missing)}")

    if not os.getenv("ANTHROPIC_API_KEY"):
        st.info("ANTHROPIC_API_KEY is missing. Mismatches will be marked for manual review.")

    if st.button("Run extraction", type="primary"):
        if uploaded is None:
            st.error("Upload a PDF before running extraction.")
            st.stop()
        if required_missing:
            st.error("OpenAI and Gemini API keys are required for the baseline comparison.")
            st.stop()

        temp_pdf_path = save_uploaded_pdf(uploaded)
        progress_bar = st.progress(0)
        progress_text = st.empty()
        debug_monitor = ExtractionDebugMonitor()

        def update_progress(message: str, index: int, total: int) -> None:
            """Update Streamlit progress while the pipeline processes pages."""

            progress_text.write(message)
            progress_bar.progress(min(max(index / total, 0.0), 1.0) if total else 1.0)

        config = PipelineConfig(
            openai_api_key=os.environ["OPENAI_API_KEY"],
            gemini_api_key=os.environ["GEMINI_API_KEY"],
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            extraction_profile=selected_profile,
            openai_model=st.session_state["openai_model"],
            gemini_model=st.session_state["gemini_model"],
            claude_model=st.session_state["claude_model"],
            max_pages=st.session_state["max_pages"] or None,
        )

        try:
            with st.spinner("Extracting pages..."):
                result = process_document(
                    pdf_path=temp_pdf_path,
                    document_id=document_id,
                    config=config,
                    progress_callback=update_progress,
                    debug_monitor=debug_monitor,
                )
        except Exception as exc:
            st.exception(exc)
            st.stop()
        finally:
            try:
                temp_pdf_path.unlink(missing_ok=True)
            except OSError:
                pass

        st.success("Extraction complete")

        final_json = result["final"]
        debug_json = result["debug"]

        render_run_report(debug_json.get("run_debug") or {})

        st.header("Final JSON")
        st.json(final_json)

        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                "Download final JSON",
                data=to_pretty_json(final_json),
                file_name=f"{document_id}_final.json",
                mime="application/json",
            )
        with col_b:
            st.download_button(
                "Download debug JSON",
                data=to_pretty_json(debug_json),
                file_name=f"{document_id}_debug.json",
                mime="application/json",
            )

        st.header("Page details")
        for page in debug_json["pages"]:
            render_page_summary(page)


def render_sidebar() -> None:
    """Render provider and run-limit settings shared by all pages."""

    with st.sidebar:
        st.header("Provider configuration")
        st.write(
            {
                "OPENAI_API_KEY": env_status("OPENAI_API_KEY"),
                "GEMINI_API_KEY": env_status("GEMINI_API_KEY"),
                "ANTHROPIC_API_KEY": env_status("ANTHROPIC_API_KEY"),
            }
        )

        st.text_input("OpenAI baseline model", "gpt-5.4-mini", key="openai_model")
        st.text_input("Gemini baseline model", "gemini-3-flash-preview", key="gemini_model")
        st.text_input("Claude mediator model", "claude-opus-4-7", key="claude_model")
        st.number_input(
            "Max pages for test runs (0 = all)",
            min_value=0,
            value=0,
            step=1,
            key="max_pages",
            help="Use a small number while testing paid model calls.",
        )


def main() -> None:
    """Configure the Streamlit app and render the selected workflow page."""

    st.set_page_config(page_title="Configurable PDF to JSON", page_icon="📄", layout="wide")
    st.title("Configurable PDF to JSON extractor")
    st.caption(
        "Users define the expected JSON profile, select it for extraction, and compare model outputs by the configured key."
    )

    render_sidebar()
    extraction_tab, config_tab = st.tabs(["Extraction", "Profile configuration"])
    with extraction_tab:
        render_extraction_page()
    with config_tab:
        render_profile_editor()


if __name__ == "__main__":
    main()
