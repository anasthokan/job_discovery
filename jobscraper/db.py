"""MySQL persistence for scraped job listings.

Credentials come from environment / `.env`:
  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

log = logging.getLogger("jobscraper.db")

_LOCK = threading.Lock()
_CONN = None
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Dedicated names so we never write into an existing ezyjob `jobs` table.
JOBS_TABLE = "job_discovery_jobs"
RUNS_TABLE = "job_discovery_scrape_runs"

CREATE_JOBS_SQL = f"""
CREATE TABLE IF NOT EXISTS {JOBS_TABLE} (
  id VARCHAR(255) NOT NULL,
  title VARCHAR(512) NOT NULL,
  company VARCHAR(512) NOT NULL,
  skills JSON NULL,
  state VARCHAR(128) NULL,
  location VARCHAR(512) NULL,
  platform VARCHAR(128) NULL,
  days_ago INT NOT NULL DEFAULT 0,
  url VARCHAR(512) NULL,
  posted_at VARCHAR(64) NULL,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_jd_jobs_url (url),
  KEY idx_jd_jobs_platform (platform),
  KEY idx_jd_jobs_last_seen (last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_RUNS_SQL = f"""
CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
  id BIGINT NOT NULL AUTO_INCREMENT,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  job_count INT NOT NULL DEFAULT 0,
  keywords VARCHAR(512) NULL,
  state VARCHAR(128) NULL,
  platform VARCHAR(128) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  error TEXT NULL,
  PRIMARY KEY (id),
  KEY idx_jd_scrape_runs_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

UPSERT_SQL = f"""
INSERT INTO {JOBS_TABLE} (
  id, title, company, skills, state, location, platform, days_ago, url, posted_at,
  first_seen_at, last_seen_at
) VALUES (
  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP()
) AS new
ON DUPLICATE KEY UPDATE
  title = new.title,
  company = new.company,
  skills = new.skills,
  state = new.state,
  location = new.location,
  platform = new.platform,
  days_ago = new.days_ago,
  url = new.url,
  posted_at = new.posted_at,
  last_seen_at = UTC_TIMESTAMP()
"""


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def mysql_config() -> dict | None:
    host = _env("MYSQL_HOST")
    user = _env("MYSQL_USER")
    database = _env("MYSQL_DATABASE")
    if not host or not user or not database:
        return None
    if not _DB_NAME_RE.fullmatch(database):
        log.error("MYSQL_DATABASE must be letters, numbers, underscore only")
        return None
    try:
        port = int(_env("MYSQL_PORT", "3306") or "3306")
    except ValueError:
        port = 3306
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": os.getenv("MYSQL_PASSWORD") or "",
        "database": database,
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": int(_env("MYSQL_CONNECT_TIMEOUT", "5") or "5"),
    }


def mysql_configured() -> bool:
    return mysql_config() is not None


def _connect(cfg: dict, *, with_database: bool = True):
    import pymysql

    kwargs = {
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
        "password": cfg["password"],
        "charset": cfg["charset"],
        "autocommit": cfg["autocommit"],
        "connect_timeout": cfg["connect_timeout"],
        "cursorclass": pymysql.cursors.DictCursor,
    }
    if with_database:
        kwargs["database"] = cfg["database"]
    return pymysql.connect(**kwargs)


def _ensure_database(cfg: dict) -> None:
    conn = _connect(cfg, with_database=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{cfg['database']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


def get_conn():
    global _CONN
    cfg = mysql_config()
    if not cfg:
        return None
    with _LOCK:
        if _CONN is None or not getattr(_CONN, "open", False):
            _CONN = _connect(cfg)
        else:
            try:
                _CONN.ping(reconnect=True)
            except Exception:
                _CONN = _connect(cfg)
        return _CONN


def close_conn() -> None:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None


def init_db() -> bool:
    cfg = mysql_config()
    if not cfg:
        log.info("MySQL not configured; jobs stay in data/jobs.json")
        return False
    try:
        _ensure_database(cfg)
        conn = get_conn()
        if conn is None:
            return False
        with conn.cursor() as cur:
            cur.execute(CREATE_JOBS_SQL)
            cur.execute(CREATE_RUNS_SQL)
        log.info("MySQL ready (%s/%s)", cfg["host"], cfg["database"])
        return True
    except Exception:
        close_conn()
        log.exception("MySQL init failed")
        return False


def _as_skills_json(value) -> str:
    if not value:
        items = []
    elif isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        items = [part.strip() for part in str(value).split(",") if part.strip()]
    return json.dumps(items, ensure_ascii=False)


def _row_from_item(job: dict) -> tuple | None:
    job_id = str(job.get("id") or "").strip()
    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "").strip()
    if not job_id or not title or not company:
        return None
    url = str(job.get("url") or "").strip() or None
    posted = job.get("posted_at")
    posted_at = str(posted).strip() if posted not in (None, "") else None
    try:
        days_ago = int(job.get("daysAgo") or 0)
    except (TypeError, ValueError):
        days_ago = 0
    return (
        job_id[:255],
        title[:512],
        company[:512],
        _as_skills_json(job.get("skills")),
        (str(job.get("state") or "").strip() or None),
        (str(job.get("location") or "").strip() or None),
        (str(job.get("platform") or "").strip() or None),
        max(days_ago, 0),
        url[:512] if url else None,
        posted_at[:64] if posted_at else None,
    )


def upsert_jobs(jobs: list[dict]) -> int:
    if not jobs or not mysql_configured():
        return 0
    rows = [row for row in (_row_from_item(job) for job in jobs) if row]
    if not rows:
        return 0
    unique = {}
    for row in rows:
        unique[row[0]] = row
    by_url = {}
    for row in unique.values():
        key = row[8] or f"id:{row[0]}"
        by_url[key] = row
    rows = list(by_url.values())
    conn = get_conn()
    if conn is None:
        return 0
    try:
        with _LOCK:
            with conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, rows)
        return len(rows)
    except Exception:
        close_conn()
        log.exception("MySQL upsert failed")
        raise


def _parse_skills(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]
    return []


def _days_ago_from_iso(value: str | None, fallback: int = 0) -> int:
    if not value:
        return fallback
    cleaned = str(value).strip().replace("Z", "+00:00")
    try:
        posted = datetime.fromisoformat(cleaned)
    except ValueError:
        return fallback
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - posted).total_seconds() // 86400))


def _job_from_row(row: dict) -> dict:
    posted = row.get("posted_at")
    days = _days_ago_from_iso(posted, int(row.get("days_ago") or 0))
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "company": row.get("company"),
        "skills": _parse_skills(row.get("skills")),
        "state": row.get("state") or "",
        "location": row.get("location") or "",
        "platform": row.get("platform") or "",
        "daysAgo": days,
        "url": row.get("url") or "",
        "posted_at": posted,
    }


def load_jobs() -> list[dict]:
    if not mysql_configured():
        return []
    try:
        conn = get_conn()
        if conn is None:
            return []
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, title, company, skills, state, location, platform, "
                f"days_ago, url, posted_at FROM {JOBS_TABLE} ORDER BY last_seen_at DESC"
            )
            rows = cur.fetchall() or []
        return [_job_from_row(row) for row in rows]
    except Exception:
        log.exception("MySQL load_jobs failed")
        close_conn()
        return []


def job_count() -> int:
    if not mysql_configured():
        return 0
    try:
        conn = get_conn()
        if conn is None:
            return 0
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {JOBS_TABLE}")
            row = cur.fetchone() or {}
        return int(row.get("n") or 0)
    except Exception:
        close_conn()
        return 0


def start_scrape_run(keywords: str, state: str, platform: str) -> int | None:
    if not mysql_configured():
        return None
    try:
        conn = get_conn()
        if conn is None:
            return None
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {RUNS_TABLE} (started_at, keywords, state, platform, status) "
                "VALUES (UTC_TIMESTAMP(), %s, %s, %s, 'running')",
                (keywords[:512] if keywords else None, state or None, platform or None),
            )
            return int(cur.lastrowid)
    except Exception:
        log.exception("Could not record scrape start")
        close_conn()
        return None


def finish_scrape_run(run_id: int | None, *, job_count_value: int, error: str | None = None) -> None:
    if not run_id or not mysql_configured():
        return
    status = "error" if error else "ok"
    try:
        conn = get_conn()
        if conn is None:
            return
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {RUNS_TABLE} SET finished_at = UTC_TIMESTAMP(), job_count = %s, "
                "status = %s, error = %s WHERE id = %s",
                (job_count_value, status, (error or "")[:4000] or None, run_id),
            )
    except Exception:
        log.exception("Could not record scrape finish")
        close_conn()


def last_scrape_meta() -> dict:
    empty = {"scraped_at": None, "age_seconds": None, "job_count": 0, "status": None}
    if not mysql_configured():
        return empty
    try:
        conn = get_conn()
        if conn is None:
            return empty
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT finished_at, job_count, status FROM {RUNS_TABLE} "
                "WHERE status = 'ok' AND finished_at IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT 1"
            )
            row = cur.fetchone()
        if not row or not row.get("finished_at"):
            return {**empty, "job_count": job_count()}
        finished = row["finished_at"]
        if isinstance(finished, datetime):
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            scraped_at = finished.astimezone(timezone.utc).isoformat()
            age = int((datetime.now(timezone.utc) - finished.astimezone(timezone.utc)).total_seconds())
        else:
            scraped_at = str(finished)
            age = None
        return {
            "scraped_at": scraped_at,
            "age_seconds": age,
            "job_count": int(row.get("job_count") or 0),
            "status": row.get("status"),
        }
    except Exception:
        close_conn()
        return empty


def health() -> dict:
    cfg = mysql_config()
    if not cfg:
        return {"configured": False, "ok": False, "jobs": 0}
    try:
        conn = get_conn()
        if conn is None:
            return {"configured": True, "ok": False, "jobs": 0, "error": "no connection"}
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
        return {
            "configured": True,
            "ok": True,
            "host": cfg["host"],
            "database": cfg["database"],
            "jobs_table": JOBS_TABLE,
            "jobs": job_count(),
            **{k: v for k, v in last_scrape_meta().items() if k in {"scraped_at", "status"}},
        }
    except Exception as exc:
        close_conn()
        return {"configured": True, "ok": False, "jobs": 0, "error": str(exc)[:300]}


def import_json_if_empty(path: Path) -> int:
    if not mysql_configured() or not path.exists() or job_count() > 0:
        return 0
    try:
        text = path.read_text(encoding="utf-8").replace("\x00", "").strip()
        if not text:
            return 0
        payload, _ = json.JSONDecoder().raw_decode(text)
    except (OSError, json.JSONDecodeError):
        return 0
    jobs = payload if isinstance(payload, list) else []
    saved = upsert_jobs(jobs)
    if saved:
        log.info("Imported %s jobs from %s", saved, path)
    return saved
