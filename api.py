import html
import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

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
from jobscraper.resume_parser import ResumeParseError, parse_resume_bytes, parse_resume_text

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "jobs.json"
INDEX_FILE = ROOT / "index.html"
SCRAPE_LOCK = threading.Lock()
SCRAPE_TIMEOUT = 300

app = FastAPI(
    title="Job Discovery",
    description="Job scrape API, resume/CV parse API, job-description-to-CV API, and PDF/DOCX conversion API for developers.",
    version="1.3.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def load_jobs() -> list[dict]:
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


def cache_meta() -> dict:
    if not DATA_FILE.exists():
        return {"scraped_at": None, "age_seconds": None}
    mtime = DATA_FILE.stat().st_mtime
    scraped_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    age = datetime.now(timezone.utc).timestamp() - mtime
    return {"scraped_at": scraped_at, "age_seconds": int(age)}


def parse_keywords(raw: str) -> list[str]:
    return [part.strip().lower() for part in (raw or "").split(",") if part.strip()]


def filter_jobs(
    jobs: list[dict],
    keywords: list[str],
    state: str,
    platform: str,
    days: int,
    city: str = "",
) -> list[dict]:
    filtered = []
    for job in jobs:
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


def run_spider(keywords: str, location: str, platform: str, city: str = "") -> None:
    DATA_FILE.parent.mkdir(exist_ok=True)
    if DATA_FILE.exists():
        DATA_FILE.unlink()
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
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=SCRAPE_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Scrapy crawl failed").strip()
        raise HTTPException(status_code=502, detail=detail[-2000:])


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
    return {
        "ok": True,
        "service": "job-discovery",
        "resume_parse": True,
        "cv_from_job": True,
        "pdf_docx_convert": True,
        "resume_formats": ["pdf", "docx", "txt"],
    }


@app.get("/")
def home():
    return FileResponse(INDEX_FILE)


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
        ],
        "days": [1, 3, 7, 30],
    }


@app.get("/api/jobs")
def get_jobs(
    keywords: str = Query(""),
    state: str = Query("All"),
    platform: str = Query("All"),
    days: int = Query(30),
    city: str = Query(""),
):
    jobs = filter_jobs(load_jobs(), [], state, platform, days, city)
    return {"jobs": jobs, "count": len(jobs), **cache_meta()}


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
        for job in load_jobs()
        if is_usa_job(job.get("state") or "", job.get("location") or "")
    ]
    return {"jobs": jobs, "count": len(jobs), **cache_meta()}


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
