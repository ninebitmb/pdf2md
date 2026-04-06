# pdf2md/converter

FastAPI microservice that converts PDFs and images to Markdown using Docling ML extraction with EasyOCR fallback.

## Tech Stack

Python 3.12, FastAPI, Docling (ML document understanding), EasyOCR (en/lt/de), pdfplumber, Prometheus metrics, uv package manager. Runs on `harbor.ninebit.lt/ninebit/python-ml:3.12` base image.

## Commands

```bash
# Dev (from repo root /Users/simas/Developer/pdf2md)
docker compose up -d                    # starts all services (converter, service, pgsql, redis)
# converter available at http://converter.pdf2md.local (OrbStack)

# From converter/ dir
make build                              # rebuild without cache
make logs                               # tail logs
make restart

# Lint
uv run ruff check .
uv run ruff format .

# Run locally (needs ML models downloaded)
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Test conversion
curl -X POST http://localhost:8000/convert -F "file=@invoice.pdf"
curl -X POST http://localhost:8000/convert?format=json -F "file=@invoice.pdf"
curl -X POST http://localhost:8000/experiment -F "file=@invoice.pdf"
```

## API

| Endpoint | Method | Description |
|---|---|---|
| `/convert` | POST | PDF/image to markdown or JSON. Query param `format=markdown\|json`. Returns `{markdown, pages, processing_time_ms}` or `{document, pages, processing_time_ms}` |
| `/experiment` | POST | Structured Lithuanian invoice extraction. Returns `ConvertResponse` with confidence score, document_type, sections |
| `/health` | GET | `{"status": "ok"}` |
| `/metrics` | GET | Prometheus metrics |

Accepts: PDF, JPEG, PNG, TIFF, WebP. Max 50MB. Multipart file upload.

## Architecture

```
main.py              → FastAPI app, endpoints, upload validation, Prometheus metrics
app/converter.py     → Docling extraction, lazy-loaded converters (with/without OCR), OCR fallback
app/markdown_builder.py → Builds markdown from Docling document model
app/structurer.py    → Invoice structuring pipeline: blocks → classify → extract fields → assemble
app/section_classifier.py → Classifies text blocks into sections (metadata, seller, buyer, etc.)
app/table_parser.py  → Extracts tables from classified blocks
app/patterns.py      → All regex patterns for invoice field extraction
app/normalizers.py   → Field cleaning, date/amount/IBAN normalization
app/response.py      → Pydantic response model (ConvertResponse)
```

**Extraction pipeline:** Upload → temp file → Docling convert (no OCR) → if empty, retry with OCR → `build_markdown()` → return. For `/experiment`: raw markdown → `split_into_blocks` → `classify_blocks` → extract fields per section → confidence score → structured markdown.

## Key Patterns

- **ML model loading**: Lazy singleton with double-checked locking (`_get_converter`). Two instances: with and without OCR. Models pre-downloaded at Docker build time.
- **Error handling**: Specific HTTP codes — 400 (bad input), 413 (too large/complex), 422 (no text extracted), 500 (unexpected). All counted in Prometheus.
- **Metrics**: `pdf_conversion_duration_seconds`, `pdf_conversion_size_bytes`, `pdf_conversions_total` (by status label).
- **Invoice language**: Lithuanian-first (section headings in LT). Language detection via keyword counting (lt vs en).
- **Confidence score**: Weighted sum (0-1) across 10 field checks. Threshold 0.1 for `success=true`.

## Development

- Dev compose mounts `main.py` and `app/` into container with `--reload` — edit locally, auto-restarts.
- OrbStack domains: `converter.pdf2md.local` (converter), `service.pdf2md.local` (Laravel frontend).
- First build is slow — downloads Docling + EasyOCR models (~2GB).
- The `service` is a Laravel app that calls converter at `http://converter:8000`.

## Do NOT

- Add GPU dependencies — runs CPU-only in production.
- Change EasyOCR language list (`en`, `lt`, `de`) without rebuilding the Docker image (models baked in).
- Remove the `|| true` from the model pre-download `RUN` step — some model downloads may fail transiently.
- Use `async def` for endpoints — Docling extraction is CPU-bound and blocks; sync endpoints run in threadpool which is correct.
