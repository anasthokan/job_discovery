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


def lookup(company: str) -> dict:
    from jobscraper.db import everify_index

    row = match_company(company, everify_index())
    if not row:
        return {
            "e_verified": "unknown",
            "e_verify_name": None,
            "e_verify_status": None,
            "e_verify_plus": None,
        }
    return {
        "e_verified": "yes",
        "e_verify_name": row.get("employer") or row.get("dba"),
        "e_verify_status": row.get("account_status") or "Open",
        "e_verify_plus": row.get("everify_plus") or None,
    }
