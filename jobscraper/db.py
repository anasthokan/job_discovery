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
from datetime import date, datetime, timezone
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
  raw_description LONGTEXT NULL,
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
  platform, days_ago, url, description, raw_description, posted_at, is_active, e_verified, e_verify_name,
  job_type, work_model, experience_level, years_experience, h1b_sponsorship, clearance_required,
  us_citizen_required, listing_inferred, first_seen_at, last_seen_at, created_at, updated_at
) VALUES (
  %s, %s, %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s, %s, %s, 1, 'unknown', '',
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
  raw_description = COALESCE(new.raw_description, {JOBS_TABLE}.raw_description),
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
    cur.execute(f"SHOW COLUMNS FROM {JOBS_TABLE} LIKE 'raw_description'")
    if not cur.fetchone():
        cur.execute(f"ALTER TABLE {JOBS_TABLE} ADD COLUMN raw_description LONGTEXT NULL")
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
        clear_copied_api_raw_descriptions()
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


def clear_copied_api_raw_descriptions() -> int:
    """Remove raw_description values that were copied from our own jobs API fields.

    The real value is the source payload captured while scraping. Those copies
    are marked by api_id, which source boards do not send.
    """
    if not mysql_configured():
        return 0
    conn = get_conn()
    if conn is None:
        return 0
    try:
        with _LOCK:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {JOBS_TABLE} SET raw_description = NULL "
                    f"WHERE raw_description IS NOT NULL "
                    f"AND CAST(raw_description AS CHAR) LIKE %s",
                    ('%"api_id"%',),
                )
                cleared = int(cur.rowcount or 0)
        if cleared:
            log.info("Cleared copied API fields from raw_description on %s jobs", cleared)
        return cleared
    except Exception:
        close_conn()
        log.exception("raw_description cleanup failed")
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


def _json_ready(value):
    """Turn API payloads into values json.dumps can store."""
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "ignore")
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "item"):
        try:
            return _json_ready(value.item())
        except Exception:
            pass
    return str(value)


_API_JOB_KEYS = {
    "id",
    "title",
    "company",
    "skills",
    "state",
    "location",
    "platform",
    "daysAgo",
    "url",
    "description",
    "job_type",
    "work_model",
    "experience_level",
    "years_experience",
    "h1b_sponsorship",
    "clearance_required",
    "us_citizen_required",
    "posted_at",
    "e_verified",
    "e_verify_name",
    "api_id",
    "search_text",
}


def _is_copied_api_job(value: dict) -> bool:
    """True when this object is our jobs API row, not a scraped source payload."""
    if "api_id" in value:
        return True
    copied = {"e_verified", "e_verify_name", "daysAgo", "us_citizen_required"}
    return copied.issubset(value) and set(value).issubset(_API_JOB_KEYS)


def _raw_description_json(job: dict) -> str | None:
    """JSON text of the scraped source job. Our own API fields are not stored."""
    raw = job.get("raw")
    if raw is None or raw == "":
        raw = job.get("raw_description")
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text in {"{}", "[]", "null"}:
            return None
        if text[:1] in "{[":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text[:500000]
            return _raw_description_json({"raw": parsed})
        return text[:500000]
    if isinstance(raw, dict):
        if not raw or _is_copied_api_job(raw):
            return None
        try:
            return json.dumps(_json_ready(raw), ensure_ascii=False)[:500000]
        except (TypeError, ValueError):
            return None
    if isinstance(raw, list):
        if not raw:
            return None
        try:
            return json.dumps(_json_ready(raw), ensure_ascii=False)[:500000]
        except (TypeError, ValueError):
            return None
    return None


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
        "raw_description": _raw_description_json(job),
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
                        row["raw_description"],
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


def _parse_raw_description(value):
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return value


def _job_payload(row: dict, *, include_raw: bool) -> dict:
    posted = row.get("posted_at")
    if isinstance(posted, datetime):
        posted = posted.strftime("%Y-%m-%d %H:%M:%S")
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
    if include_raw:
        payload["raw_description"] = _parse_raw_description(row.get("raw_description"))
    return payload


def _job_from_row(row: dict) -> dict:
    return _job_payload(row, include_raw=True)


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
                f"days_ago, url, description, raw_description, job_type, work_model, experience_level, "
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


