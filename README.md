# pdf2md

HTTP service that converts PDF files to Markdown. Built with FastAPI and [MarkItDown](https://github.com/microsoft/markitdown).

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run uvicorn main:app
```

Or with Docker:

```
docker build -t pdf2md .
docker run -p 8000:8000 pdf2md
```

## API

**POST /convert** — upload a PDF, get Markdown back.

```
curl -X POST http://localhost:8000/convert \
  -F "file=@document.pdf"
```

PHP (Guzzle):

```php
$response = $client->post('http://localhost:8000/convert', [
    'multipart' => [
        [
            'name'     => 'file',
            'contents' => fopen('/path/to/document.pdf', 'r'),
            'filename' => 'document.pdf',
        ],
    ],
]);

$result = json_decode($response->getBody(), true);
echo $result['markdown'];
```

Python:

```python
import requests

with open("document.pdf", "rb") as f:
    r = requests.post("http://localhost:8000/convert", files={"file": f})

print(r.json()["markdown"])
```

Go (resty):

```go
resp, _ := resty.New().R().
    SetFile("file", "document.pdf").
    Post("http://localhost:8000/convert")

fmt.Println(resp.String())
```

Response:

```json
{
  "markdown": "# pdf content...",
  "processing_time_ms": 142.5
}
```

**GET /health** — returns `{"status": "ok"}`

**GET /metrics** — Prometheus metrics (conversion duration, file sizes, total counts)
