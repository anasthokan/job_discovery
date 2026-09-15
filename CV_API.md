# Job Description → CV API

Give this document to the frontend or backend developer who will call the API.

**Production base URL:** `http://74.208.184.175:517`

Local example: `http://127.0.0.1:9001`

Interactive try-out: [http://74.208.184.175:517/docs](http://74.208.184.175:517/docs)

Related parse API: `RESUME_API.md`

PDF ↔ DOCX convert API: `CONVERT_API.md`.

Sample files in this repo: `samples/sample-job-description.txt` and `samples/sample-resume.txt`.

## What it does

Send a job description. The API returns:

1. **`job`** — structured posting data (title, company, skills, responsibilities)
2. **`cv`** — CV JSON in the same shape as `/api/resume/parse`
3. **`cv_text`** — plain-text CV the UI can show or download
4. **`match`** — skill overlap score when a resume is also sent

Two modes:

| Mode | When | What you get |
| --- | --- | --- |
| `tailored` | Resume file, `resume_text`, or parsed `resume` JSON is sent | Existing CV facts only, reordered and summarized toward the job |
| `template` | Job description only | Placeholder CV (`[Your Name]`, suggested skills/bullets). Does **not** invent work history |

Missing skills stay in `match.missing_skills`. They are not added to `cv.skills`.

## Endpoints

| Method | Path | Use |
| --- | --- | --- |
| GET | `/api/health` | Liveness check (`cv_from_job: true`) |
| GET | `/api/cv` | Contract / modes |
| POST | `/api/cv/from-job` | JSON body (job text + optional resume) |
| POST | `/api/cv/from-job-file` | `multipart/form-data` (job text + optional resume file) |

Query flag: `include_cv_text=false` omits `cv_text`. Default is `true`.

You can identify the job with posting text, or with `job_id` from `/api/jobs` / `/api/scrape`.

## JSON: job description only (template CV)

```bash
curl -X POST "{BASE_URL}/api/cv/from-job" ^
  -H "Content-Type: application/json" ^
  -d "{\"job_description\": \"Job Title: Senior Backend Engineer\\nCompany: Northwind Labs\\nRequirements: Python, FastAPI, PostgreSQL, AWS, Docker\"}"
```

JavaScript:

```js
const res = await fetch(`${BASE_URL}/api/cv/from-job`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    job_description: jobPostingText,
    job_title: "Senior Backend Engineer",  // optional
    company: "Northwind Labs",             // optional
  }),
});
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "CV build failed");
// data.mode === "template"
// data.cv, data.job, data.cv_text
```

## JSON: tailor an existing resume

```js
const res = await fetch(`${BASE_URL}/api/cv/from-job`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    job_description: jobPostingText,
    resume_text: rawResumeText,
  }),
});
const data = await res.json();
// data.mode === "tailored"
// data.match.score is 0-100
```

If the resume was already parsed with `/api/resume/parse`, send that JSON instead of raw text:

```js
body: JSON.stringify({
  job_description: jobPostingText,
  resume: {
    profile: parsed.profile,
    skills: parsed.skills,
    experience: parsed.experience,
    education: parsed.education,
    projects: parsed.projects,
    certifications: parsed.certifications,
    languages: parsed.languages,
  },
})
```

Optional: pass a scraped job id so title, company, and listed skills are filled from cache:

```json
{ "job_id": "remotive-1680495", "resume_text": "..." }
```

Add `job_description` as well when you have the full posting text. `job_id` alone uses title + skills from `/api/jobs`.

## File upload

```bash
curl -X POST "{BASE_URL}/api/cv/from-job-file" ^
  -F "file=@C:\path\to\resume.pdf" ^
  -F "job_description=Job Title: Senior Backend Engineer. Requirements: Python, FastAPI, AWS, Docker"
```

JavaScript:

```js
const form = new FormData();
form.append("file", fileInput.files[0]);
form.append("job_description", jobPostingText);

const res = await fetch(`${BASE_URL}/api/cv/from-job-file`, {
  method: "POST",
  body: form,
});
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "CV build failed");
```

Python:

```python
import requests

with open("resume.pdf", "rb") as handle:
    response = requests.post(
        "http://127.0.0.1:9001/api/cv/from-job-file",
        files={"file": ("resume.pdf", handle, "application/pdf")},
        data={"job_description": open("samples/sample-job-description.txt", encoding="utf-8").read()},
    )
response.raise_for_status()
print(response.json()["mode"], response.json()["match"]["score"])
```

## Success response

HTTP `200`:

```json
{
  "ok": true,
  "mode": "tailored",
  "job": {
    "title": "Senior Backend Engineer",
    "company": "Northwind Labs",
    "location": "San Francisco, CA",
    "employment_type": "Full-time",
    "workplace_type": "Remote",
    "required_skills": ["Python", "FastAPI", "PostgreSQL", "AWS", "Docker"],
    "preferred_skills": ["Kubernetes", "Terraform", "Kafka"],
    "responsibilities": ["Design and ship FastAPI services used by millions of users"],
    "qualifications": ["5+ years of experience with Python, FastAPI, and SQL"],
    "keywords": ["Python", "FastAPI", "PostgreSQL"]
  },
  "match": {
    "score": 72,
    "matched_skills": ["Python", "FastAPI", "PostgreSQL", "AWS", "Docker"],
    "missing_skills": ["Kubernetes"],
    "extra_skills": ["Django"]
  },
  "cv": {
    "profile": {
      "full_name": "Jane Doe",
      "email": "jane.doe@example.com",
      "phone": "+1 (415) 555-0199",
      "phones": ["+1 (415) 555-0199"],
      "location": "San Francisco, CA",
      "linkedin": "https://linkedin.com/in/janedoe",
      "github": "https://github.com/janedoe",
      "website": null,
      "summary": "Backend engineer with 6 years of experience building APIs. Core strengths for this role include Python, FastAPI, PostgreSQL. Prepared for Senior Backend Engineer at Northwind Labs."
    },
    "skills": ["Python", "FastAPI", "PostgreSQL", "AWS", "Docker", "Django"],
    "experience": [
      {
        "title": "Senior Software Engineer",
        "company": "Acme Corp",
        "location": "San Francisco, CA",
        "start_date": "Jan 2022",
        "end_date": "Present",
        "is_current": true,
        "bullets": ["Built APIs used by 2 million users"]
      }
    ],
    "education": [],
    "projects": [],
    "certifications": [],
    "languages": []
  },
  "cv_text": "Jane Doe\nSan Francisco, CA | jane.doe@example.com | +1 (415) 555-0199\n..."
}
```

`cv` uses the same field names as `/api/resume/parse`, so the same UI components can render both.

## Error responses

| Status | When |
| --- | --- |
| 400 | Job description too short, or resume text too short |
| 404 | `job_id` was sent but is not in the scraped job cache |
| 413 | Resume file larger than 8 MB |
| 415 | Unsupported resume file type |
| 422 | Resume file had no extractable text |

`detail` is a string. Example: `{ "detail": "Job description is too short. Paste the posting text or provide a job title and skills." }`

## CORS

CORS is open (`allow_origins=["*"]`), so a browser app on another origin can call this API directly.
