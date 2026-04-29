"""Durable local storage for extraction runs.

Run data is intentionally stored outside temporary files so long extractions
leave usable checkpoints even if Streamlit reruns or the process stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4


RUNS_DIR = Path(__file__).resolve().parent / "runs"


@dataclass(frozen=True)
class ExtractionRun:
    """Filesystem paths for one durable extraction run."""

    run_id: str
    run_dir: Path

    @property
    def pages_dir(self) -> Path:
        return self.run_dir / "pages"

    @property
    def input_pdf_path(self) -> Path:
        return self.run_dir / "input.pdf"

    @property
    def status_path(self) -> Path:
        return self.run_dir / "status.json"

    @property
    def final_path(self) -> Path:
        return self.run_dir / "final.json"

    @property
    def debug_path(self) -> Path:
        return self.run_dir / "debug.json"

    def write_input_pdf(self, data: bytes) -> None:
        """Persist the uploaded source PDF for the lifetime of the run."""

        self.input_pdf_path.write_bytes(data)

    def update_status(self, **updates: Any) -> dict[str, Any]:
        """Merge status updates into status.json and return the new state."""

        current = read_json(self.status_path) if self.status_path.exists() else {}
        current.update(updates)
        current["updated_at"] = utc_now()
        atomic_write_json(self.status_path, current)
        return current

    def read_status(self) -> dict[str, Any]:
        """Read the current durable run status."""

        return read_json(self.status_path)

    def write_page_result(self, page_result: dict[str, Any]) -> Path:
        """Persist one completed page result as an independent checkpoint."""

        page_number = int(page_result["page_number"])
        path = self.pages_dir / f"page_{page_number:04d}.json"
        atomic_write_json(path, page_result)
        return path

    def load_page_results(self) -> list[dict[str, Any]]:
        """Load completed page checkpoints in page-number order."""

        results = [read_json(path) for path in sorted(self.pages_dir.glob("page_*.json"))]
        return sorted(results, key=lambda page: int(page.get("page_number", 0)))

    def write_outputs(self, final_json: dict[str, Any], debug_json: dict[str, Any]) -> None:
        """Persist final and debug exports for later download or inspection."""

        atomic_write_json(self.final_path, final_json)
        atomic_write_json(self.debug_path, debug_json)


def create_run(
    *,
    document_id: str,
    original_filename: str,
    profile: dict[str, Any],
    config: dict[str, Any],
) -> ExtractionRun:
    """Create a durable run directory and write non-secret run metadata."""

    RUNS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}_{slugify(document_id)}_{uuid4().hex[:8]}"
    run_dir = RUNS_DIR / run_id
    pages_dir = run_dir / "pages"
    pages_dir.mkdir(parents=True)

    run = ExtractionRun(run_id=run_id, run_dir=run_dir)
    created_at = utc_now()
    atomic_write_json(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "document_id": document_id,
            "original_filename": original_filename,
            "created_at": created_at,
            "profile": profile,
            "config": config,
        },
    )
    atomic_write_json(
        run.status_path,
        {
            "run_id": run_id,
            "document_id": document_id,
            "status": "created",
            "created_at": created_at,
            "updated_at": created_at,
            "completed_pages": 0,
            "total_pages": None,
            "current_page": None,
            "message": "Run created",
        },
    )
    return run


def slugify(value: str) -> str:
    """Return a compact filesystem-safe slug."""

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return (slug or "document")[:60]


def utc_now() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from disk."""

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object.")
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON via replace so readers never see partial files."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)
