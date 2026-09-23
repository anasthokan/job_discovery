import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as UrlRequest, urlopen
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from jobscraper.ats_score import AtsScoreError, score_resume
from jobscraper.document_convert import (
    ConvertError,
    DOCX_TYPE,
    PDF_TYPE,
    docx_to_pdf,
    pdf_to_docx,
    preview_text_from_docx,
)
from jobscraper.jd_to_cv import JobToCvError, build_cv_from_job
from jobscraper.locations import DROPDOWN_STATES, is_usa_job, matches_city_filter, matches_state_filter
from jobscraper.recommend import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    normalize_skills,
    parse_skill_query,
    recommend_jobs,
)
from jobscraper.resume_parser import ResumeParseError, parse_resume_bytes, parse_resume_text
from jobscraper.db import (
    close_conn as close_mysql,
    finish_scrape_run,
    health as mysql_health,
    import_json_if_empty,
    init_db as init_mysql,
    job_count as mysql_job_count,
    last_scrape_meta,
    load_jobs as load_jobs_mysql,
    mysql_configured,
    start_scrape_run,
    upsert_jobs,
)

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DATA_FILE = ROOT / "data" / "jobs.json"
INDEX_FILE = ROOT / "index.html"
DASHBOARD_FILE = ROOT / "dashboard.html"
SCRAPE_LOCK = threading.Lock()
SCRAPE_TIMEOUT = 360
log = logging.getLogger("job-discovery")
USAJOBS_CACHE_TTL = 3600
USAJOBS_CODELISTS = {
    "agencies": {
        "url": "https://data.usajobs.gov/api/codelist/agencysubelements",
        "kind": "agency",
        "search_param": "a",
    },
    "series": {
        "url": "https://data.usajobs.gov/api/codelist/occupationalseries",
        "kind": "series",
        "search_param": "j",
    },
}
USAJOBS_AGENCIES_URL = USAJOBS_CODELISTS["agencies"]["url"]
_USAJOBS_LOCK = threading.Lock()
_USAJOBS_CACHE: dict[str, dict] = {}
_SCHEDULER: BackgroundScheduler | None = None


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _truthy(name: str, default: str = "true") -> bool:
    return _env(name, default).lower() not in {"0", "false", "no", "off"}


def _schedule_hours() -> list[int]:
    hours = []
    for part in (_env("SCRAPE_HOURS", "8,20") or "8,20").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour = int(part)
        except ValueError:
            continue
        if 0 <= hour <= 23:
            hours.append(hour)
    return hours or [8, 20]


def scheduled_scrape() -> None:
    if not SCRAPE_LOCK.acquire(blocking=False):
        log.warning("Scheduled scrape skipped: another scrape is running")
        return
    try:
        keywords = _env("SCRAPE_KEYWORDS", "")
        state = _env("SCRAPE_STATE", "All") or "All"
        platform = _env("SCRAPE_PLATFORM", "All") or "All"
        city = _env("SCRAPE_CITY", "")
        log.info("Scheduled scrape starting keywords=%s", keywords)
        run_spider(keywords, state, platform, city)
        log.info("Scheduled scrape finished")
    except Exception:
        log.exception("Scheduled scrape failed")
    finally:
        SCRAPE_LOCK.release()


