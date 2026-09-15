import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from jobscraper.locations import DROPDOWN_STATES, is_usa_job, matches_city_filter, matches_state_filter

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "jobs.json"
INDEX_FILE = ROOT / "index.html"
SCRAPE_LOCK = threading.Lock()
SCRAPE_TIMEOUT = 300

app = FastAPI(title="Job Discovery")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/api/health")
def health():
    return {"ok": True, "service": "job-discovery"}


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


if __name__ == "__main__":
    import os
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "1") == "1"
    uvicorn.run("api:app", host=host, port=port, reload=reload)
