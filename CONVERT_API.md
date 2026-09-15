# PDF ↔ DOCX Convert API

Give this document to the frontend or backend developer who will call the API.

**Production base URL:** `http://74.208.184.175:517`

Local example: `http://127.0.0.1:9001`

Interactive try-out: [http://74.208.184.175:517/docs](http://74.208.184.175:517/docs)

Related APIs: `RESUME_API.md`, `CV_API.md`

## What it does

Upload a file and download the converted file:

- **PDF → DOCX** (`POST /api/convert/pdf-to-docx`)
- **DOCX → PDF** (`POST /api/convert/docx-to-pdf`)

The response is the converted **file**, not JSON. Conversion is text-based. Images, columns, headers/footers, and exact page layout are not preserved.

Files are converted in memory. Nothing is saved on disk.

## Endpoints

| Method | Path | Use |
| --- | --- | --- |
| GET | `/api/health` | Liveness check (`pdf_docx_convert: true`) |
| GET | `/api/convert` | Contract / supported conversions |
| POST | `/api/convert/pdf-to-docx` | Upload a PDF, download a `.docx` |
| POST | `/api/convert/docx-to-pdf` | Upload a `.docx`, download a `.pdf` |

Limits: max **8 MB**. Types: **pdf** or **docx** depending on the endpoint. Old `.doc` is not supported.

Scanned image-only PDFs fail because there is no OCR in this version.

## PDF to DOCX

```bash
curl -X POST "{BASE_URL}/api/convert/pdf-to-docx" ^
  -F "file=@C:\path\to\resume.pdf" ^
  --output resume.docx
```

JavaScript:

```js
const form = new FormData();
form.append("file", fileInput.files[0]);

const res = await fetch(`${BASE_URL}/api/convert/pdf-to-docx`, {
  method: "POST",
  body: form,
});
if (!res.ok) {
  const err = await res.json();
  throw new Error(err.detail || "PDF to DOCX failed");
}
const blob = await res.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url;
a.download = "converted.docx";
a.click();
URL.revokeObjectURL(url);
```

Python:

```python
import requests

with open("resume.pdf", "rb") as handle:
    response = requests.post(
        "http://127.0.0.1:9001/api/convert/pdf-to-docx",
        files={"file": ("resume.pdf", handle, "application/pdf")},
    )
response.raise_for_status()
open("resume.docx", "wb").write(response.content)
```

## DOCX to PDF

```bash
curl -X POST "{BASE_URL}/api/convert/docx-to-pdf" ^
  -F "file=@C:\path\to\resume.docx" ^
  --output resume.pdf
```

JavaScript:

```js
const form = new FormData();
form.append("file", fileInput.files[0]);

const res = await fetch(`${BASE_URL}/api/convert/docx-to-pdf`, {
  method: "POST",
  body: form,
});
if (!res.ok) {
  const err = await res.json();
  throw new Error(err.detail || "DOCX to PDF failed");
}
const blob = await res.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url;
a.download = "converted.pdf";
a.click();
URL.revokeObjectURL(url);
```

Python:

```python
import requests

with open("resume.docx", "rb") as handle:
    response = requests.post(
        "http://127.0.0.1:9001/api/convert/docx-to-pdf",
        files={
            "file": (
                "resume.docx",
                handle,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
response.raise_for_status()
open("resume.pdf", "wb").write(response.content)
```

## Success response

HTTP `200` with the converted file bytes.

| Endpoint | `Content-Type` | Download name |
| --- | --- | --- |
| `/api/convert/pdf-to-docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | original name with `.docx` |
| `/api/convert/docx-to-pdf` | `application/pdf` | original name with `.pdf` |

`Content-Disposition` is `attachment`.

## Error responses

JSON `{ "detail": "..." }` when conversion fails.

| Status | When |
| --- | --- |
| 400 | Empty file |
| 413 | File larger than 8 MB |
| 415 | Wrong file type for that endpoint |
| 422 | File could not be read, or had no extractable text |

Example: `{ "detail": "Upload a PDF file (.pdf)." }`

## CORS

CORS is open (`allow_origins=["*"]`), so a browser app on another origin can call this API directly.
