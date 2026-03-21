FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    libxcb1 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py .
COPY app/ ./app/

# Pre-download all ML models at build time (not runtime)
RUN uv run python -c "\
from docling.document_converter import DocumentConverter; \
DocumentConverter(); \
import easyocr; \
easyocr.Reader(['en', 'lt', 'de'], gpu=False, download_enabled=True); \
print('All models downloaded')" || true

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