_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _quote_ident(name: str) -> str:
    if not _IDENT_RE.fullmatch(name or ""):
        raise ValueError(f"Unsafe SQL name: {name}")
    return f"`{name}`"


def load_app_user(candidate_id: str) -> dict | None:
    """Read an ezyjob.users row (numeric id or public_id) into a recommend profile."""
    if not mysql_configured():
        return None
    conn = get_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            if str(candidate_id).isdigit():
                cur.execute(
                    "SELECT id, email, first_name, last_name FROM users WHERE id = %s LIMIT 1",
                    (int(candidate_id),),
                )
            else:
                cur.execute(
                    "SELECT id, email, first_name, last_name FROM users WHERE public_id = %s LIMIT 1",
                    (candidate_id,),
                )
            user = cur.fetchone()
            if not user:
                return None
            user_id = int(user["id"])
            profile_row = _profile_row(cur, user_id)
            skills = _user_skill_values(cur, user_id, profile_row.get("profile_id"))
            skills.extend(_linked_names(cur, user_id, profile_row.get("profile_id")))
            extra_text = _user_text(cur, user_id, profile_row.get("profile_id"))
            extra_text = "\n".join(
                part for part in (extra_text, _resume_text_from_stored_files(cur, user_id, profile_row.get("profile_id"))) if part
            )
    except Exception:
        log.exception("load_app_user failed for %s", candidate_id)
        close_conn()
        raise
    summary = " ".join(
        part
        for part in (
            profile_row.get("headline") or "",
            profile_row.get("professional_summary") or "",
            extra_text,
        )
        if part
    )
    return {
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
        "email": user.get("email") or "",
        "phone": profile_row.get("phone") or "",
        "city": profile_row.get("city") or "",
        "linkedin_url": profile_row.get("linkedin_url") or "",
        "skills": skills,
        "resume_text": summary,
    }


def _profile_row(cur, user_id: int) -> dict:
    try:
        cur.execute(
            "SELECT id AS profile_id, phone, headline, professional_summary, linkedin_url, city "
            "FROM profiles WHERE user_id = %s LIMIT 1",
            (user_id,),
        )
        return cur.fetchone() or {}
    except Exception:
        log.exception("profiles lookup failed for user %s", user_id)
        return {}


def _user_skill_values(cur, user_id: int, profile_id) -> list[str]:
    cur.execute(
        """
        SELECT c.TABLE_NAME AS table_name,
               c.COLUMN_NAME AS skill_column,
               u.COLUMN_NAME AS owner_column
        FROM information_schema.COLUMNS c
        JOIN information_schema.COLUMNS u
          ON u.TABLE_SCHEMA = c.TABLE_SCHEMA
         AND u.TABLE_NAME = c.TABLE_NAME
         AND u.COLUMN_NAME IN ('user_id', 'profile_id')
        WHERE c.TABLE_SCHEMA = DATABASE()
          AND (c.COLUMN_NAME LIKE %s OR c.TABLE_NAME LIKE %s)
          AND c.DATA_TYPE IN ('varchar', 'char', 'text', 'mediumtext', 'longtext', 'json')
          AND c.COLUMN_NAME NOT IN ('user_id', 'profile_id', 'id')
        """,
        ("%skill%", "%skill%"),
    )
    found: list[str] = []
    for row in cur.fetchall() or []:
        owner = row.get("owner_column")
        key = user_id if owner == "user_id" else profile_id
        if key is None:
            continue
        table = str(row.get("table_name") or "")
        column = str(row.get("skill_column") or "")
        if not _IDENT_RE.fullmatch(table) or not _IDENT_RE.fullmatch(column) or owner not in {"user_id", "profile_id"}:
            continue
        try:
            cur.execute(
                f"SELECT {_quote_ident(column)} AS skill FROM {_quote_ident(table)} "
                f"WHERE {_quote_ident(owner)} = %s LIMIT 40",
                (key,),
            )
            skill_rows = cur.fetchall() or []
        except Exception:
            log.exception("skill lookup failed on %s.%s", table, column)
            continue
        for skill_row in skill_rows:
            value = skill_row.get("skill")
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("utf-8", "ignore")
            if isinstance(value, str) and value.strip().startswith("["):
                parsed = _profile_from_cell(value)
                if isinstance(parsed, list):
                    found.extend(str(item) for item in parsed)
                    continue
            if value:
                found.append(str(value))
    return found


