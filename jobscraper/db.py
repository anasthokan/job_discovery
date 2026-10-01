"""MySQL persistence for scraped job listings.

Credentials come from environment / `.env`:
  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
"""

from __future__ import annotations

import hashlib
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
# One MySQL connection per thread. A single shared connection is closed by one
# request while another request is still reading, which crashes uvicorn and
# IIS then returns 502.
_local = threading.local()
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_EVERIFY_INDEX: dict | None = None

# Scraped listings go into the shared ezyjob database, table `jobs`.
JOBS_TABLE = "jobs"
RUNS_TABLE = "job_discovery_scrape_runs"
EVERIFY_TABLE = "job_discovery_everify_employers"
CANDIDATES_TABLE = "job_discovery_candidates"

CREATE_JOBS_SQL = f"""
CREATE TABLE IF NOT EXISTS {JOBS_TABLE} (
  id BIGINT NOT NULL AUTO_INCREMENT,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  public_id CHAR(32) NOT NULL,
  title VARCHAR(512) NOT NULL,
  location VARCHAR(512) NOT NULL,
  description LONGTEXT NOT NULL,
  posted_at DATETIME(6) NULL,
  is_active TINYINT(1) NOT NULL,
  company_ref_id BIGINT NULL,
  platform VARCHAR(120) NOT NULL,
  days_ago INT UNSIGNED NULL,
  company VARCHAR(255) NULL,
  e_verified VARCHAR(64) NOT NULL,
  e_verify_name VARCHAR(255) NOT NULL,
  first_seen_at DATETIME(6) NULL,
  last_seen_at DATETIME(6) NULL,
  skills JSON NOT NULL,
  source_id VARCHAR(255) NULL,
  state VARCHAR(255) NOT NULL,
  url VARCHAR(1000) NOT NULL,
  job_type VARCHAR(32) NOT NULL DEFAULT 'fulltime',
  work_model VARCHAR(16) NOT NULL DEFAULT 'unknown',
  experience_level VARCHAR(32) NOT NULL DEFAULT 'unknown',
  years_experience SMALLINT NULL,
  h1b_sponsorship VARCHAR(16) NOT NULL DEFAULT 'unknown',
  clearance_required VARCHAR(16) NOT NULL DEFAULT 'unknown',
  us_citizen_required VARCHAR(16) NOT NULL DEFAULT 'no',
  listing_inferred TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_jobs_public_id (public_id),
  UNIQUE KEY uk_jobs_source_id (source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_EVERIFY_SQL = f"""