def _start_scheduler() -> BackgroundScheduler | None:
    if not _truthy("SCRAPE_SCHEDULE_ENABLED", "true"):
        log.info("Scrape schedule disabled")
        return None
    tz_name = _env("SCRAPE_TZ", "Asia/Kolkata") or "Asia/Kolkata"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        log.warning("Invalid SCRAPE_TZ=%s, using UTC", tz_name)
        tz_name = "UTC"
        tz = ZoneInfo("UTC")
    hours = _schedule_hours()
    scheduler = BackgroundScheduler(timezone=tz)
    for hour in hours:
        scheduler.add_job(
            scheduled_scrape,
            "cron",
            hour=hour,
            minute=0,
            id=f"scrape-{hour}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    scheduler.start()
    log.info("Scrape schedule: %s:00 %s", hours, tz_name)
    return scheduler


@asynccontextmanager
async def lifespan(_app):
    global _SCHEDULER
    init_mysql()
    import_json_if_empty(DATA_FILE)
    _SCHEDULER = _start_scheduler()
    yield
    if _SCHEDULER is not None:
        _SCHEDULER.shutdown(wait=False)
        _SCHEDULER = None
    close_mysql()


app = FastAPI(
    title="Job Discovery",
    description="Job listings API, skill-based job recommendations, resume/CV parse, ATS resume score, job-description-to-CV, PDF/DOCX conversion, and MySQL job storage.",
    version="1.6.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "null"],
    allow_origin_regex=".*",
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResumeTextRequest(BaseModel):
    text: str = Field(..., min_length=40, description="Raw resume / CV text")


class ResumeSource(BaseModel):
    filename: str | None = None
    content_type: str | None = None
    pages: int | None = None
    char_count: int


class ResumeProfile(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    phones: list[str] = []
    location: str | None = None
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None
    summary: str | None = None


class ResumeExperience(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    bullets: list[str] = []


class ResumeEducation(BaseModel):
    degree: str | None = None
    field: str | None = None
    institution: str | None = None
    start_year: str | None = None
    end_year: str | None = None
    gpa: str | None = None


class ResumeProject(BaseModel):
    name: str
    description: str | None = None


class ResumeParseResponse(BaseModel):
    ok: bool = True
    source: ResumeSource
    profile: ResumeProfile
    skills: list[str]
    experience: list[ResumeExperience]
    education: list[ResumeEducation]
    projects: list[ResumeProject]
    certifications: list[str]
    languages: list[str]
    raw_text: str | None = None


class ResumeInput(BaseModel):
    profile: ResumeProfile | None = None
    skills: list[str] = []
    experience: list[ResumeExperience] = []
    education: list[ResumeEducation] = []
    projects: list[ResumeProject] = []
    certifications: list[str] = []
    languages: list[str] = []


class CvFromJobRequest(BaseModel):
    job_description: str = Field("", description="Full job posting text")
    job_title: str | None = Field(None, description="Optional override if the posting title is known")
    company: str | None = None
    location: str | None = None
    job_id: str | None = Field(None, description="Optional scraped job id from /api/jobs")
    resume_text: str | None = Field(None, description="Optional raw resume text to tailor")
    resume: ResumeInput | None = Field(None, description="Optional parsed resume JSON from /api/resume/parse")


class JobParse(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    required_skills: list[str] = []
    preferred_skills: list[str] = []
    responsibilities: list[str] = []
    qualifications: list[str] = []
    keywords: list[str] = []


class MatchReport(BaseModel):
    score: int | None = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    extra_skills: list[str] = []


class TailoredCv(BaseModel):
    profile: ResumeProfile
    skills: list[str]
    experience: list[ResumeExperience]
    education: list[ResumeEducation]
    projects: list[ResumeProject]
    certifications: list[str]
    languages: list[str]


class CvFromJobResponse(BaseModel):
    ok: bool = True
    mode: str
    job: JobParse
    match: MatchReport
    cv: TailoredCv
    cv_text: str | None = None


class RecommendRequest(BaseModel):
    skills: list[str] = Field(default_factory=list, description="Candidate skills, e.g. Python, FastAPI, AWS")
    resume_text: str | None = Field(None, description="Optional raw resume text; skills are extracted from it")
    resume: ResumeInput | None = Field(None, description="Optional parsed resume JSON from /api/resume/parse")
    state: str = Field("All", description="US state filter, Remote, or All")
    platform: str = Field("All", description="Job board name, or All")
    days: int = Field(30, description="Only listings posted within this many days")
    city: str = ""
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    refresh: bool = Field(False, description="If true, scrape boards with these skills before ranking")


class AtsScoreRequest(BaseModel):
    resume_text: str | None = Field(None, description="Raw resume / CV text")
    resume: ResumeInput | None = Field(None, description="Optional parsed resume JSON from /api/resume/parse")
    job_description: str = Field("", description="Optional job posting text to score keyword match")
    job_title: str | None = None
    company: str | None = None
    location: str | None = None
    job_id: str | None = Field(None, description="Optional scraped job id from /api/jobs")


class AtsCheck(BaseModel):
    id: str
    ok: bool
    message: str


class AtsBreakdown(BaseModel):
    parseability: int
    contact: int
    structure: int
    content: int
    job_match: int | None = None


class AtsMatch(BaseModel):
    score: int | None = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    extra_skills: list[str] = []
    job_title: str | None = None
    company: str | None = None


class AtsProfile(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin: str | None = None
    github: str | None = None


class AtsScoreResponse(BaseModel):
    ok: bool = True
    score: int
    grade: str
    label: str
    job_title: str | None = None
    matched_keywords: list[str] = []
    suggested_additions: list[str] = []
    breakdown: AtsBreakdown
    checks: list[AtsCheck]
    suggestions: list[str]
    match: AtsMatch | None = None
    job: JobParse | None = None
    skills: list[str] = []
    profile: AtsProfile
    source: ResumeSource


def load_jobs_json() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        text = DATA_FILE.read_text(encoding="utf-8").replace("\x00", "").strip()
        if not text:
            return []
        payload, _ = json.JSONDecoder().raw_decode(text)
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def load_jobs() -> list[dict]:
    if mysql_configured():
        rows = load_jobs_mysql()
        if rows:
            return rows
    return load_jobs_json()


def cache_meta() -> dict:
    if mysql_configured():
        meta = last_scrape_meta()
        if meta.get("scraped_at"):
            return {"scraped_at": meta["scraped_at"], "age_seconds": meta["age_seconds"]}
    if not DATA_FILE.exists():
        return {"scraped_at": None, "age_seconds": None}
    mtime = DATA_FILE.stat().st_mtime
    scraped_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    age = datetime.now(timezone.utc).timestamp() - mtime
    return {"scraped_at": scraped_at, "age_seconds": int(age)}


def parse_keywords(raw: str) -> list[str]:
    return [part.strip().lower() for part in (raw or "").split(",") if part.strip()]


def _days_ago_from_iso(value: str | None) -> int:
    if not value:
        return 0
    cleaned = str(value).strip().replace("Z", "+00:00")
    try:
        posted = datetime.fromisoformat(cleaned)
    except ValueError:
        return 0
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - posted).total_seconds() // 86400))


def _codelist_to_job(row: dict, *, kind: str, search_param: str) -> dict:
    code = str(row.get("Code") or "").strip()
    name = str(row.get("Value") or "").strip() or f"Unknown {kind}"
    parent = str(row.get("ParentCode") or "").strip()
    acronym = str(row.get("Acronym") or "").strip()
    family = str(row.get("JobFamily") or "").strip()
    modified = str(row.get("LastModified") or "").strip()
    company = acronym or parent or (f"Series {code}" if kind == "series" and code else "USAJobs")
    skills = [item for item in [code, parent, acronym, family] if item]
    query = f"{search_param}={quote(code)}" if code else ""
    return {
        "id": f"usajobs-{kind}-{code}",
        "title": name,
        "company": company,
        "skills": skills,
        "state": "United States",
        "location": "United States",
        "platform": "USAJobs",
        "list_type": kind,
        "daysAgo": _days_ago_from_iso(modified),
        "url": f"https://www.usajobs.gov/Search/Results?{query}" if query else "https://www.usajobs.gov/",
        "posted_at": modified or None,
        "agency_code": code if kind == "agency" else None,
        "parent_code": parent or None,
        "series_code": code if kind == "series" else None,
        "job_family": family or None,
    }


def _fetch_usajobs_json(url: str) -> dict:
    req = UrlRequest(
        url,
        headers={
            "User-Agent": "JobDiscovery/1.5 (dashboard test bind)",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"USAJobs returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail=f"USAJobs request failed: {exc.reason}.") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="USAJobs returned invalid JSON.") from exc


def fetch_usajobs_codelist(name: str, *, refresh: bool = False) -> list[dict]:
    spec = USAJOBS_CODELISTS.get(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Unknown USAJobs list '{name}'.")
    now = datetime.now(timezone.utc).timestamp()
    with _USAJOBS_LOCK:
        cached = _USAJOBS_CACHE.get(name) or {"fetched_at": 0.0, "jobs": []}
        age = now - float(cached.get("fetched_at") or 0)
        if cached.get("jobs") and not refresh and age < USAJOBS_CACHE_TTL:
            return cached["jobs"]
    payload = _fetch_usajobs_json(spec["url"])
    values = []
    for block in payload.get("CodeList") or []:
        if isinstance(block, dict):
            values.extend(block.get("ValidValue") or [])
    jobs = [
        _codelist_to_job(row, kind=spec["kind"], search_param=spec["search_param"])
        for row in values
        if isinstance(row, dict) and str(row.get("IsDisabled") or "No").lower() != "yes"
    ]
    with _USAJOBS_LOCK:
        _USAJOBS_CACHE[name] = {"fetched_at": now, "jobs": jobs}
    return jobs


def fetch_usajobs_agencies(*, refresh: bool = False) -> list[dict]:
    return fetch_usajobs_codelist("agencies", refresh=refresh)


def _filter_usajobs_jobs(jobs: list[dict], keywords: str) -> list[dict]:
    tokens = parse_keywords(keywords)
    if not tokens:
        return jobs
    return [
        job
        for job in jobs
        if all(
            token
            in " ".join(
                [
                    str(job.get("title") or ""),
                    str(job.get("company") or ""),
                    " ".join(job.get("skills") or []),
                ]
            ).lower()
            for token in tokens
        )
    ]


def _usajobs_list_payload(
    name: str,
    *,
    keywords: str = "",
    refresh: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    jobs = _filter_usajobs_jobs(fetch_usajobs_codelist(name, refresh=refresh), keywords)
    page = jobs[offset:] if limit is None else jobs[offset : offset + limit]
    return {
        "ok": True,
        "source": USAJOBS_CODELISTS[name]["url"],
        "list": name,
        "jobs": page,
        "count": len(page),
        "total": len(jobs),
        "limit": limit,
        "offset": offset,
    }


def filter_jobs(
    jobs: list[dict],
    keywords: list[str],
    state: str,
    platform: str,
    days: int,
    city: str = "",
    usa_only: bool = True,
) -> list[dict]:
    filtered = []
    for job in jobs:
        if usa_only and not is_usa_job(job.get("state") or "", job.get("location") or ""):
            continue
        if platform != "All" and job.get("platform") != platform:
            continue
        if int(job.get("daysAgo") or 0) > days:
            continue
        if not matches_state_filter(job.get("state") or "", job.get("location") or "", state):
            continue
        if not matches_city_filter(job.get("location") or "", city):
            continue
        blob = " ".join(
            [
                str(job.get("title") or ""),
                str(job.get("company") or ""),
                str(job.get("location") or ""),
                " ".join(job.get("skills") or []),
            ]
        ).lower()
        if keywords and not all(keyword in blob for keyword in keywords):
            continue
        filtered.append(job)
    filtered.sort(key=lambda row: (row.get("daysAgo") or 0, row.get("title") or ""))
    return filtered


def _page(jobs: list[dict], limit: int | None, offset: int) -> list[dict]:
    start = max(offset, 0)
    if limit is None:
        return jobs[start:]
    return jobs[start : start + max(limit, 0)]


def _empty_cache_hint() -> str:
    return (
        "No listings in cache. POST /api/scrape?keywords=python,fastapi first, "
        "wait for the twice-daily scrape, or call /api/recommend with refresh=true."
    )


def _skills_from_inputs(
    skills: list[str] | None = None,
    resume_text: str | None = None,
    resume: dict | None = None,
) -> list[str]:
    found = normalize_skills(skills)
    if found:
        return found
    if resume:
        found = normalize_skills(resume.get("skills") or [])
        if found:
            return found
    if resume_text and resume_text.strip():
        try:
            parsed = parse_resume_text(resume_text)
        except ResumeParseError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        found = normalize_skills(parsed.get("skills") or [])
    if not found:
        raise HTTPException(
            status_code=400,
            detail="Send at least one skill, or a resume / resume_text that contains a skills section.",
        )
    return found


def _recommend_payload(
    *,
    skills: list[str],
    state: str,
    platform: str,
    days: int,
    city: str,
    limit: int,
    refresh: bool,
) -> dict:
    if refresh:
        if not SCRAPE_LOCK.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A scrape is already running. Try again in a moment.")
        try:
            run_spider(",".join(skills), state, platform, city)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Job scrape timed out.") from exc
        finally:
            SCRAPE_LOCK.release()
    filtered = filter_jobs(load_jobs(), [], state, platform, days, city)
    ranked = recommend_jobs(filtered, skills, limit=limit)
    hint = None
    if not filtered:
        hint = _empty_cache_hint()
    elif not ranked:
        hint = "Listings were found, but none overlapped these skills. Try refresh=true or broader skills."
    return {
        "ok": True,
        "count": len(ranked),
        "skills": skills,
        "jobs": ranked,
        "hint": hint,
        **cache_meta(),
    }


def run_spider(keywords: str, location: str, platform: str, city: str = "") -> None:
    DATA_FILE.parent.mkdir(exist_ok=True)
    if DATA_FILE.exists():
        DATA_FILE.unlink()
    run_id = start_scrape_run(keywords, location, platform)
    cmd = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        "jobs",
        "-a",
        f"keywords={keywords}",
        "-a",
        f"location={location}",
        "-a",
        f"city={city}",
        "-a",
        f"platform={platform}",
        "-s",
        "LOG_LEVEL=WARNING",
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=SCRAPE_TIMEOUT,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        finish_scrape_run(run_id, job_count_value=0, error="timeout")
        raise
    except Exception as exc:
        finish_scrape_run(run_id, job_count_value=0, error=str(exc)[:2000])
        raise
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Scrapy crawl failed").strip()
        finish_scrape_run(run_id, job_count_value=0, error=detail[-2000:])
        raise HTTPException(status_code=502, detail=detail[-2000:])
    this_run = load_jobs_json()
    try:
        saved = upsert_jobs(this_run)
    except Exception as exc:
        detail = f"MySQL save failed: {exc}"
        finish_scrape_run(run_id, job_count_value=0, error=detail[:2000])
        raise HTTPException(status_code=502, detail=detail[:2000]) from exc
    finish_scrape_run(run_id, job_count_value=len(this_run) or saved)


def _resume_response(parsed: dict, include_raw: bool) -> dict:
    if not include_raw:
        parsed = {**parsed, "raw_text": None}
    return parsed


def _parse_or_http(data: bytes, filename: str, content_type: str) -> dict:
    try:
        return parse_resume_bytes(data, filename=filename, content_type=content_type)
    except ResumeParseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _swap_extension(filename: str, extension: str) -> str:
    stem = Path(filename or "converted").stem.strip() or "converted"
    stem = re.sub(r"[^\w.\-]+", "_", stem).strip("._") or "converted"
    return f"{stem}{extension}"


def _file_download(content: bytes, filename: str, media_type: str) -> Response:
    encoded = quote(filename)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded}",
        },
    )


def _html_preview(title: str, text: str) -> HTMLResponse:
    body = html.escape(text or "").replace("\n", "<br>\n")
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Calibri, Arial, sans-serif; max-width: 760px; margin: 24px auto; color: #0f172a; line-height: 1.5; }}
    h1 {{ font-size: 1rem; color: #64748b; font-weight: 600; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <div>{body}</div>
</body>
</html>"""
    return HTMLResponse(page)


def _extract_multipart_file(data: bytes) -> tuple[bytes, str, str] | None:
    """Pull the first file part out of a raw multipart body (wrong Content-Type)."""
    stripped = data.lstrip()
    if not stripped.startswith(b"--"):
        return None
    boundary, _, _rest = stripped.partition(b"\n")
    boundary = boundary.strip().rstrip(b"\r")
    if len(boundary) < 4:
        return None
    for part in stripped.split(boundary):
        part = part.lstrip(b"\r\n")
        if b"filename=" not in part[:1200]:
            continue
        header, sep, body = part.partition(b"\r\n\r\n")
        if not sep:
            header, sep, body = part.partition(b"\n\n")
        if not sep:
            continue
        match = re.search(br'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', header, re.I)
        filename = match.group(1).decode("latin-1", "ignore").strip() if match else "upload"
        ctype_match = re.search(br"Content-Type:\s*([^\r\n]+)", header, re.I)
        ctype = ctype_match.group(1).decode("ascii", "ignore").strip() if ctype_match else ""
        body = body.rstrip(b"\r\n")
        if body.endswith(b"--"):
            body = body[:-2].rstrip(b"\r\n")
        if body:
            return body, filename, ctype
    return None


def _filename_from_bytes(data: bytes, content_type: str, fallback: str) -> str:
    header_name = fallback.strip()
    if header_name:
        return header_name
    if data.startswith(b"%PDF"):
        return "upload.pdf"
    if data[:2] == b"PK":
        return "upload.docx"
    if (content_type or "").startswith("text/"):
        return "upload.txt"
    return "upload.bin"


async def _load_resume_upload(request: Request, file: UploadFile | None) -> tuple[bytes, str, str]:
    if file is not None:
        data = await file.read()
        if data:
            return data, file.filename or "resume", file.content_type or ""

    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        for value in form.values():
            filename = getattr(value, "filename", None)
            if not filename:
                continue
            data = await value.read()
            if data:
                return data, filename, getattr(value, "content_type", "") or ""

    data = await request.body()
    extracted = _extract_multipart_file(data) if data else None
    if extracted:
        return extracted
    if data:
        filename = _filename_from_bytes(
            data,
            content_type,
            request.headers.get("x-filename") or "",
        )
        return data, filename, content_type.split(";")[0].strip()

    raise HTTPException(
        status_code=400,
        detail=(
            "No file received. In Postman use Body → form-data, key=file, type=File, "
            "and uncheck Headers → Content-Type. Or use Body → binary with "
            "Content-Type application/pdf."
        ),
    )


@app.get("/api/health")
def health():
    mysql = mysql_health()
    return {
        "ok": True,
        "service": "job-discovery",
        "jobs_list": True,
        "jobs_recommend": True,
        "usajobs_agencies": True,
        "usajobs_series": True,
        "resume_parse": True,
        "ats_score": True,
        "cv_from_job": True,
        "pdf_docx_convert": True,
        "resume_formats": ["pdf", "docx", "txt"],
        "mysql": mysql,
        "scrape_schedule": {
            "enabled": _truthy("SCRAPE_SCHEDULE_ENABLED", "true"),
            "tz": _env("SCRAPE_TZ", "Asia/Kolkata") or "Asia/Kolkata",
            "hours": _schedule_hours(),
        },
    }


@app.get("/")
def home():
    return FileResponse(INDEX_FILE)


@app.get("/dashboard")
@app.get("/dashboard.html")
def dashboard_page():
    return FileResponse(DASHBOARD_FILE)


@app.get("/api/filters")
def filters():
    return {
        "states": ["All", "Remote", *DROPDOWN_STATES],
        "platforms": [
            "All",
            "RemoteOK",
            "Remotive",
            "Jobicy",
            "The Muse",
            "Arbeitnow",
            "Himalayas",
            "We Work Remotely",
            "Remote.co",
            "Greenhouse",
            "Lever",
            "Ashby",
            "SmartRecruiters",
            "Workable",
            "LinkedIn",
            "Dice",
            "Y Combinator",
            "Hacker News",
            "USAJobs",
        ],
        "days": [1, 3, 7, 30],
    }


@app.get("/api/jobs")
def get_jobs(
    keywords: str = Query("", description="Comma-separated title/company/skill keywords"),
    state: str = Query("All"),
    platform: str = Query("All"),
    days: int = Query(30),
    city: str = Query(""),
    limit: int | None = Query(None, ge=1, le=200, description="Optional page size. Omit to return all matches."),
    offset: int = Query(0, ge=0),
):
    jobs = filter_jobs(load_jobs(), parse_keywords(keywords), state, platform, days, city)
    page = _page(jobs, limit, offset)
    return {
        "jobs": page,
        "count": len(page),
        "total": len(jobs),
        "limit": limit,
        "offset": offset,
        "source": "mysql" if mysql_configured() else "json",
        **cache_meta(),
    }


@app.get("/api/usajobs/agencies")
def usajobs_agencies(
    keywords: str = Query("", description="Comma-separated agency name / code / acronym filter"),
    refresh: bool = Query(False, description="Bypass the 1-hour in-memory cache"),
    limit: int | None = Query(None, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Test bind: USAJobs agency subelements mapped into the dashboard job-card shape."""
    return _usajobs_list_payload("agencies", keywords=keywords, refresh=refresh, limit=limit, offset=offset)


@app.get("/api/usajobs/series")
def usajobs_series(
    keywords: str = Query("", description="Comma-separated series name / code / job-family filter"),
    refresh: bool = Query(False, description="Bypass the 1-hour in-memory cache"),
    limit: int | None = Query(None, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Test bind: USAJobs occupational series mapped into the dashboard job-card shape."""
    return _usajobs_list_payload("series", keywords=keywords, refresh=refresh, limit=limit, offset=offset)


@app.post("/api/scrape")
def scrape_jobs(
    keywords: str = Query(""),
    state: str = Query("All"),
    platform: str = Query("All"),
    days: int = Query(30),
    city: str = Query(""),
):
    if not SCRAPE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A scrape is already running. Try again in a moment.")
    try:
        run_spider(keywords, state, platform, city)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Job scrape timed out.") from exc
    finally:
        SCRAPE_LOCK.release()
    jobs = [
        job
        for job in load_jobs_json()
        if is_usa_job(job.get("state") or "", job.get("location") or "")
    ]
    return {
        "jobs": jobs,
        "count": len(jobs),
        "saved_to": "mysql" if mysql_configured() else "json",
        "db_total": mysql_job_count() if mysql_configured() else len(jobs),
        **cache_meta(),
    }


@app.get("/api/scrape/status")
def scrape_status():
    return {
        "ok": True,
        "running": SCRAPE_LOCK.locked(),
        "mysql": mysql_health(),
        "schedule": {
            "enabled": _truthy("SCRAPE_SCHEDULE_ENABLED", "true"),
            "tz": _env("SCRAPE_TZ", "Asia/Kolkata") or "Asia/Kolkata",
            "hours": _schedule_hours(),
            "keywords": _env("SCRAPE_KEYWORDS", ""),
            "state": _env("SCRAPE_STATE", "All") or "All",
            "platform": _env("SCRAPE_PLATFORM", "All") or "All",
        },
        **cache_meta(),
    }


@app.get("/api/recommend")
def recommend_contract():
    return {
        "ok": True,
        "service": "jobs-recommend",
        "endpoints": {
            "listings": {
                "method": "GET",
                "path": "/api/jobs",
                "query": {
                    "keywords": "python,fastapi",
                    "state": "All",
                    "platform": "All",
                    "days": 30,
                    "city": "",
                    "limit": 50,
                    "offset": 0,
                },
            },
            "scrape": {
                "method": "POST",
                "path": "/api/scrape",
                "query": {
                    "keywords": "python,fastapi",
                    "state": "All",
                    "platform": "All",
                    "days": 30,
                    "city": "",
                },
            },
            "recommend": {
                "method": "POST",
                "path": "/api/recommend",
                "content_type": "application/json",
                "body": {
                    "skills": ["Python", "FastAPI", "AWS"],
                    "resume_text": "optional raw resume text",
                    "resume": "optional parsed resume JSON",
                    "state": "All",
                    "platform": "All",
                    "days": 30,
                    "limit": 25,
                    "refresh": False,
                },
            },
            "recommend_file": {
                "method": "POST",
                "path": "/api/recommend/file",
                "content_type": "multipart/form-data",
                "fields": {
                    "file": "optional resume PDF/DOCX/TXT",
                    "skills": "optional comma-separated skills",
                    "state": "All",
                    "platform": "All",
                    "days": 30,
                    "limit": 25,
                    "refresh": False,
                },
            },
        },
        "docs": "/docs",
    }


@app.post(
    "/api/recommend",
    summary="Recommend job listings from skills or a resume",
)
def recommend_from_json(body: RecommendRequest):
    resume_payload = body.resume.model_dump() if body.resume else None
    skills = _skills_from_inputs(body.skills, body.resume_text, resume_payload)
    return _recommend_payload(
        skills=skills,
        state=body.state,
        platform=body.platform,
        days=body.days,
        city=body.city,
        limit=body.limit,
        refresh=body.refresh,
    )


@app.post(
    "/api/recommend/file",
    summary="Upload a resume and recommend matching job listings",
)
async def recommend_from_file(
    file: UploadFile | None = File(None, description="Optional resume file: PDF, DOCX, or TXT"),
    skills: str = Form("", description="Optional comma-separated skills"),
    state: str = Form("All"),
    platform: str = Form("All"),
    days: int = Form(30),
    city: str = Form(""),
    limit: int = Form(DEFAULT_LIMIT),
    refresh: bool = Form(False),
):
    listed = parse_skill_query(skills)
    resume_payload = None
    if file is not None:
        data = await file.read()
        if data:
            resume_payload = _parse_or_http(data, file.filename or "resume", file.content_type or "")
    if not listed and resume_payload is None:
        raise HTTPException(
            status_code=400,
            detail="Upload a resume file or send skills as comma-separated text.",
        )
    found = _skills_from_inputs(listed, None, resume_payload)
    return _recommend_payload(
        skills=found,
        state=state,
        platform=platform,
        days=days,
        city=city,
        limit=limit,
        refresh=refresh,
    )


@app.get("/api/resume")
def resume_contract():
    return {
        "ok": True,
        "service": "resume-parse",
        "max_upload_bytes": 8 * 1024 * 1024,
        "formats": ["pdf", "docx", "txt"],
        "endpoints": {
            "parse_file": {
                "method": "POST",
                "path": "/api/resume/parse",
                "content_type": "multipart/form-data",
                "field": "file",
                "query": {"include_raw": "false"},
            },
            "parse_text": {
                "method": "POST",
                "path": "/api/resume/parse-text",
                "content_type": "application/json",
                "body": {"text": "resume text"},
                "query": {"include_raw": "false"},
            },
        },
        "docs": "/docs",
    }


@app.post(
    "/api/resume/parse",
    response_model=ResumeParseResponse,
    summary="Parse a resume / CV file",
)
async def parse_resume_file(
    request: Request,
    file: UploadFile | None = File(None, description="Resume file: PDF, DOCX, or TXT"),
    include_raw: bool = Query(False, description="Include extracted raw_text in the response"),
):
    data, filename, content_type = await _load_resume_upload(request, file)
    parsed = _parse_or_http(data, filename, content_type)
    return _resume_response(parsed, include_raw)


@app.post(
    "/api/resume/scrape",
    response_model=ResumeParseResponse,
    summary="Alias of /api/resume/parse",
    include_in_schema=False,
)
async def scrape_resume_file(
    request: Request,
    file: UploadFile | None = File(None, description="Resume file: PDF, DOCX, or TXT"),
    include_raw: bool = Query(False),
):
    return await parse_resume_file(request, file, include_raw)


@app.post(
    "/api/resume/parse-text",
    response_model=ResumeParseResponse,
    summary="Parse pasted resume / CV text",
)
def parse_resume_from_text(
    body: ResumeTextRequest,
    include_raw: bool = Query(False, description="Include extracted raw_text in the response"),
):
    try:
        parsed = parse_resume_text(body.text)
    except ResumeParseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _resume_response(parsed, include_raw)


def _job_from_cache(job_id: str | None) -> dict | None:
    if not job_id:
        return None
    wanted = job_id.strip().lower()
    for job in load_jobs():
        if str(job.get("id") or "").lower() == wanted:
            return job
    raise HTTPException(status_code=404, detail=f"No scraped job found for id '{job_id}'.")


def _ats_or_http(
    *,
    resume_text: str | None = None,
    resume: dict | None = None,
    job_description: str = "",
    job_title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    job_id: str | None = None,
) -> dict:
    cached = _job_from_cache(job_id)
    extra_skills = None
    if cached:
        job_title = job_title or cached.get("title")
        company = company or cached.get("company")
        location = location or cached.get("location")
        extra_skills = list(cached.get("skills") or []) or None
        if not (job_description or "").strip() and extra_skills:
            job_description = (
                f"{cached.get('title') or 'Open role'}\n"
                f"Company: {cached.get('company') or ''}\n"
                f"Location: {cached.get('location') or ''}\n"
                f"Required skills: {', '.join(extra_skills)}"
            )
    try:
        return score_resume(
            resume_text=resume_text,
            resume=resume,
            job_description=job_description,
            job_title=job_title,
            company=company,
            location=location,
            job_skills=extra_skills,
        )
    except (AtsScoreError, JobToCvError, ResumeParseError) as exc:
        status = getattr(exc, "status_code", 400)
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/api/ats")
def ats_contract():
    return {
        "ok": True,
        "service": "ats-score",
        "max_upload_bytes": 8 * 1024 * 1024,
        "formats": ["pdf", "docx", "txt"],
        "score": "0-100",
        "grades": ["A", "B", "C", "D", "F"],
        "endpoints": {
            "score": {
                "method": "POST",
                "path": "/api/ats/score",
                "content_type": "application/json",
                "body": {
                    "resume_text": "resume text",
                    "resume": "optional parsed resume JSON",
                    "job_description": "optional job posting text",
                    "job_id": "optional scraped job id",
                },
            },
            "score_file": {
                "method": "POST",
                "path": "/api/ats/score-file",
                "content_type": "multipart/form-data",
                "fields": {
                    "file": "resume PDF/DOCX/TXT",
                    "job_description": "optional job posting text",
                    "job_id": "optional scraped job id",
                },
            },
        },
        "docs": "/docs",
    }


@app.post(
    "/api/ats/score",
    response_model=AtsScoreResponse,
    summary="Get an ATS score from resume text or parsed JSON",
)
def ats_score_from_json(body: AtsScoreRequest):
    resume_payload = body.resume.model_dump() if body.resume else None
    return _ats_or_http(
        resume_text=body.resume_text,
        resume=resume_payload,
        job_description=body.job_description,
        job_title=body.job_title,
        company=body.company,
        location=body.location,
        job_id=body.job_id,
    )


@app.post(
    "/api/ats/score-file",
    response_model=AtsScoreResponse,
    summary="Upload a resume and get an ATS score",
)
async def ats_score_from_file(
    request: Request,
    file: UploadFile | None = File(None, description="Resume file: PDF, DOCX, or TXT"),
    job_description: str = Form("", description="Optional job posting text"),
    job_title: str | None = Form(None),
    company: str | None = Form(None),
    location: str | None = Form(None),
    job_id: str | None = Form(None),
):
    data, filename, content_type = await _load_resume_upload(request, file)
    parsed = _parse_or_http(data, filename, content_type)
    return _ats_or_http(
        resume=parsed,
        job_description=job_description,
        job_title=job_title,
        company=company,
        location=location,
        job_id=job_id,
    )


def _build_cv_or_http(
    *,
    job_description: str = "",
    job_title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    job_id: str | None = None,
    resume_text: str | None = None,
    resume: dict | None = None,
    include_cv_text: bool = True,
) -> dict:
    cached = _job_from_cache(job_id)
    extra_skills = None
    if cached:
        job_title = job_title or cached.get("title")
        company = company or cached.get("company")
        location = location or cached.get("location")
        extra_skills = list(cached.get("skills") or []) or None
        if not (job_description or "").strip() and extra_skills:
            job_description = (
                f"{cached.get('title') or 'Open role'}\n"
                f"Company: {cached.get('company') or ''}\n"
                f"Location: {cached.get('location') or ''}\n"
                f"Required skills: {', '.join(extra_skills)}"
            )
    try:
        return build_cv_from_job(
            job_description,
            job_title=job_title,
            company=company,
            location=location,
            job_skills=extra_skills,
            resume_text=resume_text,
            resume=resume,
            include_cv_text=include_cv_text,
        )
    except (JobToCvError, ResumeParseError) as exc:
        status = getattr(exc, "status_code", 400)
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/api/cv")
def cv_contract():
    return {
        "ok": True,
        "service": "cv-from-job",
        "modes": ["tailored", "template"],
        "endpoints": {
            "from_job": {
                "method": "POST",
                "path": "/api/cv/from-job",
                "content_type": "application/json",
                "body": {
                    "job_description": "job posting text",
                    "job_title": "optional",
                    "company": "optional",
                    "job_id": "optional scraped job id",
                    "resume_text": "optional resume text",
                    "resume": "optional parsed resume JSON",
                },
                "query": {"include_cv_text": "true"},
            },
            "from_job_file": {
                "method": "POST",
                "path": "/api/cv/from-job-file",
                "content_type": "multipart/form-data",
                "fields": {
                    "file": "optional resume PDF/DOCX/TXT",
                    "job_description": "job posting text",
                    "job_title": "optional",
                    "company": "optional",
                    "job_id": "optional",
                },
                "query": {"include_cv_text": "true"},
            },
        },
        "docs": "/docs",
    }


@app.post(
    "/api/cv/from-job",
    response_model=CvFromJobResponse,
    summary="Build or tailor a CV from a job description",
)
def cv_from_job(
    body: CvFromJobRequest,
    include_cv_text: bool = Query(True, description="Include formatted cv_text for display or download"),
):
    resume_payload = body.resume.model_dump() if body.resume else None
    return _build_cv_or_http(
        job_description=body.job_description,
        job_title=body.job_title,
        company=body.company,
        location=body.location,
        job_id=body.job_id,
        resume_text=body.resume_text,
        resume=resume_payload,
        include_cv_text=include_cv_text,
    )


@app.get("/api/convert")
def convert_contract():
    return {
        "ok": True,
        "service": "pdf-docx-convert",
        "max_upload_bytes": 8 * 1024 * 1024,
        "formats": {
            "pdf_to_docx": ["pdf"],
            "docx_to_pdf": ["docx"],
        },
        "notes": [
            "Default response is a file download, not JSON.",
            "In Postman, PDF→DOCX looks like XML in the Body tab. Use Send and Download, or add ?preview=true.",
            "Text-based conversion. Images, columns, and exact layout are not preserved.",
            "Image-only / scanned PDFs fail because there is no OCR.",
            "Old .doc is not supported.",
        ],
        "endpoints": {
            "pdf_to_docx": {
                "method": "POST",
                "path": "/api/convert/pdf-to-docx",
                "content_type": "multipart/form-data",
                "field": "file",
                "query": {"preview": "false"},
                "returns": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            },
            "docx_to_pdf": {
                "method": "POST",
                "path": "/api/convert/docx-to-pdf",
                "content_type": "multipart/form-data",
                "field": "file",
                "query": {"preview": "false"},
                "returns": "application/pdf",
            },
        },
        "docs": "/docs",
    }


@app.post(
    "/api/convert/pdf-to-docx",
    summary="Convert a PDF file to DOCX",
    response_class=Response,
)
async def convert_pdf_to_docx(
    request: Request,
    file: UploadFile | None = File(None, description="PDF file to convert"),
    preview: bool = Query(False, description="Return HTML text preview instead of a .docx file"),
):
    data, filename, _content_type = await _load_resume_upload(request, file)
    try:
        converted = pdf_to_docx(data, filename)
    except ConvertError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF to DOCX failed: {exc}") from exc
    out_name = _swap_extension(filename, ".docx")
    if preview:
        return _html_preview(out_name, preview_text_from_docx(converted))
    return _file_download(converted, out_name, DOCX_TYPE)


@app.post(
    "/api/convert/docx-to-pdf",
    summary="Convert a DOCX file to PDF",
    response_class=Response,
)
async def convert_docx_to_pdf(
    request: Request,
    file: UploadFile | None = File(None, description="Word .docx file to convert"),
    preview: bool = Query(False, description="Return HTML text preview instead of a .pdf file"),
):
    data, filename, _content_type = await _load_resume_upload(request, file)
    try:
        converted = docx_to_pdf(data, filename)
    except ConvertError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DOCX to PDF failed: {exc}") from exc
    out_name = _swap_extension(filename, ".pdf")
    if preview:
        return _html_preview(out_name, preview_text_from_docx(data))
    return _file_download(converted, out_name, PDF_TYPE)


@app.post(
    "/api/cv/from-job-file",
    response_model=CvFromJobResponse,
    summary="Upload a resume and tailor it to a job description",
)
async def cv_from_job_file(
    file: UploadFile | None = File(None, description="Optional resume file: PDF, DOCX, or TXT"),
    job_description: str = Form("", description="Full job posting text"),
    job_title: str | None = Form(None),
    company: str | None = Form(None),
    location: str | None = Form(None),
    job_id: str | None = Form(None),
    include_cv_text: bool = Query(True, description="Include formatted cv_text for display or download"),
):
    resume_payload = None
    if file is not None:
        data = await file.read()
        if data:
            resume_payload = _parse_or_http(data, file.filename or "resume", file.content_type or "")

    return _build_cv_or_http(
        job_description=job_description,
        job_title=job_title,
        company=company,
        location=location,
        job_id=job_id,
        resume=resume_payload,
        include_cv_text=include_cv_text,
    )


if __name__ == "__main__":
    import os
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "1") == "1"
    uvicorn.run("api:app", host=host, port=port, reload=reload)
