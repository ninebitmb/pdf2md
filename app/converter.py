"""PDF extraction using Docling for structured document understanding."""

import logging
import threading
import warnings

# Suppress noisy PyTorch warning on CPU-only servers
warnings.filterwarnings("ignore", message=".*pin_memory.*")

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    EasyOcrOptions,
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

logger = logging.getLogger(__name__)


class EmptyExtractionError(Exception):
    """Raised when no text could be extracted from a PDF."""


def _base_pipeline_options() -> PdfPipelineOptions:
    opts = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        do_code_enrichment=False,
        do_formula_enrichment=False,
    )
    opts.table_structure_options = TableStructureOptions(
        do_cell_matching=True,
        mode=TableFormerMode.ACCURATE,
    )
    opts.accelerator_options = AcceleratorOptions(
        num_threads=4,
        device=AcceleratorDevice.AUTO,
    )
    return opts


def _create_converter(ocr: bool = False) -> DocumentConverter:
    opts = _base_pipeline_options()
    if ocr:
        opts.do_ocr = True
        opts.ocr_options = EasyOcrOptions(lang=["en", "lt", "de"])
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)},
    )


_converter: DocumentConverter | None = None
_converter_ocr: DocumentConverter | None = None
_lock = threading.Lock()


def _get_converter(ocr: bool = False) -> DocumentConverter:
    global _converter, _converter_ocr
    if ocr:
        if _converter_ocr is None:
            with _lock:
                if _converter_ocr is None:
                    logger.info("Initializing Docling DocumentConverter (OCR)")
                    _converter_ocr = _create_converter(ocr=True)
        return _converter_ocr
    else:
        if _converter is None:
            with _lock:
                if _converter is None:
                    logger.info("Initializing Docling DocumentConverter")
                    _converter = _create_converter(ocr=False)
        return _converter


def _get_page_count(doc) -> int:
    if hasattr(doc, "pages") and doc.pages:
        return len(doc.pages)
    if hasattr(doc, "num_pages"):
        return doc.num_pages()
    return 1


def _convert_doc(pdf_path: str, ocr: bool = False):
    """Run Docling conversion. Returns DoclingDocument."""
    converter = _get_converter(ocr=ocr)
    return converter.convert(pdf_path).document


def _has_real_content(md_text: str) -> bool:
    """Check if markdown has real content (not just HTML comments)."""
    stripped = md_text.strip() if md_text else ""
    if not stripped:
        return False
    return not all(
        line.strip().startswith("<!--") for line in stripped.split("\n") if line.strip()
    )


def extract_raw(pdf_path: str) -> tuple[str, int, dict]:
    """Extract column-aware markdown from PDF. Falls back to OCR if no text found.

    Uses Docling's bbox data for multi-column detection.
    Returns (markdown, page_count, doc_dict).
    Raises EmptyExtractionError if OCR also yields nothing.
    """
    from app.markdown_builder import build_markdown

    doc = _convert_doc(pdf_path, ocr=False)
    page_count = _get_page_count(doc)
    md_text = build_markdown(doc)

    if not _has_real_content(md_text):
        logger.info("No text without OCR (%d pages), retrying with OCR", page_count)
        doc = _convert_doc(pdf_path, ocr=True)
        page_count = _get_page_count(doc)
        md_text = build_markdown(doc)

    if not _has_real_content(md_text):
        logger.warning("No text even with OCR (%d pages)", page_count)
        raise EmptyExtractionError(
            f"No text could be extracted from the PDF ({page_count} pages). "
            "It may be image-only or scanned."
        )

    logger.debug("Extracted %d chars, %d pages", len(md_text), page_count)
    doc_dict = doc.export_to_dict()
    return md_text, page_count, doc_dict