CREATE TABLE IF NOT EXISTS {EVERIFY_TABLE} (
  name_key VARCHAR(255) NOT NULL,
  employer VARCHAR(512) NOT NULL,
  dba VARCHAR(512) NULL,
  account_status VARCHAR(64) NULL,
  everify_plus VARCHAR(32) NULL,
  date_enrolled VARCHAR(64) NULL,
  hiring_sites VARCHAR(512) NULL,
  PRIMARY KEY (name_key),
  KEY idx_jd_everify_status (account_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_CANDIDATES_SQL = f"""
CREATE TABLE IF NOT EXISTS {CANDIDATES_TABLE} (
  candidate_id VARCHAR(64) NOT NULL,
  profile JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  PRIMARY KEY (candidate_id)
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

# ezyjob.jobs.id is a bigint autoincrement. The scraper id is stored in source_id.
UPSERT_SQL = f"""
INSERT INTO {JOBS_TABLE} (
  public_id, source_id, title, company, company_ref_id, skills, state, location,
  platform, days_ago, url, description, posted_at, is_active, e_verified, e_verify_name,
  job_type, work_model, experience_level, years_experience, h1b_sponsorship, clearance_required,
  us_citizen_required, listing_inferred, first_seen_at, last_seen_at, created_at, updated_at
) VALUES (
  %s, %s, %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s, %s, 1, 'unknown', '',
  %s, %s, %s, %s, %s, %s,
  %s, 1, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
) AS new
ON DUPLICATE KEY UPDATE
  title = new.title,
  e_verified = IF({JOBS_TABLE}.company <=> new.company, {JOBS_TABLE}.e_verified, 'unknown'),
  e_verify_name = IF({JOBS_TABLE}.company <=> new.company, {JOBS_TABLE}.e_verify_name, ''),
  company = new.company,
  company_ref_id = COALESCE(new.company_ref_id, {JOBS_TABLE}.company_ref_id),
  skills = new.skills,
  state = new.state,
  location = new.location,
  platform = new.platform,
  days_ago = new.days_ago,
  url = new.url,
  description = new.description,
  posted_at = new.posted_at,
  job_type = new.job_type,
  work_model = new.work_model,
  experience_level = new.experience_level,
  years_experience = new.years_experience,
  h1b_sponsorship = new.h1b_sponsorship,
  clearance_required = new.clearance_required,
  us_citizen_required = new.us_citizen_required,
  listing_inferred = 1,
  is_active = 1,
  last_seen_at = UTC_TIMESTAMP(6),
  updated_at = UTC_TIMESTAMP(6)
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
    cfg = mysql_config()
    if not cfg:
        return None
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(conn, "open", False):
        return conn
    conn = _connect(cfg)
    _local.conn = conn
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    _local.conn = None
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def _ensure_job_columns(cur) -> None:
    cur.execute(f"SHOW COLUMNS FROM {JOBS_TABLE} LIKE 'e_verified'")
    if not cur.fetchone():
        cur.execute(
            f"ALTER TABLE {JOBS_TABLE} "
            "ADD COLUMN e_verified VARCHAR(16) NOT NULL DEFAULT 'unknown'"
        )
    cur.execute(f"SHOW COLUMNS FROM {JOBS_TABLE} LIKE 'e_verify_name'")
    if not cur.fetchone():
        cur.execute(f"ALTER TABLE {JOBS_TABLE} ADD COLUMN e_verify_name VARCHAR(512) NULL")
    cur.execute(f"SHOW COLUMNS FROM {JOBS_TABLE} LIKE 'job_type'")
    if not cur.fetchone():
        cur.execute(
            f"ALTER TABLE {JOBS_TABLE} "
            "ADD COLUMN job_type VARCHAR(32) NOT NULL DEFAULT 'fulltime'"
        )
    for name, ddl in (
        ("work_model", "VARCHAR(16) NOT NULL DEFAULT 'unknown'"),
        ("experience_level", "VARCHAR(32) NOT NULL DEFAULT 'unknown'"),
        ("years_experience", "SMALLINT NULL"),
        ("h1b_sponsorship", "VARCHAR(16) NOT NULL DEFAULT 'unknown'"),
        ("clearance_required", "VARCHAR(16) NOT NULL DEFAULT 'unknown'"),
        ("listing_inferred", "TINYINT(1) NOT NULL DEFAULT 0"),
    ):
        cur.execute(f"SHOW COLUMNS FROM {JOBS_TABLE} LIKE '{name}'")
        if not cur.fetchone():
            cur.execute(f"ALTER TABLE {JOBS_TABLE} ADD COLUMN {name} {ddl}")
    cur.execute(f"SHOW COLUMNS FROM {JOBS_TABLE} LIKE 'us_citizen_required'")
    if not cur.fetchone():
        cur.execute(
            f"ALTER TABLE {JOBS_TABLE} "
            "ADD COLUMN us_citizen_required VARCHAR(16) NOT NULL DEFAULT 'no'"
        )
        # Re-run inference on saved rows so the new column is filled.
        cur.execute(f"UPDATE {JOBS_TABLE} SET listing_inferred = 0")
    else:
        cur.execute(
            f"ALTER TABLE {JOBS_TABLE} ALTER COLUMN us_citizen_required SET DEFAULT 'no'"
        )
        cur.execute(
            f"UPDATE {JOBS_TABLE} SET us_citizen_required = 'no' "
            "WHERE us_citizen_required NOT IN ('yes', 'no')"
        )
    try:
        cur.execute(f"ALTER TABLE {JOBS_TABLE} ADD KEY idx_jd_jobs_everify (e_verified)")
    except Exception:
        pass


def init_db() -> bool:
    global _EVERIFY_INDEX
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
            cur.execute(CREATE_EVERIFY_SQL)
            cur.execute(CREATE_CANDIDATES_SQL)
            _ensure_job_columns(cur)
        backfill_listing_fields()
        _EVERIFY_INDEX = None
        from jobscraper.everify import load_rows_from_csv

        rows = load_rows_from_csv()
        if rows:
            replace_everify_employers(rows)
            enrich_jobs_everify()
        log.info("MySQL ready (%s/%s)", cfg["host"], cfg["database"])
        return True
    except Exception:
        close_conn()
        log.exception("MySQL init failed")
        return False


def backfill_listing_fields() -> int:
    """Fill work model, experience, H1B, clearance, and US citizen on rows saved before those columns existed."""
    if not mysql_configured():
        return 0
    from jobscraper.job_fields import infer_listing_fields

    conn = get_conn()
    if conn is None:
        return 0
    try:
        with _LOCK:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, title, location, state, description, job_type "
                    f"FROM {JOBS_TABLE} WHERE listing_inferred = 0"
                )
                rows = cur.fetchall() or []
                if not rows:
                    return 0
                payload = []
                for row in rows:
                    fields = infer_listing_fields(
                        title=row.get("title") or "",
                        location=row.get("location") or "",
                        state=row.get("state") or "",
                        description=row.get("description") or "",
                        job_type=row.get("job_type") or "",
                    )
                    payload.append(
                        (
                            fields["work_model"],
                            str(fields["experience_level"] or "unknown")[:32],
                            fields["years_experience"],
                            fields["h1b_sponsorship"],
                            fields["clearance_required"],
                            fields["us_citizen_required"],
                            fields["job_type"],
                            row["id"],
                        )
                    )
                cur.executemany(
                    f"UPDATE {JOBS_TABLE} SET work_model=%s, experience_level=%s, "
                    f"years_experience=%s, h1b_sponsorship=%s, clearance_required=%s, "
                    f"us_citizen_required=%s, job_type=%s, listing_inferred=1 WHERE id=%s",
                    payload,
                )
        log.info("Backfilled listing fields on %s jobs", len(payload))
        return len(payload)
    except Exception:
        close_conn()
        log.exception("Listing field backfill failed")
        return 0


def _as_skills_json(value) -> str:
    if not value:
        items = []
    elif isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        items = [part.strip() for part in str(value).split(",") if part.strip()]
    return json.dumps(items, ensure_ascii=False)


def _clip(value, limit: int, default: str = "") -> str:
    text = str(value or "").strip()
    return (text or default)[:limit]


def _public_id(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _company_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())[:255]


def _posted_mysql(value) -> str | None:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "nat", "nan"}:
        return None
    if text.isdigit():
        try:
            stamp = int(text)
            if stamp > 10_000_000_000:
                stamp //= 1000
            return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return None
    cleaned = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10] + " 00:00:00"
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _listing_fields(job: dict, description: str) -> dict:
    from jobscraper.job_fields import infer_listing_fields

    fields = infer_listing_fields(
        title=str(job.get("title") or ""),
        location=str(job.get("location") or ""),
        state=str(job.get("state") or ""),
        description=description,
        job_type=job.get("job_type") or "",
    )
    level = str(fields["experience_level"] or "unknown")
    return {
        "job_type": fields["job_type"],
        "work_model": str(fields["work_model"] or "unknown")[:16],
        "experience_level": level[:32],
        "years_experience": fields["years_experience"],
        "h1b_sponsorship": str(fields["h1b_sponsorship"] or "unknown")[:16],
        "clearance_required": str(fields["clearance_required"] or "unknown")[:16],
        "us_citizen_required": str(fields["us_citizen_required"] or "no")[:16],
    }


