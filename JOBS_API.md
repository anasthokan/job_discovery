# Jobs Listings + Skill Recommend API

Give this document to the frontend or backend developer who will call the API.

**Production base URL:** `http://74.208.184.175:517`

Local example: `http://127.0.0.1:9001`

Interactive try-out: [http://74.208.184.175:517/docs](http://74.208.184.175:517/docs)

Related APIs: `RESUME_API.md`, `ATS_API.md`, `CV_API.md`, `CONVERT_API.md`

## What it does

1. **Listings** — return US job listings (title, company, skills, location, board, apply URL)
2. **Scrape** — refresh from public job boards and **upsert into MySQL** (JSON file is a backup)
3. **Recommend** — rank those listings against candidate skills (or a resume)
4. **Schedule** — scrape runs **twice a day** (default 08:00 and 20:00 IST) so the database stays fresh

Listings come from MySQL when `.env` has `MYSQL_*` set, otherwise from the last `data/jobs.json` scrape. If `count` is `0`, scrape first (or call recommend with `refresh= true`).

US jobs only. Remote + named US states. Foreign listings are dropped.

## Endpoints

| Method | Path | Use |
| --- | --- | --- |
| GET | `/api/health` | Liveness (`jobs_list` and `jobs_recommend`) |
| GET | `/api/filters` | States, boards, and day options for dropdowns |
| GET | `/api/jobs` | Cached listings, filterable |
| POST | `/api/scrape` | Scrape boards, save to MySQL, return this run |
| GET | `/api/scrape/status` | MySQL + last scrape + next schedule |
| GET | `/api/recommend` | Contract |
| POST | `/api/recommend` | Rank listings from a skill list or resume JSON/text |
| POST | `/api/recommend/file` | Upload a resume file, then rank listings |

Scrape can take **20–60 seconds**. Recommend without `refresh` is fast (cache only).

## List cached jobs

```bash
curl "{BASE_URL}/api/jobs?keywords=python,fastapi&state=All&platform=All&days=7&limit=20"
```

JavaScript:

```js
const params = new URLSearchParams({
  keywords: "python,fastapi",
  state: "All",          // All | Remote | California | ...
  platform: "All",       // All | LinkedIn | Remotive | ...
  days: "30",
  city: "",
  limit: "20",
  offset: "0",
});
const res = await fetch(`${BASE_URL}/api/jobs?${params}`);
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "Jobs list failed");
// data.jobs, data.count, data.total
```

Query flags:

| Query | Default | Notes |
| --- | --- | --- |
| `keywords` | `""` | Comma-separated. **All** tokens must appear in title, company, location, or skills |
| `state` | `All` | `All`, `Remote`, or a US state name |
| `platform` | `All` | Exact board name from `/api/filters` |
| `days` | `30` | `1`, `3`, `7`, or `30` |
| `city` | `""` | Optional city substring |
| `limit` | omit | Max **200**. Omit to return every match |
| `offset` | `0` | Skip this many matches |

## Refresh listings (scrape)

```bash
curl -X POST "{BASE_URL}/api/scrape?keywords=python,react&state=All&platform=All&days=7"
```

`409` if another scrape is already running. `504` if it exceeds 5 minutes.

This run is upserted into MySQL (`job_discovery_jobs`) when `.env` has credentials. Duplicate URL/id rows are updated (`last_seen_at`), not inserted twice. This does not use an existing `jobs` table in the same database.

A background scheduler also calls the same scrape at **08:00 and 20:00** `Asia/Kolkata` (override with `SCRAPE_HOURS` / `SCRAPE_TZ` in `.env`).

Use this when the cache is empty or stale. Then call `/api/jobs` or `/api/recommend`.

## Recommend from skills

```bash
curl -X POST "{BASE_URL}/api/recommend" ^
  -H "Content-Type: application/json" ^
  -d "{\"skills\": [\"Python\", \"FastAPI\", \"AWS\", \"Docker\"], \"limit\": 20}"
```

JavaScript:

```js
const res = await fetch(`${BASE_URL}/api/recommend`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    skills: ["Python", "FastAPI", "PostgreSQL", "AWS", "Docker"],
    state: "All",
    platform: "All",
    days: 30,
    limit: 20,
    refresh: false, // true = scrape boards with these skills first (slow)
  }),
});
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "Recommend failed");
// data.jobs[0].match.score is 0-100
```

If the resume was already parsed with `/api/resume/parse`, send that JSON instead of a skill list:

```js
body: JSON.stringify({
  resume: {
    skills: parsed.skills,
  },
  limit: 20,
})
```

Or send raw resume text; skills are extracted the same way as `/api/resume/parse-text`:

```json
{ "resume_text": "Jane Doe\nSkills: Python, FastAPI, AWS, Docker\n..." }
```

`skills` wins if it is non-empty. Otherwise `resume.skills`, then `resume_text`.

## Recommend from a resume file

```bash
curl -X POST "{BASE_URL}/api/recommend/file" ^
  -F "file=@C:\path\to\resume.pdf" ^
  -F "limit=20"
```

JavaScript:

```js
const form = new FormData();
form.append("file", fileInput.files[0]);
form.append("limit", "20");
form.append("refresh", "false");

const res = await fetch(`${BASE_URL}/api/recommend/file`, {
  method: "POST",
  body: form,
});
const data = await res.json();
if (!res.ok) throw new Error(data.detail || "Recommend failed");
```

You can also send `skills` as form text (`Python, FastAPI, AWS`) without a file.

## Success response (listings)

HTTP `200`:

```json
{
  "jobs": [
    {
      "id": "remotive-1680495",
      "title": "Senior Backend Engineer",
      "company": "Northwind Labs",
      "skills": ["Python", "FastAPI", "AWS"],
      "state": "Remote",
      "location": "United States",
      "platform": "Remotive",
      "daysAgo": 2,
      "url": "https://...",
      "posted_at": "2026-09-15"
    }
  ],
  "count": 1,
  "total": 1,
  "limit": 20,
  "offset": 0,
  "scraped_at": "2026-09-17T05:00:00+00:00",
  "age_seconds": 120
}
```

Missing fields may be `null` or `[]`. `url` is the apply / posting link.

## Success response (recommend)

HTTP `200`:

```json
{
  "ok": true,
  "count": 1,
  "skills": ["Python", "FastAPI", "AWS", "Docker"],
  "jobs": [
    {
      "id": "remotive-1680495",
      "title": "Senior Backend Engineer",
      "company": "Northwind Labs",
      "skills": ["Python", "FastAPI", "AWS"],
      "state": "Remote",
      "location": "United States",
      "platform": "Remotive",
      "daysAgo": 2,
      "url": "https://...",
      "match": {
        "score": 75,
        "matched_skills": ["Python", "FastAPI", "AWS"],
        "missing_skills": ["Docker"]
      }
    }
  ],
  "hint": null,
  "scraped_at": "2026-09-17T05:00:00+00:00",
  "age_seconds": 120
}
```

Jobs are sorted by `match.score` (high first), then newest. `limit` max is **100**, default **25**.

`hint` is a string when the cache is empty or nothing overlapped the skills. Otherwise `null`.

Use `job.id` with `POST /api/cv/from-job` as `job_id` to tailor a CV to that listing.

## Error responses

JSON `{ "detail": "..." }`.

| Status | When |
| --- | --- |
| 400 | No skills, and resume / text had none |
| 409 | A scrape is already running (`refresh=true` or `/api/scrape`) |
| 413 | Resume file larger than 8 MB |
| 415 | Unsupported resume file type |
| 422 | Resume file had no extractable text |
| 504 | Scrape timed out |

Example: `{ "detail": "Send at least one skill, or a resume / resume_text that contains a skills section." }`

## CORS

CORS is open (`allow_origins=["*"]`), so a browser app on another origin can call this API directly.
