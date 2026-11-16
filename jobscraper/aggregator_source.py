"""Keyed job aggregators: Adzuna, Jooble, Careerjet, and USAJOBS Search.

Each source is skipped when its env keys are missing so a full scrape still
runs the public ATS boards. Keys stay out of Scrapy request logs.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger("jobscraper.aggregators")

KEYED_PLATFORMS = ("Adzuna", "Jooble", "Careerjet", "USAJobs")
MAX_TERMS = 6
PAGES = 2
PAGE_SIZE = 20
TIMEOUT = 25
USER_AGENT = "JobDiscovery/1.0 (personal job search aggregator)"


def _env(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _wanted(selected: str, name: str) -> bool:
    return selected in ("All", name)


def _blank(value):
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value).strip()
    return text or None


def _skills(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        names = []
        for item in value:
            if isinstance(item, dict):
                names.append(str(item.get("Name") or item.get("name") or "").strip())
            else:
                names.append(str(item).strip())
        return [name for name in names if name][:8]
    return [part.strip() for part in str(value).split(",") if part.strip()][:8]


def _job(
    *,
    ident: str,
    title: str,
    company: str,
    location: str,
    platform: str,
    url: str,
    posted_at: str = "",
    job_type: str = "",
    search_text: str = "",
    skills=None,
    raw=None,
) -> dict | None:
    title = _blank(title)
    url = _blank(url)
    if not title or not url:
        return None
    place = _blank(location) or "United States"
    return {
        "id": ident[:255],
        "title": title,
        "company": _blank(company) or "Unknown",
        "skills": skills or [],
        "location": place,
        "platform": platform,
        "url": url,
        "posted_at": _blank(posted_at) or "",
        "job_type": _blank(job_type) or "",
        "search_text": search_text or "",
        "raw": raw,
    }


def _safe_url(url: str) -> str:
    if "jooble.org/api/" in url:
        return "https://jooble.org/api/***"
    return url.split("?")[0]


def _read_json(url: str, *, data: bytes | None = None, headers: dict | None = None):
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    request = Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        log.warning("%s returned HTTP %s", _safe_url(url), exc.code)
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        log.warning("%s failed: %s", _safe_url(url), exc)
    return None


def _terms(search_terms: list[str]) -> list[str]:
    return [term.strip() for term in search_terms if term and term.strip()][:MAX_TERMS]


def _adzuna(terms: list[str], location: str, is_remote: bool) -> list[dict]:
    app_id = _env("ADZUNA_APP_ID")
    app_key = _env("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        log.warning("Adzuna skipped: set ADZUNA_APP_ID and ADZUNA_APP_KEY")
        return []
    found = []
    where = location or "United States"
    for term in terms:
        what = f"{term} remote" if is_remote else term
        for page in range(1, PAGES + 1):
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": PAGE_SIZE,
                "what": what,
                "where": where,
                "max_days_old": 30,
                "content-type": "application/json",
            }
            url = f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?{urlencode(params)}"
            payload = _read_json(url)
            rows = (payload or {}).get("results") or []
            if not rows:
                break
            for row in rows:
                company = (row.get("company") or {}).get("display_name") or "Unknown"
                loc = (row.get("location") or {}).get("display_name") or where
                ident = str(row.get("id") or row.get("redirect_url") or "")
                job = _job(
                    ident=f"adzuna-{ident}",
                    title=row.get("title") or "",
                    company=company,
                    location=loc,
                    platform="Adzuna",
                    url=row.get("redirect_url") or row.get("adref") or "",
                    posted_at=str(row.get("created") or ""),
                    job_type=row.get("contract_time") or row.get("contract_type") or "",
                    search_text=row.get("description") or "",
                    skills=_skills((row.get("category") or {}).get("label")),
                    raw=row,
                )
                if job:
                    found.append(job)
    return found


def _jooble(terms: list[str], location: str, is_remote: bool) -> list[dict]:
    key = _env("JOOBLE_API_KEY")
    if not key:
        log.warning("Jooble skipped: set JOOBLE_API_KEY")
        return []
    found = []
    where = location or "United States"
    endpoint = f"https://jooble.org/api/{key}"
    for term in terms:
        keywords = f"{term} remote" if is_remote else term
        for page in range(1, PAGES + 1):
            body = json.dumps(
                {
                    "keywords": keywords,
                    "location": where,
                    "page": page,
                    "ResultOnPage": PAGE_SIZE,
                }
            ).encode()
            payload = _read_json(
                endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
            )
            rows = (payload or {}).get("jobs") or []
            if not rows:
                break
            for row in rows:
                ident = str(row.get("id") or row.get("link") or "")
                job = _job(
                    ident=f"jooble-{ident}",
                    title=row.get("title") or "",
                    company=row.get("company") or "Unknown",
                    location=row.get("location") or where,
                    platform="Jooble",
                    url=row.get("link") or "",
                    posted_at=str(row.get("updated") or ""),
                    job_type=row.get("type") or "",
                    search_text=row.get("snippet") or "",
                    raw=row,
                )
                if job:
                    found.append(job)
    return found


def _careerjet(terms: list[str], location: str, is_remote: bool) -> list[dict]:
    api_key = _env("CAREERJET_API_KEY")
    affid = _env("CAREERJET_AFFID")
    if not api_key and not affid:
        log.warning("Careerjet skipped: set CAREERJET_API_KEY or CAREERJET_AFFID")
        return []
    found = []
    where = location or "United States"
    user_ip = _env("CAREERJET_USER_IP") or "8.8.8.8"
    headers = {}
    if api_key:
        token = base64.b64encode(f"{api_key}:".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
        base = "https://search.api.careerjet.net/v4/query"
    else:
        base = "https://public.api.careerjet.net/search"
    for term in terms:
        keywords = f"{term} remote" if is_remote else term
        for page in range(1, PAGES + 1):
            params = {
                "locale_code": "en_US",
                "keywords": keywords,
                "location": where,
                "page": page,
                "page_size": PAGE_SIZE,
                "user_ip": user_ip,
                "user_agent": USER_AGENT,
                "sort": "date",
            }
            if affid:
                params["affid"] = affid
            payload = _read_json(f"{base}?{urlencode(params)}", headers=headers)
            rows = (payload or {}).get("jobs") or []
            if not rows:
                break
            for row in rows:
                ident = str(row.get("url") or row.get("title") or "")
                job = _job(
                    ident=f"careerjet-{ident}",
                    title=row.get("title") or "",
                    company=row.get("company") or "Unknown",
                    location=row.get("locations") or row.get("location") or where,
                    platform="Careerjet",
                    url=row.get("url") or "",
                    posted_at=str(row.get("date") or ""),
                    search_text=row.get("description") or row.get("snippet") or "",
                    raw=row,
                )
                if job:
                    found.append(job)
    return found


def _usajobs(terms: list[str], location: str, is_remote: bool) -> list[dict]:
    key = _env("USAJOBS_API_KEY", "USAJOBS_AUTH_KEY")
    email = _env("USAJOBS_EMAIL", "USAJOBS_USER_AGENT")
    if not key or not email:
        log.warning("USAJobs search skipped: set USAJOBS_API_KEY and USAJOBS_EMAIL")
        return []
    found = []
    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": email,
        "Authorization-Key": key,
    }
    loc = "" if location in ("", "United States") else location
    for term in terms:
        for page in range(1, PAGES + 1):
            params = {
                "Keyword": term,
                "ResultsPerPage": 25,
                "Page": page,
                "DatePosted": 30,
            }
            if loc:
                params["LocationName"] = loc
            if is_remote:
                params["RemoteIndicator"] = "true"
            payload = _read_json(
                f"https://data.usajobs.gov/api/search?{urlencode(params)}",
                headers=headers,
            )
            result = (payload or {}).get("SearchResult") or {}
            rows = result.get("SearchResultItems") or []
            if not rows:
                break
            for row in rows:
                desc = row.get("MatchedObjectDescriptor") or {}
                ident = str(row.get("MatchedObjectId") or desc.get("PositionID") or "")
                details = ((desc.get("UserArea") or {}).get("Details") or {})
                summary = desc.get("QualificationSummary") or details.get("JobSummary") or ""
                job = _job(
                    ident=f"usajobs-{ident}",
                    title=desc.get("PositionTitle") or "",
                    company=desc.get("OrganizationName") or "USAJobs",
                    location=desc.get("PositionLocationDisplay") or "United States",
                    platform="USAJobs",
                    url=desc.get("PositionURI") or "",
                    posted_at=str(desc.get("PublicationStartDate") or ""),
                    job_type=(desc.get("PositionSchedule") or [{}])[0].get("Name")
                    if isinstance(desc.get("PositionSchedule"), list)
                    else "",
                    search_text=summary,
                    skills=_skills(desc.get("JobCategory")),
                    raw=row,
                )
                if job:
                    found.append(job)
    return found


def scrape(
    selected: str,
    search_terms: list[str],
    location: str,
    *,
    is_remote: bool = False,
) -> list[dict]:
    """Return job dicts from keyed aggregators. Empty when keys are missing."""
    selected = selected or "All"
    if selected != "All" and selected not in KEYED_PLATFORMS:
        return []
    terms = _terms(search_terms)
    if not terms:
        return []
    found: list[dict] = []
    if _wanted(selected, "Adzuna"):
        found.extend(_adzuna(terms, location, is_remote))
    if _wanted(selected, "Jooble"):
        found.extend(_jooble(terms, location, is_remote))
    if _wanted(selected, "Careerjet"):
        found.extend(_careerjet(terms, location, is_remote))
    if _wanted(selected, "USAJobs"):
        found.extend(_usajobs(terms, location, is_remote))
    log.info("Aggregators returned %s jobs", len(found))
    return found
