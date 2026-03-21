import logging
import tempfile
import time
from enum import StrEnum

from fastapi import FastAPI, HTTPException, Query, UploadFile
from prometheus_client import Counter, Histogram, make_asgi_app

from app.converter import EmptyExtractionError, extract_raw
from app.response import ConvertResponse
from app.structurer import structure_invoice

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/octet-stream",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}


class OutputFormat(StrEnum):
    MARKDOWN = "markdown"
    JSON = "json"


app = FastAPI(
    title="PDF2MD",
    description="PDF/image to Markdown conversion service powered by Docling with OCR fallback.",
    version="0.4.0",
)

CONVERSION_DURATION = Histogram(
    "pdf_conversion_duration_seconds",
    "Time spent converting PDF to markdown",
)
CONVERSION_SIZE_BYTES = Histogram(
    "pdf_conversion_size_bytes",
    "Size of uploaded PDF files in bytes",
    buckets=[1_000, 10_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000, 50_000_000],
)
CONVERSIONS_TOTAL = Counter(
    "pdf_conversions_total",
    "Total number of PDF conversions",
    ["status"],
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


def _read_upload(file: UploadFile) -> bytes:
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        CONVERSIONS_TOTAL.labels(status="rejected").inc()
        raise HTTPException(status_code=400, detail="Only PDF and image files are accepted")

    try:
        data = file.file.read()
    except (IOError, OSError) as e:
        CONVERSIONS_TOTAL.labels(status="upload_error").inc()
        logger.warning("Failed to read uploaded file: %s", e)
        raise HTTPException(status_code=400, detail="Failed to read the uploaded file")
    finally:
        file.file.close()

    if not data:
        CONVERSIONS_TOTAL.labels(status="rejected").inc()
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > MAX_FILE_SIZE:
        CONVERSIONS_TOTAL.labels(status="oversize").inc()
        raise HTTPException(status_code=413, detail=f"File too large, max {MAX_FILE_SIZE / 1024 / 1024:.4g}MB")
    CONVERSION_SIZE_BYTES.observe(len(data))
    return data


def _extract(data: bytes, filename: str = "document.pdf") -> tuple[str, int, dict, float]:
    """Extract content from PDF/image. Returns (markdown, pages, doc_dict, duration_ms)."""
    suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".pdf"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            start = time.time()
            md, pages, doc_dict = extract_raw(tmp.name)
            duration_ms = (time.time() - start) * 1000
            CONVERSION_DURATION.observe(duration_ms / 1000)
            return md, pages, doc_dict, duration_ms
    except EmptyExtractionError as e:
        CONVERSIONS_TOTAL.labels(status="no_text").inc()
        logger.warning("Empty extraction: %s", e)
        raise HTTPException(status_code=422, detail=str(e))
    except (ValueError, RuntimeError) as e:
        CONVERSIONS_TOTAL.labels(status="bad_input").inc()
        logger.warning("Invalid file: %s", e)
        raise HTTPException(status_code=400, detail="The uploaded file is not valid or is corrupted")
    except MemoryError:
        CONVERSIONS_TOTAL.labels(status="oom").inc()
        logger.error("Out of memory processing file")
        raise HTTPException(status_code=413, detail="File is too complex to process")
    except Exception:
        CONVERSIONS_TOTAL.labels(status="error").inc()
        logger.exception("Failed to convert file")
        raise HTTPException(status_code=500, detail="Conversion failed")


@app.post(
    "/convert",
    summary="Convert PDF/image to Markdown or JSON",
    description=(
        "Converts a PDF or image file using Docling ML-based extraction. "
        "Supports PDF, JPG, PNG, TIFF, WebP. "
        "Detects multi-column layouts using element bounding boxes. "
        "Falls back to OCR automatically for scanned/image-only documents.\n\n"
        "**format=markdown** (default): Clean markdown with headings, tables, lists.\n\n"
        "**format=json**: Full Docling document structure with element types, "
        "bounding boxes, table cell data, and reading order."
    ),
    responses={
        400: {"description": "Invalid file (unsupported format, empty, or corrupted)"},
        413: {"description": "File too large (max 50MB) or too complex"},
        422: {"description": "No text could be extracted (even with OCR)"},
    },
)
def convert(
    file: UploadFile,
    format: OutputFormat = Query(OutputFormat.MARKDOWN, description="Output format: markdown or json"),
) -> dict:
    data = _read_upload(file)
    md, pages, doc_dict, duration_ms = _extract(data, file.filename or "document.pdf")

    CONVERSIONS_TOTAL.labels(status="success").inc()

    if format == OutputFormat.JSON:
        return {
            "document": doc_dict,
            "pages": pages,
            "processing_time_ms": duration_ms,
        }

    return {
        "markdown": md,
        "pages": pages,
        "processing_time_ms": duration_ms,
    }


@app.post(
    "/experiment",
    summary="Convert PDF to structured invoice Markdown",
    description=(
        "Converts a PDF invoice to structured Markdown with standardized Lithuanian sections: "
        "Metaduomenys, Pardavėjas, Pirkėjas, Prekės/Paslaugos, Sumos, Mokėjimo rekvizitai, Pastabos. "
        "Uses Docling extraction + post-processing pipeline. "
        "Includes confidence score (0-1) and raw_markdown fallback."
    ),
    responses={
        400: {"description": "Invalid file"},
        422: {"description": "No text could be extracted"},
    },
)
def experiment(file: UploadFile) -> ConvertResponse:
    data = _read_upload(file)
    md, pages, _, duration_ms = _extract(data, file.filename or "document.pdf")

    result = structure_invoice(md, pages=pages)

    CONVERSIONS_TOTAL.labels(status="success").inc()
    return ConvertResponse(
        success=result.confidence >= 0.1,
        markdown=result.markdown,
        raw_markdown=md,
        pages=result.pages,
        confidence=result.confidence,
        document_type=result.document_type,
        language=result.language,
        has_table=result.has_table,
        has_payment_info=result.has_payment_info,
        processing_time_ms=duration_ms,
    )


@app.get(
    "/health",
    summary="Health check",
    description="Returns service status. Used by Docker healthcheck and load balancers.",
)
def health() -> dict:
    return {"status": "ok"}
