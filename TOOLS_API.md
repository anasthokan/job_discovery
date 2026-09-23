# Tools page APIs (ATS Matcher + PDF/Word Convert)

Give this document to the frontend developer for `tools.html`.

**Production base URL:** `http://74.208.184.175:517`

Interactive try-out: [http://74.208.184.175:517/docs](http://74.208.184.175:517/docs)

These two APIs map 1:1 to the two cards on the Tools page.

| UI card | Button | API |
| --- | --- | --- |
| AI Resume & ATS Matcher | Scan Resume ATS Score | `POST /api/ats/score-file` |
| PDF to Word & Word to PDF Converter | Convert Document Now | `POST /api/convert/pdf-to-docx` or `POST /api/convert/docx-to-pdf` |

CORS is open (`allow_origins=["*"]`).

---

## 1) ATS Matcher

Form fields:

| UI label | Form key | Required |
| --- | --- | --- |
| Target Job Title | `job_title` | no, but send it |
| Upload Resume File | `file` | **yes** (PDF / DOCX) |
| Paste Job Description | `job_description` | yes for a real match score |

```js
const form = new FormData();
form.append("file", document.getElementById("ats-resume-file").files[0]);
form.append("job_title", document.getElementById("ats-job-title").value);
form.append("job_description", document.getElementById("ats-job-desc").value);

const res = await fetch("http://74.208.184.175:517/api/ats/score-file", {
  method: "POST",
  body: form,
});
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "ATS score failed");

// data.score              0-100
// data.label              Excellent | Strong | Good | Fair | Needs work
// data.job_title          echoed title
// data.matched_keywords   ["Python", "FastAPI", ...]
// data.suggested_additions ["Kubernetes", "CI/CD", ...]
```

UI mapping (same as the current mock box):

```js
atsOutput.innerHTML = `
  <div>ATS Match Score: ${data.score}% ${data.label}</div>
  <p>Scanned <strong>${data.source.filename}</strong> against <strong>${data.job_title || title}</strong>.</p>
  <div>Matching Keywords: ${data.matched_keywords.join(", ") || "none"}</div>
  <div>Suggested Additions: ${data.suggested_additions.join(", ") || "none"}</div>
`;
```

**Postman:** POST `http://74.208.184.175:517/api/ats/score-file` → Body → **form-data**. `file` type **File**. Do not set `Content-Type` yourself.

Full contract: `ATS_API.md`.

---

## 2) PDF ↔ Word converter

Form fields:

| UI label | Use |
| --- | --- |
| Conversion Mode | pick the matching URL |
| Select Document File | form key `file` |

| Dropdown value | Method | Path |
| --- | --- | --- |
| PDF to Word (.docx) | POST | `/api/convert/pdf-to-docx` |
| Word (.docx) to PDF | POST | `/api/convert/docx-to-pdf` |

Response is the **file bytes**, not JSON. Trigger a download.

```js
const file = document.getElementById("conv-file-input").files[0];
const mode = document.getElementById("conv-direction").value; // pdf2word | word2pdf
const path = mode === "pdf2word" ? "/api/convert/pdf-to-docx" : "/api/convert/docx-to-pdf";
const ext = mode === "pdf2word" ? "docx" : "pdf";

const form = new FormData();
form.append("file", file);

const res = await fetch(`http://74.208.184.175:517${path}`, {
  method: "POST",
  body: form,
});
if (!res.ok) {
  const err = await res.json();
  throw new Error(err.detail || "Convert failed");
}
const blob = await res.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url;
a.download = file.name.replace(/\.[^/.]+$/, "") + "." + ext;
a.click();
URL.revokeObjectURL(url);
```

**Postman:** same URL, Body → **form-data**, key `file` type **File**. Then **Send and Download**. A `.docx` body looks like XML — that is the Word file.

Old `.doc` is not supported. Convert to PDF or DOCX first.

Full contract: `CONVERT_API.md`.
