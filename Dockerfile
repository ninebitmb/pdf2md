FROM zot.ninebit.lt/ninebit/python-ml:3.12

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