def _owner_links(cur, column_like: str | None = None, column_names: tuple[str, ...] = ()) -> list[dict]:
    filters = ["u.COLUMN_NAME IN ('user_id', 'profile_id', 'candidate_id')"]
    params: list[str] = []
    if column_like:
        filters.append("c.COLUMN_NAME LIKE %s")
        params.append(column_like)
    if column_names:
        marks = ", ".join(["%s"] * len(column_names))
        filters.append(f"c.COLUMN_NAME IN ({marks})")
        params.extend(column_names)
    cur.execute(
        f"""
        SELECT c.TABLE_NAME AS table_name,
               c.COLUMN_NAME AS value_column,
               u.COLUMN_NAME AS owner_column
        FROM information_schema.COLUMNS c
        JOIN information_schema.COLUMNS u
          ON u.TABLE_SCHEMA = c.TABLE_SCHEMA
         AND u.TABLE_NAME = c.TABLE_NAME
         AND u.COLUMN_NAME IN ('user_id', 'profile_id', 'candidate_id')
        WHERE c.TABLE_SCHEMA = DATABASE()
          AND {" AND ".join(filters)}
        """,
        params,
    )
    return list(cur.fetchall() or [])


def _rows_for_owner(cur, table: str, column: str, owner: str, key) -> list[dict]:
    if not _IDENT_RE.fullmatch(table) or not _IDENT_RE.fullmatch(column) or not _IDENT_RE.fullmatch(owner):
        return []
    try:
        cur.execute(
            f"SELECT {_quote_ident(column)} AS value FROM {_quote_ident(table)} "
            f"WHERE {_quote_ident(owner)} = %s LIMIT 30",
            (key,),
        )
        return list(cur.fetchall() or [])
    except Exception:
        log.exception("lookup failed on %s.%s", table, column)
        return []


def _linked_names(cur, user_id: int, profile_id) -> list[str]:
    """Resolve skill_id / target_role_id links to their display names."""
    catalogs = []
    try:
        cur.execute(
            """
            SELECT TABLE_NAME AS table_name, COLUMN_NAME AS name_column
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME LIKE %s
              AND COLUMN_NAME IN ('name', 'title', 'label', 'skill_name')
            """,
            ("%skill%",),
        )
        catalogs.extend(cur.fetchall() or [])
        cur.execute(
            """
            SELECT TABLE_NAME AS table_name, COLUMN_NAME AS name_column
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME LIKE %s
              AND COLUMN_NAME IN ('name', 'title', 'label')
            """,
            ("%target_role%",),
        )
        catalogs.extend(cur.fetchall() or [])
    except Exception:
        log.exception("catalog lookup failed")
        return []
    found: list[str] = []
    try:
        links = _owner_links(cur, column_like="%id")
    except Exception:
        log.exception("link lookup failed")
        return found
    for link in links:
        owner = link.get("owner_column")
        key = user_id if owner == "user_id" else profile_id
        if key is None or owner == "candidate_id":
            key = user_id if owner == "candidate_id" else key
        fk = str(link.get("value_column") or "")
        if not any(token in fk for token in ("skill", "role")):
            continue
        table = str(link.get("table_name") or "")
        for catalog in catalogs:
            cat_table = str(catalog.get("table_name") or "")
            cat_name = str(catalog.get("name_column") or "")
            if cat_table == table or not _IDENT_RE.fullmatch(cat_table) or not _IDENT_RE.fullmatch(cat_name):
                continue
            if not _IDENT_RE.fullmatch(table) or not _IDENT_RE.fullmatch(fk) or owner not in {"user_id", "profile_id", "candidate_id"}:
                continue
            if key is None:
                continue
            try:
                cur.execute(
                    f"SELECT cat.{_quote_ident(cat_name)} AS skill "
                    f"FROM {_quote_ident(table)} link "
                    f"JOIN {_quote_ident(cat_table)} cat ON cat.id = link.{_quote_ident(fk)} "
                    f"WHERE link.{_quote_ident(owner)} = %s LIMIT 40",
                    (key,),
                )
                for row in cur.fetchall() or []:
                    if row.get("skill"):
                        found.append(str(row["skill"]))
            except Exception:
                continue
    return found


_TEXT_COLUMNS = (
    "headline",
    "professional_summary",
    "summary",
    "description",
    "answer",
    "job_title",
    "title",
    "resume_text",
    "extracted_text",
    "content",
    "body",
    "degree",
    "field_of_study",
)


