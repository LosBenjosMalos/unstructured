"""PDF page preparation helpers.

The default pipeline sends one PDF page at a time to the model providers.
Profiles can opt into an anchor/context window where qpdf also creates a
temporary PDF containing the anchor page plus the next source page.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PagePDF:
    """A temporary one-page PDF plus its 1-based source page number."""

    page_number: int
    path: Path


@dataclass(frozen=True)
class PageWindow:
    """Anchor page plus the PDF sent to providers for full extraction."""

    anchor_page_number: int
    anchor_page_path: Path
    extraction_pdf_path: Path
    context_page_numbers: list[int]

    @property
    def page_number(self) -> int:
        """Return the source page number represented by this anchor window."""

        return self.anchor_page_number


@dataclass
class PreparedPDF:
    """Container for all temporary page PDFs created from one source document."""

    source_path: Path
    temp_dir: Path
    pages: list[PagePDF]
    windows: list[PageWindow]

    def cleanup(self) -> None:
        """Remove the temporary directory and all generated PDFs inside it."""

        shutil.rmtree(self.temp_dir, ignore_errors=True)


def qpdf_available() -> bool:
    """Return whether the local `qpdf` command can be used for splitting."""

    return shutil.which("qpdf") is not None


def get_page_count(pdf_path: str | Path) -> int:
    """Read the number of pages in a PDF using qpdf.

    The project intentionally stays small and does not require a Python PDF
    dependency. qpdf is deterministic and avoids rendering the pages.
    """

    if not qpdf_available():
        raise RuntimeError("qpdf is required for PDF splitting but was not found on PATH.")

    result = subprocess.run(
        ["qpdf", "--show-npages", str(pdf_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def qpdf_page_range(page_numbers: list[int]) -> str:
    """Return a qpdf page range for already ordered source page numbers."""

    return ",".join(str(page_number) for page_number in page_numbers)


def split_pdf_into_pages(
    pdf_path: str | Path,
    max_pages: int | None = None,
    following_context_pages: int = 0,
) -> PreparedPDF:
    """Split a PDF into one-page PDFs and anchor extraction windows.

    The caller is responsible for calling `PreparedPDF.cleanup()` after the
    pipeline finishes, usually in a `finally` block.
    """

    source_path = Path(pdf_path)
    source_page_count = get_page_count(source_path)
    anchor_page_count = source_page_count
    if max_pages is not None:
        anchor_page_count = min(anchor_page_count, max_pages)

    context_pages = max(0, min(following_context_pages, 1))
    split_page_count = min(source_page_count, anchor_page_count + context_pages)

    temp_dir = Path(tempfile.mkdtemp(prefix="fm_page_extract_"))
    pages: list[PagePDF] = []
    page_by_number: dict[int, PagePDF] = {}

    for page_number in range(1, split_page_count + 1):
        output_path = temp_dir / f"page_{page_number:04d}.pdf"
        subprocess.run(
            [
                "qpdf",
                "--empty",
                "--pages",
                str(source_path),
                str(page_number),
                "--",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        page = PagePDF(page_number=page_number, path=output_path)
        pages.append(page)
        page_by_number[page_number] = page

    windows: list[PageWindow] = []
    for anchor_page_number in range(1, anchor_page_count + 1):
        context_page_numbers = [
            page_number
            for page_number in range(anchor_page_number + 1, anchor_page_number + context_pages + 1)
            if page_number <= source_page_count
        ]
        anchor_page = page_by_number[anchor_page_number]
        if context_page_numbers:
            output_path = temp_dir / f"window_{anchor_page_number:04d}.pdf"
            page_range = qpdf_page_range([anchor_page_number, *context_page_numbers])
            subprocess.run(
                [
                    "qpdf",
                    "--empty",
                    "--pages",
                    str(source_path),
                    page_range,
                    "--",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            extraction_pdf_path = output_path
        else:
            extraction_pdf_path = anchor_page.path

        windows.append(
            PageWindow(
                anchor_page_number=anchor_page_number,
                anchor_page_path=anchor_page.path,
                extraction_pdf_path=extraction_pdf_path,
                context_page_numbers=context_page_numbers,
            )
        )

    return PreparedPDF(source_path=source_path, temp_dir=temp_dir, pages=pages, windows=windows)
