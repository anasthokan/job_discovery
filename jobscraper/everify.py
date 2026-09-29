"""Match scraped employers against the public E-Verify enrolled-employer list.

USCIS has no public bulk API. Export the table from
https://www.e-verify.gov/e-verify-employer-search
(Tableau toolbar → Download → Crosstab / Data) and save as
data/everify_employers.csv (or set EVERIFY_CSV).
"""

from __future__ import annotations

import csv
import logging
import os
import re
from io import StringIO
from pathlib import Path

log = logging.getLogger("jobscraper.everify")

_ROOT = Path(__file__).resolve().parent.parent
SUFFIXES = re.compile(
    r"\b(incorporated|inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|"
    r"plc|lp|llp|pllc|pc|dba|gmbh|ag|sa|pte|pty)\b\.?",
    re.I,
)
NON_ALNUM = re.compile(r"[^a-z0-9]+")

HEADER_MAP = {
    "employer": "employer",
    "employername": "employer",
    "businessname": "employer",
    "name": "employer",
    "doingbusinessas": "dba",
    "dba": "dba",
    "dbaname": "dba",
    "accountstatus": "account_status",
    "status": "account_status",
    "optedintoeverify": "everify_plus",
    "optedintoeverifyplus": "everify_plus",
    "everifyplus": "everify_plus",
    "dateenrolled": "date_enrolled",
    "enrollmentdate": "date_enrolled",
    "hiringsitelocations": "hiring_sites",
    "hiringsites": "hiring_sites",
}


def csv_path() -> Path:
    raw = (os.getenv("EVERIFY_CSV") or "data/everify_employers.csv").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _ROOT / path
    return path


def normalize_name(value: str) -> str:
    text = SUFFIXES.sub(" ", str(value or "").lower())
    text = NON_ALNUM.sub("", text)
    return text.strip()


def _header_key(name: str) -> str:
    return NON_ALNUM.sub("", (name or "").lower())


def _open_status(value: str) -> bool:
    status = (value or "").strip().lower()
    return status in {"", "open", "active", "enrolled", "yes"}


def parse_employer_rows(text: str) -> list[dict]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(text), dialect=dialect)
    rows = []
    for raw in reader:
        mapped = {}
        for key, value in (raw or {}).items():
            field = HEADER_MAP.get(_header_key(key or ""))
            if field:
                mapped[field] = (value or "").strip()
        employer = mapped.get("employer") or ""
        dba = mapped.get("dba") or ""
        if not employer and not dba:
            continue
        mapped.setdefault("account_status", "")
        mapped.setdefault("everify_plus", "")
        mapped.setdefault("date_enrolled", "")
        mapped.setdefault("hiring_sites", "")
        mapped["employer"] = employer or dba
        mapped["dba"] = dba
        rows.append(mapped)
    return rows


def load_rows_from_csv(path: Path | None = None) -> list[dict]:
    path = path or csv_path()
    if not path.exists():
        return []
    data = path.read_bytes()
    if not data.strip():
        return []
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    return parse_employer_rows(text)