def _years_value(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_from_item(job: dict) -> dict | None:
    source_id = _clip(job.get("id"), 255)
    title = _clip(job.get("title"), 512)
    company = _clip(job.get("company"), 255)
    if not source_id or not title or not company:
        return None
    skills = _as_skills_json(job.get("skills"))
    description = str(job.get("description") or job.get("search_text") or "").strip()
    if len(description) < 40:
        try:
            skill_names = json.loads(skills)
        except json.JSONDecodeError:
            skill_names = []
        description = ", ".join(skill_names) if skill_names else title
    description = description[:60000]
    try:
        days_ago = int(job.get("daysAgo") or 0)
    except (TypeError, ValueError):
        days_ago = 0
    return {
        "public_id": _public_id(source_id),
        "source_id": source_id,
        "title": title,
        "company": company,
        "company_key": _company_key(company),
        "skills": skills,
        "state": _clip(job.get("state"), 255),
        "location": _clip(job.get("location"), 512, "United States"),
        "platform": _clip(job.get("platform"), 120, "Unknown"),
        "days_ago": max(days_ago, 0),
        "url": _clip(job.get("url"), 1000),
        "description": description,
        "posted_at": _posted_mysql(job.get("posted_at")),
        **_listing_fields(job, description),
    }


def _company_ids(cur, names: dict[str, str]) -> dict[str, int]:
    """Map normalized company name -> companies.id, inserting a row when missing."""
    found: dict[str, int] = {}
    for key, name in names.items():
        if not key or key in found:
            continue
        cur.execute(
            "SELECT id FROM companies WHERE normalized_name = %s OR LOWER(name) = %s LIMIT 1",
            (key, key),
        )
        row = cur.fetchone()
        if row:
            found[key] = int(row["id"])
            continue
        cur.execute(
            """
            INSERT INTO companies (
              public_id, name, normalized_name, website, h1b_sponsor,
              sponsorship_notes, logo_url, created_at, updated_at
            ) VALUES (%s, %s, %s, '', 0, '', '', UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE updated_at = companies.updated_at
            """,
            (_public_id("company:" + key), name, key),
        )
        cur.execute(
            "SELECT id FROM companies WHERE public_id = %s OR normalized_name = %s LIMIT 1",
            (_public_id("company:" + key), key),
        )
        row = cur.fetchone()
        if row:
            found[key] = int(row["id"])
    return found


def upsert_jobs(jobs: list[dict]) -> int:
    if not jobs or not mysql_configured():
        return 0
    rows = [row for row in (_row_from_item(job) for job in jobs) if row]
    if not rows:
        return 0
    unique = {}
    for row in rows:
        unique[row["source_id"]] = row
    by_url = {}
    for row in unique.values():
        by_url[row["url"] or f"id:{row['source_id']}"] = row
    rows = list(by_url.values())
    conn = get_conn()
    if conn is None:
        return 0
    try:
        with _LOCK:
            with conn.cursor() as cur:
                company_ids = _company_ids(
                    cur,
                    {row["company_key"]: row["company"] for row in rows if row["company_key"]},
                )
                payload = [
                    (
                        row["public_id"],
                        row["source_id"],
                        row["title"],
                        row["company"],
                        company_ids.get(row["company_key"]),
                        row["skills"],
                        row["state"],
                        row["location"],
                        row["platform"],
                        row["days_ago"],
                        row["url"],
                        row["description"],
                        row["posted_at"],
                        row["job_type"] if row["job_type"] in {"fulltime", "parttime", "contract", "internship"} else "fulltime",
                        row["work_model"],
                        row["experience_level"],
                        row["years_experience"],
                        row["h1b_sponsorship"],
                        row["clearance_required"],
                        row["us_citizen_required"],
                    )
                    for row in rows
                ]
                cur.executemany(UPSERT_SQL, payload)
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


def _as_e_verified(value) -> str:
    """Stored flag as true, false, or unknown."""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "1"}:
        return "true"
    if text in {"false", "no", "0"}:
        return "false"
    return "unknown"


