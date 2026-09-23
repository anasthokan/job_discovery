# ATS Resume Score API

Give this document to the frontend or backend developer who will call the API.

**Production base URL:** `http://74.208.184.175:517`

Local example: `http://127.0.0.1:9001`

Interactive try-out: [http://74.208.184.175:517/docs](http://74.208.184.175:517/docs)

Related APIs: `RESUME_API.md`, `JOBS_API.md`, `CV_API.md`, `CONVERT_API.md`, `TOOLS_API.md`

Sample files in this repo: `samples/sample-resume.txt` and `samples/sample-job-description.txt`.

## What it does

Upload a resume (PDF, DOCX, or TXT) or send raw text. The API returns an **ATS score from 0–100**, a letter grade, a per-category breakdown, pass/fail checks, and fix suggestions.

Optional: also send a job description (or a scraped `job_id`). Then the score includes a **job keyword match**.

Files are parsed in memory. Nothing is saved on disk.

## Endpoints

| Method | Path | Use |
| --- | --- | --- |
| GET | `/api/health` | Liveness check (`ats_score: true`) |
| GET | `/api/ats` | Contract / supported formats |
| POST | `/api/ats/score` | JSON body (resume text or parsed resume) |
| POST | `/api/ats/score-file` | Upload a resume file (`multipart/form-data`) |

Limits: max **8 MB**. Types: **pdf, docx, txt**. Old `.doc` is not supported; convert to PDF or DOCX first.

Score is heuristic (contact fields, standard sections, skill keywords, optional JD overlap). Scanned image-only PDFs fail because there is no OCR in this version.

## File upload (resume only)

```bash
curl -X POST "{BASE_URL}/api/ats/score-file" ^
  -F "file=@C:\path\to\resume.pdf"
```

JavaScript:

```js
const form = new FormData();
form.append("file", fileInput.files[0]);

const res = await fetch(`${BASE_URL}/api/ats/score-file`, {
  method: "POST",
  body: form,
});
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "ATS score failed");
// data.score is 0-100
```

Python:

```python
import requests

with open("resume.pdf", "rb") as handle:
    response = requests.post(
        "http://127.0.0.1:9001/api/ats/score-file",
        files={"file": ("resume.pdf", handle, "application/pdf")},
    )
response.raise_for_status()
print(response.json()["score"], response.json()["grade"])
```

## File upload + job description

```bash
curl -X POST "{BASE_URL}/api/ats/score-file" ^
  -F "file=@C:\path\to\resume.pdf" ^
  -F "job_description=Job Title: Senior Backend Engineer. Requirements: Python, FastAPI, AWS, Docker"
```

JavaScript:

```js
const form = new FormData();
form.append("file", fileInput.files[0]);
form.append("job_description", jobPostingText);

const res = await fetch(`${BASE_URL}/api/ats/score-file`, {
  method: "POST",
  body: form,
});
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "ATS score failed");
// data.match.score is 0-100 keyword overlap
```

## JSON: resume text

```bash
curl -X POST "{BASE_URL}/api/ats/score" ^
  -H "Content-Type: application/json" ^
  -d "{\"resume_text\": \"Jane Doe\\nEmail: jane@example.com\\nSkills: Python, FastAPI, AWS\\n...\"}"
```

If the resume was already parsed with `/api/resume/parse`, send that JSON instead of raw text:

```js
const res = await fetch(`${BASE_URL}/api/ats/score`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    resume: {
      profile: parsed.profile,
      skills: parsed.skills,
      experience: parsed.experience,
      education: parsed.education,
      projects: parsed.projects,
      certifications: parsed.certifications,
    },
    job_description: jobPostingText, // optional
  }),
});
```

Optional: pass a scraped job id so required skills come from `/api/jobs`:

```json
{ "resume_text": "...", "job_id": "remotive-1680495" }
```

## Success response

HTTP `200`:

```json
{
  "ok": true,
  "score": 82,
  "grade": "B",
  "label": "Strong",
  "breakdown": {
    "parseability": 95,
    "contact": 100,
    "structure": 90,
    "content": 70,
    "job_match": 72
  },
  "checks": [
    { "id": "email", "ok": true, "message": "Email found." },
    { "id": "job_keywords", "ok": true, "message": "Resume keywords overlap this job (5 matched)." }
  ],
  "suggestions": [],
  "match": {
    "score": 72,
    "matched_skills": ["Python", "FastAPI", "AWS", "Docker"],
    "missing_skills": ["Kubernetes"],
    "extra_skills": ["Django"],
    "job_title": "Senior Backend Engineer",
    "company": "Northwind Labs"
  },
  "job": {
    "title": "Senior Backend Engineer",
    "required_skills": ["Python", "FastAPI", "PostgreSQL", "AWS", "Docker"]
  },
  "skills": ["Python", "FastAPI", "Django", "PostgreSQL", "AWS", "Docker"],
  "profile": {
    "full_name": "Jane Doe",
    "email": "jane.doe@example.com",
    "phone": "+1 (415) 555-0199",
    "location": "San Francisco, CA",
    "linkedin": "https://linkedin.com/in/janedoe",
    "github": "https://github.com/janedoe"
  },
  "source": {
    "filename": "resume.pdf",
    "content_type": "application/pdf",
    "pages": 1,
    "char_count": 4120
  }
}
```

`job` and `match` are `null` when no job description / `job_id` is sent. Then `breakdown.job_match` is also `null`, and `score` is based on parseability, contact, structure, and content only.

| Score | Grade | `label` |
| --- | --- | --- |
| 90–100 | A | Excellent |
| 80–89 | B | Strong |
| 70–79 | C | Good |
| 50–69 | D | Fair |
| 0–49 | F | Needs work |

Show `score` + `label` in the UI. Use `suggestions` as a to-do list. Use `checks[].ok` for green/red rows.

## Error responses

JSON `{ "detail": "..." }`.

| Status | When |
| --- | --- |
| 400 | Empty resume, text too short, or job description too short |
| 404 | `job_id` was sent but is not in the scraped job cache |
| 413 | Resume file larger than 8 MB |
| 415 | Unsupported resume file type |
| 422 | Resume file had no extractable text |

Example: `{ "detail": "Send a resume file, resume_text, or parsed resume JSON." }`

## CORS

CORS is open (`allow_origins=["*"]`), so a browser app on another origin can call this API directly.
