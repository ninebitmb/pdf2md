ARG BASE_IMAGE=zot.ninebit.lt/ninebit/python-ml:3.12
FROM ${BASE_IMAGE}

ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="pdf2md-converter" \
      org.opencontainers.image.source="https://github.com/ninebitmb/pdf2md" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.url="https://pdf2md.ninebit.lt" \
      org.opencontainers.image.licenses="proprietary"

ENV APP_REVISION=${GIT_SHA}

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