def _job_from_row(row: dict) -> dict:
    posted = row.get("posted_at")
    days = _days_ago_from_iso(posted, int(row.get("days_ago") or 0))
    payload = {
        "id": row.get("id"),
        "title": row.get("title"),
        "company": row.get("company"),
        "skills": _parse_skills(row.get("skills")),
        "state": row.get("state") or "",
        "location": row.get("location") or "",
        "platform": row.get("platform") or "",
        "daysAgo": days,
        "url": row.get("url") or "",
        "description": row.get("description") or "",
        "job_type": row.get("job_type") or "fulltime",
        "work_model": row.get("work_model") or "unknown",
        "experience_level": row.get("experience_level") or "unknown",
        "years_experience": _years_value(row.get("years_experience")),
        "h1b_sponsorship": row.get("h1b_sponsorship") or "unknown",
        "clearance_required": row.get("clearance_required") or "unknown",
        "us_citizen_required": "yes" if row.get("us_citizen_required") == "yes" else "no",
        "posted_at": posted,
        "e_verified": _as_e_verified(row.get("e_verified")),
        "e_verify_name": row.get("e_verify_name"),
    }
    if row.get("api_id") is not None:
        payload["api_id"] = int(row["api_id"])
    return payload


def load_jobs() -> list[dict]:
    if not mysql_configured():
        return []
    try:
        conn = get_conn()
        if conn is None:
            return []
        try:
            enrich_jobs_everify(only_unknown=True)
        except Exception:
            log.exception("Could not resolve unknown e_verified rows")
            close_conn()
            conn = get_conn()
            if conn is None:
                return []
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id AS api_id, "
                f"COALESCE(NULLIF(source_id, ''), public_id, CAST(id AS CHAR)) AS id, "
                f"title, company, skills, state, location, platform, "
                f"days_ago, url, description, job_type, work_model, experience_level, "
                f"years_experience, h1b_sponsorship, clearance_required, us_citizen_required, "
                f"posted_at, e_verified, e_verify_name "
                f"FROM {JOBS_TABLE} ORDER BY last_seen_at DESC"
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


