import logging
import tempfile
import time

from fastapi import FastAPI, HTTPException, Query, UploadFile
from prometheus_client import Counter, Histogram, make_asgi_app

from app.converter import EmptyExtractionError, extract_raw
from app.response import ConvertResponse
from app.structurer import structure_invoice

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
LOW_CONFIDENCE_THRESHOLD = 0.1
ALLOWED_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}

app = FastAPI(title="pdf 2 md")

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


@app.post("/convert")
def convert(
    file: UploadFile,
    raw: bool = Query(False, description="Return raw unstructured markdown"),
) -> ConvertResponse:
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        CONVERSIONS_TOTAL.labels(status="rejected").inc()
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

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

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(data)
            tmp.flush()

            start = time.time()
            raw_markdown, page_count = extract_raw(tmp.name)

            if raw:
                duration = time.time() - start
                CONVERSION_DURATION.observe(duration)
                CONVERSIONS_TOTAL.labels(status="success").inc()
                return ConvertResponse(
                    success=True,
                    markdown=raw_markdown,
                    raw_markdown=raw_markdown,
                    pages=page_count,
                    confidence=0.0,
                    document_type="unknown",
                    language="unknown",
                    has_table=False,
                    has_payment_info=False,
                    processing_time_ms=duration * 1000,
                )

            result = structure_invoice(raw_markdown, pages=page_count)
            duration = time.time() - start
            CONVERSION_DURATION.observe(duration)
    except HTTPException:
        raise
    except EmptyExtractionError as e:
        CONVERSIONS_TOTAL.labels(status="no_text").inc()
        logger.warning("Empty extraction: %s", e)
        raise HTTPException(status_code=422, detail=str(e))
    except (ValueError, RuntimeError) as e:
        CONVERSIONS_TOTAL.labels(status="bad_input").inc()
        logger.warning("Invalid PDF file: %s", e)
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF or is corrupted")
    except MemoryError:
        CONVERSIONS_TOTAL.labels(status="oom").inc()
        logger.error("Out of memory processing PDF")
        raise HTTPException(status_code=413, detail="PDF is too complex to process")
    except Exception:
        CONVERSIONS_TOTAL.labels(status="error").inc()
        logger.exception("Failed to convert PDF")
        raise HTTPException(status_code=500, detail="Conversion failed")

    is_successful = result.confidence >= LOW_CONFIDENCE_THRESHOLD
    if not is_successful:
        logger.warning("Low confidence extraction (%.2f)", result.confidence)
        CONVERSIONS_TOTAL.labels(status="low_confidence").inc()
    else:
        CONVERSIONS_TOTAL.labels(status="success").inc()

    return ConvertResponse(
        success=is_successful,
        markdown=result.markdown,
        raw_markdown=raw_markdown,
        pages=result.pages,
        confidence=result.confidence,
        document_type=result.document_type,
        language=result.language,
        has_table=result.has_table,
        has_payment_info=result.has_payment_info,
        processing_time_ms=duration * 1000,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