def build_index(rows: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for row in rows:
        if not _open_status(row.get("account_status") or ""):
            continue
        for name in (row.get("employer"), row.get("dba")):
            key = normalize_name(name or "")
            if len(key) < 4:
                continue
            index[key] = row
    return index


def match_company(company: str, index: dict[str, dict]) -> dict | None:
    key = normalize_name(company)
    if len(key) < 4:
        return None
    hit = index.get(key)
    if hit:
        return hit
    if len(key) < 8:
        return None
    for employer_key, row in index.items():
        if key in employer_key or employer_key in key:
            shorter, longer = sorted((key, employer_key), key=len)
            if len(shorter) >= 8 and longer.startswith(shorter):
                return row
    return None


# Values the E-Verify Checker form sends. Labels match the dropdown text.
STATE_FILTERS = (
    {"value": "All", "label": "All US States & Territories", "codes": ()},
    {"value": "California", "label": "California (SF Bay Area & LA)", "codes": ("CA",)},
    {"value": "Washington", "label": "Washington (Seattle / Redmond)", "codes": ("WA",)},
    {"value": "New York", "label": "New York (NYC Metro)", "codes": ("NY",)},
    {"value": "Texas", "label": "Texas (Austin / Dallas)", "codes": ("TX",)},
    {"value": "Massachusetts", "label": "Massachusetts (Boston / Cambridge)", "codes": ("MA",)},
)

# The public employer export has no industry column, so these are accepted
# and echoed. They do not narrow the result list.
INDUSTRY_FILTERS = (
    {"value": "All", "label": "All Industries"},
    {"value": "software", "label": "Software, Cloud & AI Tech"},
    {"value": "fintech", "label": "Fintech & Financial Services"},
    {"value": "healthtech", "label": "Healthtech & Biotech"},
    {"value": "defense", "label": "Defense & Aerospace"},
)

INDUSTRY_NOTE = (
    "The E-Verify employer list has no industry column, so Industry Category "
    "does not narrow these results."
)

_STATE_NAMES = {
    "CA": "CALIFORNIA",
    "WA": "WASHINGTON",
    "NY": "NEW YORK",
    "TX": "TEXAS",
    "MA": "MASSACHUSETTS",
}
_SITE_CODE = re.compile(r"\b[A-Z]{2}\b")


def filter_options() -> dict:
    return {
        "states": [{"value": row["value"], "label": row["label"]} for row in STATE_FILTERS],
        "industries": [{"value": row["value"], "label": row["label"]} for row in INDUSTRY_FILTERS],
    }


def resolve_state(value: str) -> dict:
    raw = (value or "").strip()
    if not raw:
        raw = "All"
    key = raw.casefold()
    for row in STATE_FILTERS:
        if key in {row["value"].casefold(), row["label"].casefold()}:
            return row
    allowed = ", ".join(row["value"] for row in STATE_FILTERS)
    raise ValueError(f"Unknown state. Use one of: {allowed}.")


def resolve_industry(value: str) -> dict:
    raw = (value or "").strip()
    if not raw:
        raw = "All"
    key = raw.casefold()
    for row in INDUSTRY_FILTERS:
        if key in {row["value"].casefold(), row["label"].casefold()}:
            return row
    allowed = ", ".join(row["value"] for row in INDUSTRY_FILTERS)
    raise ValueError(f"Unknown industry. Use one of: {allowed}.")


def _plus_flag(value: str) -> bool | None:
    text = (value or "").strip().lower()
    if text in {"yes", "y", "true", "1"}:
        return True
    if text in {"no", "n", "false", "0"}:
        return False
    return None


def _enrolled(status: str) -> bool:
    return _open_status(status) and (status or "").strip().lower() not in {"terminated", "closed"}


def stem_opt_status(status: str, found: bool) -> str:
    """Employer side of the 24-month STEM OPT extension.

    USCIS requires the employer to be enrolled in E-Verify. This does not
    decide whether a specific student or degree qualifies.
    """
    if not found:
        return "unknown"
    if _enrolled(status):
        return "eligible"
    return "not_eligible"


def hiring_site_codes(text: str) -> set[str]:
    raw = (text or "").strip().upper()
    if raw in {"", "NULL", "NONE", "N/A", "NA"}:
        return set()
    codes = set(_SITE_CODE.findall(raw))
    for code, name in _STATE_NAMES.items():
        if name in raw:
            codes.add(code)
    return codes


def name_matches(keyword: str, employer: str, dba: str) -> bool:
    key = normalize_name(keyword)
    if len(key) < 2:
        return False
    for raw in (employer, dba):
        other = normalize_name(raw or "")
        if not other:
            continue
        if key == other or key in other:
            return True
        if len(other) >= 4 and other in key:
            return True
    return False


def state_matches(hiring_sites: str, state: dict) -> bool:
    codes = state.get("codes") or ()
    if not codes:
        return True
    found = hiring_site_codes(hiring_sites)
    return any(code in found for code in codes)


def employer_result(row: dict) -> dict:
    status = (row.get("account_status") or "").strip()
    enrolled = _enrolled(status)
    sites = (row.get("hiring_sites") or "").strip()
    return {
        "employer": row.get("employer") or row.get("dba") or "",
        "dba": (row.get("dba") or "").strip() or None,
        "account_status": status or ("Open" if enrolled else None),
        "enrolled": enrolled,
        "e_verify_plus": _plus_flag(row.get("everify_plus") or ""),
        "date_enrolled": (row.get("date_enrolled") or "").strip() or None,
        "hiring_sites": sites or None,
        "hiring_site_states": sorted(hiring_site_codes(sites)),
        "mou_registered": enrolled,
        "stem_opt_24_month": stem_opt_status(status, True),
    }


def filter_employer_rows(rows: list[dict], company: str, state: dict) -> list[dict]:
    matched = []
    seen = set()
    for row in rows:
        employer = row.get("employer") or ""
        dba = row.get("dba") or ""
        if not name_matches(company, employer, dba):
            continue
        if not state_matches(row.get("hiring_sites") or "", state):
            continue
        result = employer_result(row)
        key = (
            normalize_name(result["employer"]),
            normalize_name(result.get("dba") or ""),
            (result.get("account_status") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        matched.append(result)
    matched.sort(key=lambda item: (not item["enrolled"], item["employer"].lower()))
    return matched


def lookup(company: str) -> dict:
    from jobscraper.db import everify_index

    row = match_company(company, everify_index())
    if not row:
        return {
            "e_verified": "unknown",
            "e_verify_name": None,
            "e_verify_status": None,
            "e_verify_plus": None,
            "enrolled": False,
            "mou_registered": False,
            "stem_opt_24_month": "unknown",
        }
    result = employer_result(row)
    return {
        "e_verified": "yes" if result["enrolled"] else "unknown",
        "e_verify_name": result["employer"],
        "e_verify_status": result["account_status"],
        "e_verify_plus": row.get("everify_plus") or None,
        "enrolled": result["enrolled"],
        "mou_registered": result["mou_registered"],
        "date_enrolled": result["date_enrolled"],
        "hiring_sites": result["hiring_sites"],
        "stem_opt_24_month": result["stem_opt_24_month"],
    }


def search(company: str, state: str = "All", industry: str = "All", limit: int = 25, offset: int = 0) -> dict:
    from jobscraper.db import everify_count, everify_search_rows

    state_row = resolve_state(state)
    industry_row = resolve_industry(industry)
    loaded = everify_search_rows(company)
    matched = filter_employer_rows(loaded, company, state_row)
    page = matched[offset:offset + limit]
    if not matched:
        summary = "unknown"
    elif any(item["stem_opt_24_month"] == "eligible" for item in matched):
        summary = "eligible"
    else:
        summary = "not_eligible"
    employers = everify_count()
    ready = employers > 0 or bool(loaded)
    return {
        "ok": True,
        "company": company.strip(),
        "state": state_row["value"],
        "state_label": state_row["label"],
        "industry": industry_row["value"],
        "industry_label": industry_row["label"],
        "industry_applied": False,
        "industry_note": INDUSTRY_NOTE,
        "database_ready": ready,
        "count": len(page),
        "total": len(matched),
        "limit": limit,
        "offset": offset,
        "stem_opt_24_month": summary,
        "message": _search_message(ready, len(matched), summary, state_row["value"]),
        "results": page,
    }


def _search_message(ready: bool, total: int, summary: str, state_value: str) -> str:
    if not ready:
        return "E-Verify employer list is not loaded yet."
    if total == 0:
        where = " in the selected state" if state_value != "All" else ""
        return (
            f"No enrollment record matched this company name{where}. "
            "That is not a confirmed No — legal name and DBA often differ."
        )
    if summary == "eligible":
        return (
            "At least one matched employer is enrolled in E-Verify. "
            "That meets the employer requirement for a 24-month STEM OPT extension."
        )
    return (
        "Matched employers are not currently enrolled, so they do not meet "
        "the E-Verify requirement for a 24-month STEM OPT extension."
    )