def _profile_from_cell(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def save_candidate(candidate_id: str, profile: dict) -> None:
    conn = get_conn()
    if conn is None:
        raise RuntimeError("MySQL is not configured")
    payload = json.dumps(profile, ensure_ascii=False)
    with conn.cursor() as cur:
        cur.execute(CREATE_CANDIDATES_SQL)
        cur.execute(
            f"INSERT INTO {CANDIDATES_TABLE} (candidate_id, profile, created_at, updated_at) "
            "VALUES (%s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)) AS new "
            "ON DUPLICATE KEY UPDATE profile = new.profile, updated_at = UTC_TIMESTAMP(6)",
            (candidate_id, payload),
        )


def load_candidate(candidate_id: str) -> dict | None:
    if not mysql_configured():
        return None
    conn = get_conn()
    if conn is None:
        return None
    with conn.cursor() as cur:
        cur.execute(CREATE_CANDIDATES_SQL)
        cur.execute(
            f"SELECT profile FROM {CANDIDATES_TABLE} WHERE candidate_id = %s",
            (candidate_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _profile_from_cell(row.get("profile"))


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


def replace_everify_employers(rows: list[dict]) -> int:
    global _EVERIFY_INDEX
    from jobscraper.everify import build_index, normalize_name

    if not mysql_configured():
        return 0
    conn = get_conn()
    if conn is None:
        return 0
    payload = []
    seen = set()
    for row in rows:
        for name in (row.get("employer"), row.get("dba")):
            key = normalize_name(name or "")[:255]
            if len(key) < 4 or key in seen:
                continue
            seen.add(key)
            payload.append(
                (
                    key,
                    (row.get("employer") or name or "")[:512],
                    (row.get("dba") or None),
                    (row.get("account_status") or None),
                    (row.get("everify_plus") or None),
                    (row.get("date_enrolled") or None),
                    (row.get("hiring_sites") or None),
                )
            )
    with _LOCK:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {EVERIFY_TABLE}")
            if payload:
                cur.executemany(
                    f"INSERT INTO {EVERIFY_TABLE} "
                    "(name_key, employer, dba, account_status, everify_plus, date_enrolled, hiring_sites) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    payload,
                )
    _EVERIFY_INDEX = build_index(rows)
    return len(payload)


def everify_index() -> dict:
    global _EVERIFY_INDEX
    from jobscraper.everify import build_index, load_rows_from_csv

    if _EVERIFY_INDEX is not None:
        return _EVERIFY_INDEX
    rows = []
    if mysql_configured():
        conn = get_conn()
        if conn is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT employer, dba, account_status, everify_plus, date_enrolled, hiring_sites "
                        f"FROM {EVERIFY_TABLE}"
                    )
                    rows = list(cur.fetchall() or [])
            except Exception:
                close_conn()
                rows = []
    if not rows:
        imported = load_rows_from_csv()
        if imported and mysql_configured():
            replace_everify_employers(imported)
            return _EVERIFY_INDEX or {}
        rows = imported
    _EVERIFY_INDEX = build_index(rows)
    return _EVERIFY_INDEX


def everify_search_rows(company: str) -> list[dict]:
    """Employer rows whose name or DBA contains the company keyword."""
    from jobscraper.everify import load_rows_from_csv, normalize_name

    needle = re.sub(r"[%_\\]", "", (company or "").strip())
    key = normalize_name(needle)
    if len(needle) < 2 and len(key) < 2:
        return []
    if mysql_configured():
        conn = get_conn()
        if conn is not None:
            try:
                like = f"%{needle}%"
                key_like = f"%{key}%" if len(key) >= 2 else like
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT employer, dba, account_status, everify_plus, date_enrolled, hiring_sites "
                        f"FROM {EVERIFY_TABLE} "
                        f"WHERE employer LIKE %s OR dba LIKE %s OR name_key LIKE %s",
                        (like, like, key_like),
                    )
                    return list(cur.fetchall() or [])
            except Exception:
                close_conn()
    return load_rows_from_csv()


def everify_count() -> int:
    if not mysql_configured():
        return 0
    try:
        conn = get_conn()
        if conn is None:
            return 0
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {EVERIFY_TABLE}")
            row = cur.fetchone() or {}
        return int(row.get("n") or 0)
    except Exception:
        close_conn()
        return 0


def enrich_jobs_everify(*, only_unknown: bool = False) -> dict:
    """Set job e_verified from the E-Verify employer table in this database.

    A job stays unknown until that table has rows. Then a name match is true
    and a miss is false. only_unknown skips rows that were already checked.
    """
    from jobscraper.everify import match_company

    empty = {"updated": 0, "true": 0, "false": 0, "unknown": 0, "employers": 0}
    if not mysql_configured():
        return {**empty, "employers": everify_count()}
    conn = get_conn()
    if conn is None:
        return empty
    index = everify_index()
    employers = everify_count()
    if employers <= 0:
        return empty
    where = ""
    if only_unknown:
        where = " WHERE e_verified IS NULL OR TRIM(e_verified) = '' OR LOWER(e_verified) = 'unknown'"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, company FROM {JOBS_TABLE}{where}")
        jobs = list(cur.fetchall() or [])
        matched = 0
        missed = 0
        for job in jobs:
            hit = match_company(job.get("company") or "", index)
            if hit:
                matched += 1
                status = "true"
                name = hit.get("employer") or hit.get("dba")
            else:
                missed += 1
                status = "false"
                name = ""
            cur.execute(
                f"UPDATE {JOBS_TABLE} SET e_verified = %s, e_verify_name = %s WHERE id = %s",
                (status, (name or "")[:255], job.get("id")),
            )
    return {
        "updated": len(jobs),
        "true": matched,
        "false": missed,
        "unknown": 0,
        "employers": employers,
    }


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
            "everify_employers": everify_count(),
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
