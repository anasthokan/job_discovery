"""Which job sources a scrape is allowed to read.

Each platform has a row in job_discovery_sources. status "active" means the
scraper fetches that source. status "inactive" means it is skipped. A missing
row is treated as active so a new source still runs until it is switched off.
"""

from __future__ import annotations

import logging
import re
import threading
import time

log = logging.getLogger("jobscraper.sources")

SOURCES_TABLE = "job_discovery_sources"
_TTL_SECONDS = 2.0

CREATE_SOURCES_SQL = f"""
CREATE TABLE IF NOT EXISTS {SOURCES_TABLE} (
  id VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  last_sync_at DATETIME(6) NULL,
  updated_at DATETIME(6) NOT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# id is the stable key. platform matches the name the spider and filters use.
CATALOG = (
    {"id": "remoteok", "name": "RemoteOK", "platform": "RemoteOK"},
    {"id": "remotive", "name": "Remotive", "platform": "Remotive"},
    {"id": "jobicy", "name": "Jobicy", "platform": "Jobicy"},
    {"id": "muse", "name": "The Muse", "platform": "The Muse"},
    {"id": "arbeitnow", "name": "Arbeitnow", "platform": "Arbeitnow"},
    {"id": "himalayas", "name": "Himalayas", "platform": "Himalayas"},
    {"id": "wwr", "name": "We Work Remotely", "platform": "We Work Remotely"},
    {"id": "remoteco", "name": "Remote.co", "platform": "Remote.co"},
    {"id": "greenhouse", "name": "Greenhouse", "platform": "Greenhouse"},
    {"id": "lever", "name": "Lever", "platform": "Lever"},
    {"id": "ashby", "name": "Ashby", "platform": "Ashby"},
    {"id": "smartrecruiters", "name": "SmartRecruiters", "platform": "SmartRecruiters"},
    {"id": "workable", "name": "Workable", "platform": "Workable"},
    {"id": "workday", "name": "Workday", "platform": "Workday"},
    {"id": "bamboohr", "name": "BambooHR", "platform": "BambooHR"},
    {"id": "recruitee", "name": "Recruitee", "platform": "Recruitee"},
    {"id": "breezy", "name": "Breezy HR", "platform": "Breezy HR"},
    {"id": "personio", "name": "Personio", "platform": "Personio"},
    {"id": "linkedin", "name": "LinkedIn", "platform": "LinkedIn"},
    {"id": "indeed", "name": "Indeed", "platform": "Indeed"},
    {"id": "glassdoor", "name": "Glassdoor", "platform": "Glassdoor"},
    {"id": "ziprecruiter", "name": "ZipRecruiter", "platform": "ZipRecruiter"},
    {"id": "google", "name": "Google Jobs", "platform": "Google Jobs"},
    {"id": "adzuna", "name": "Adzuna", "platform": "Adzuna"},
    {"id": "jooble", "name": "Jooble", "platform": "Jooble"},
    {"id": "careerjet", "name": "Careerjet", "platform": "Careerjet"},
    {"id": "dice", "name": "Dice", "platform": "Dice"},
    {"id": "yc", "name": "Y Combinator", "platform": "Y Combinator"},
    {"id": "hackernews", "name": "Hacker News", "platform": "Hacker News"},
    {"id": "usajobs", "name": "USAJobs", "platform": "USAJobs"},
)

BY_ID = {row["id"]: row for row in CATALOG}
PLATFORM_TO_ID = {row["platform"]: row["id"] for row in CATALOG}

_LOCK = threading.Lock()
_CACHE: dict[str, str] = {}
_LOADED_AT = 0.0


def is_platform_active(platform: str) -> bool:
    """True when this platform should be scraped. Unknown names stay on."""
    source_id = PLATFORM_TO_ID.get(platform or "")
    if not source_id:
        return True
    return _status(source_id) == "active"


def list_sources() -> list[dict]:
    stored = _rows()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "platform": row["platform"],
            "status": stored.get(row["id"], "active"),
        }
        for row in CATALOG
    ]


def resolve_source(value: str) -> dict | None:
    """Match a source id, display name, or platform. 'Greenhouse' and 'greenhouse' both work."""
    text = (value or "").strip().lower()
    if not text:
        return None
    if text in BY_ID:
        return BY_ID[text]
    compact = re.sub(r"[^a-z0-9]+", "", text)
    for row in CATALOG:
        names = (row["id"], row["name"].lower(), row["platform"].lower())
        if text in names:
            return row
        if compact and compact in {re.sub(r"[^a-z0-9]+", "", name) for name in names}:
            return row
    return None


def set_source_status(source_id: str, status: str) -> dict | None:
    spec = resolve_source(source_id)
    if not spec:
        return None
    cleaned = str(status or "").strip().lower()
    if cleaned not in {"active", "inactive"}:
        raise ValueError("status must be active or inactive")
    _write_status(spec["id"], cleaned)
    log.info("Job source %s is now %s", spec["id"], cleaned)
    for row in list_sources():
        if row["id"] == spec["id"]:
            return row
    return None


def ensure_sources(cur) -> None:
    """Create the table and insert any catalog source that is not stored yet."""
    cur.execute(CREATE_SOURCES_SQL)
    cur.executemany(
        f"INSERT IGNORE INTO {SOURCES_TABLE} (id, status, updated_at) "
        "VALUES (%s, 'active', UTC_TIMESTAMP(6))",
        [(row["id"],) for row in CATALOG],
    )


def _status(source_id: str) -> str:
    status = str(_rows().get(source_id) or "active").strip().lower()
    return status if status in {"active", "inactive"} else "active"


def _rows() -> dict[str, str]:
    global _LOADED_AT
    now = time.monotonic()
    with _LOCK:
        if _LOADED_AT and now - _LOADED_AT < _TTL_SECONDS:
            return dict(_CACHE)
        try:
            loaded = _read_store()
        except Exception:
            log.exception("Could not read job source status")
            if _LOADED_AT:
                return dict(_CACHE)
            return {}
        _CACHE.clear()
        _CACHE.update(loaded)
        _LOADED_AT = now
        return dict(_CACHE)


def _remember(source_id: str, status: str) -> None:
    global _LOADED_AT
    with _LOCK:
        _CACHE[source_id] = status
        _LOADED_AT = time.monotonic()


def _read_store() -> dict[str, str]:
    from jobscraper.db import get_conn, mysql_configured

    if not mysql_configured():
        return {}
    conn = get_conn()
    if conn is None:
        return {}
    with conn.cursor() as cur:
        ensure_sources(cur)
        cur.execute(f"SELECT id, status FROM {SOURCES_TABLE}")
        found = {}
        for row in cur.fetchall() or []:
            source_id = str(row.get("id") or "")
            if source_id in BY_ID:
                found[source_id] = str(row.get("status") or "active")
        return found


def _write_status(source_id: str, status: str) -> None:
    from jobscraper.db import close_conn, get_conn, mysql_configured

    if not mysql_configured():
        raise RuntimeError("MySQL is not configured")
    conn = get_conn()
    if conn is None:
        raise RuntimeError("MySQL is not connected")
    try:
        with conn.cursor() as cur:
            ensure_sources(cur)
            cur.execute(
                f"INSERT INTO {SOURCES_TABLE} (id, status, updated_at) "
                "VALUES (%s, %s, UTC_TIMESTAMP(6)) "
                "ON DUPLICATE KEY UPDATE status = VALUES(status), updated_at = UTC_TIMESTAMP(6)",
                (source_id, status),
            )
    except Exception:
        close_conn()
        raise
    _remember(source_id, status)
