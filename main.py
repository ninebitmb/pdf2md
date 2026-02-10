import logging
import tempfile
import time

from fastapi import FastAPI, HTTPException, UploadFile
from markitdown import MarkItDown
from prometheus_client import Counter, Histogram, make_asgi_app

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

app = FastAPI(title="pdf 2 md")
md = MarkItDown()

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
def convert(file: UploadFile) -> dict:
    if file.content_type != "application/pdf":
        CONVERSIONS_TOTAL.labels(status="rejected").inc()
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            data = file.file.read()
            if len(data) > MAX_FILE_SIZE:
                CONVERSIONS_TOTAL.labels(status="oversize").inc()
                raise HTTPException(status_code=413, detail=f"File too large, max {MAX_FILE_SIZE / 1024 / 1024:.4g}MB")
            CONVERSION_SIZE_BYTES.observe(len(data))
            tmp.write(data)
            tmp.flush()

            start = time.time()
            result = md.convert(tmp.name)
            duration = time.time() - start
            CONVERSION_DURATION.observe(duration)
    except Exception as e:
        CONVERSIONS_TOTAL.labels(status="error").inc()
        logger.exception("Failed to convert PDF")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {e}")

    CONVERSIONS_TOTAL.labels(status="success").inc()
    return {
        "markdown": result.text_content,
        "processing_time_ms": duration * 1000,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
