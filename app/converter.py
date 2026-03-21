"""PDF extraction using Docling for structured document understanding."""

import logging

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

logger = logging.getLogger(__name__)


class EmptyExtractionError(Exception):
    """Raised when no text could be extracted from a PDF."""


def _create_converter() -> DocumentConverter:
    """Create a configured Docling DocumentConverter."""
    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        do_code_enrichment=False,
        do_formula_enrichment=False,
    )
    pipeline_options.table_structure_options = TableStructureOptions(
        do_cell_matching=True,
        mode=TableFormerMode.ACCURATE,
    )
    pipeline_options.accelerator_options = AcceleratorOptions(
        num_threads=4,
        device=AcceleratorDevice.AUTO,
    )

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


# Module-level singleton — heavy to initialize, reuse across requests
_converter: DocumentConverter | None = None


def _get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        logger.info("Initializing Docling DocumentConverter")
        _converter = _create_converter()
    return _converter


def extract_raw(pdf_path: str) -> tuple[str, int]:
    """Extract markdown and page count from a PDF file using Docling.

    Returns (markdown, page_count).
    Raises EmptyExtractionError if no text could be extracted.
    """
    converter = _get_converter()
    result = converter.convert(pdf_path)
    doc = result.document

    page_count = doc.num_pages() if hasattr(doc, 'num_pages') else 1
    # Fallback page count from pages
    if hasattr(doc, 'pages') and doc.pages:
        page_count = len(doc.pages)

    md_text = doc.export_to_markdown()

    if not md_text or not md_text.strip():
        logger.warning("Docling extraction produced no text (%d pages)", page_count)
        raise EmptyExtractionError(
            f"No text could be extracted from the PDF ({page_count} pages). "
            "It may be image-only or scanned."
        )

    logger.debug("Docling extracted %d chars, %d pages", len(md_text), page_count)
    return md_text, page_count