def _user_text(cur, user_id: int, profile_id) -> str:
    chunks: list[str] = []
    try:
        links = _owner_links(cur, column_names=_TEXT_COLUMNS)
    except Exception:
        log.exception("profile text lookup failed")
        return ""
    for link in links:
        owner = str(link.get("owner_column") or "")
        key = {"user_id": user_id, "profile_id": profile_id, "candidate_id": user_id}.get(owner)
        if key is None:
            continue
        for row in _rows_for_owner(
            cur,
            str(link.get("table_name") or ""),
            str(link.get("value_column") or ""),
            owner,
            key,
        ):
            value = row.get("value")
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip()[:4000])
    return "\n".join(chunks)[:20000]


def _resume_text_from_stored_files(cur, user_id: int, profile_id) -> str:
    """Parse a resume file or blob saved on the user or profile row."""
    try:
        from jobscraper.resume_parser import parse_resume_bytes
    except Exception:
        return ""
    texts: list[str] = []
    try:
        links = _owner_links(
            cur,
            column_names=("file", "file_path", "path", "resume", "document", "upload", "attachment"),
        )
    except Exception:
        log.exception("resume path lookup failed")
        links = []
    for link in links:
        owner = str(link.get("owner_column") or "")
        key = {"user_id": user_id, "profile_id": profile_id, "candidate_id": user_id}.get(owner)
        if key is None:
            continue
        for row in _rows_for_owner(
            cur,
            str(link.get("table_name") or ""),
            str(link.get("value_column") or ""),
            owner,
            key,
        ):
            text = _text_from_resume_value(row.get("value"), parse_resume_bytes)
            if text:
                texts.append(text)
    if texts:
        return "\n".join(texts)[:20000]
    try:
        cur.execute(
            """
            SELECT c.TABLE_NAME AS table_name,
                   c.COLUMN_NAME AS value_column,
                   u.COLUMN_NAME AS owner_column
            FROM information_schema.COLUMNS c
            JOIN information_schema.COLUMNS u
              ON u.TABLE_SCHEMA = c.TABLE_SCHEMA
             AND u.TABLE_NAME = c.TABLE_NAME
             AND u.COLUMN_NAME IN ('user_id', 'profile_id')
            WHERE c.TABLE_SCHEMA = DATABASE()
              AND c.DATA_TYPE IN ('blob', 'mediumblob', 'longblob')
              AND (c.COLUMN_NAME LIKE %s OR c.COLUMN_NAME LIKE %s OR c.COLUMN_NAME LIKE %s)
            """,
            ("%resume%", "%file%", "%document%"),
        )
        blobs = list(cur.fetchall() or [])
    except Exception:
        log.exception("resume blob lookup failed")
        return ""
    for link in blobs:
        owner = str(link.get("owner_column") or "")
        key = user_id if owner == "user_id" else profile_id
        if key is None:
            continue
        table = str(link.get("table_name") or "")
        column = str(link.get("value_column") or "")
        if not _IDENT_RE.fullmatch(table) or not _IDENT_RE.fullmatch(column) or not _IDENT_RE.fullmatch(owner):
            continue
        try:
            cur.execute(
                f"SELECT {_quote_ident(column)} AS value FROM {_quote_ident(table)} "
                f"WHERE {_quote_ident(owner)} = %s LIMIT 1",
                (key,),
            )
            row = cur.fetchone() or {}
        except Exception:
            continue
        text = _text_from_resume_value(row.get("value"), parse_resume_bytes)
        if text:
            texts.append(text)
    return "\n".join(texts)[:20000]


def _text_from_resume_value(value, parse_resume_bytes) -> str:
    if isinstance(value, str):
        path = Path(value.strip())
        if path.suffix.lower() in {".pdf", ".docx", ".txt"} and path.is_file():
            try:
                parsed = parse_resume_bytes(path.read_bytes(), filename=path.name)
            except Exception:
                return ""
            return str(parsed.get("raw_text") or "")[:20000]
        return ""
    if isinstance(value, (bytes, bytearray)) and len(value) > 100:
        try:
            parsed = parse_resume_bytes(bytes(value), filename="resume.pdf")
        except Exception:
            return ""
        return str(parsed.get("raw_text") or "")[:20000]
    return ""


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
