"""USCIS H-1B Employer Data Hub: companies with at least one approval.

Official yearly files, fiscal years 2009–2023:
https://www.uscis.gov/archive/h-1b-employer-data-hub-files

A later fiscal year is not published as a static CSV. One approval in any
of these years is enough to treat the employer as an H-1B sponsor.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import subprocess
from io import StringIO
from pathlib import Path

from jobscraper.everify import normalize_name

log = logging.getLogger("jobscraper.h1b")

_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _ROOT / "data" / "h1b"
SOURCE_URL = "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{year}.csv"
FISCAL_YEARS = tuple(range(2009, 2024))

_CORP_TAILS = {
    "inc",
    "incorporated",
    "llc",
    "corp",
    "corporation",
    "company",
    "co",
    "ltd",
    "limited",
    "plc",
    "lp",
    "llp",
    "pllc",
    "pc",
    "na",
    "dba",
}
_BUSINESS_TAILS = {
    "com",
    "services",
    "service",
    "solutions",
    "technologies",
    "technology",
    "tech",
    "labs",
    "lab",
    "group",
    "holdings",
    "holding",
    "international",
    "global",
    "usa",
    "us",
    "americas",
    "enterprises",
    "enterprise",
    "systems",
    "system",
    "software",
    "consulting",
    "partners",
    "partner",
    "platforms",
    "platform",
    "web",
}
_GENERIC_BRANDS = _BUSINESS_TAILS | {"and", "the"}
_DBA = re.compile(r"\bdba\b\s+(.+)$", re.I)
_TOKEN = re.compile(r"[a-z0-9]+")


def _header_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _tokens(value: str) -> list[str]:
    return _TOKEN.findall(str(value or "").lower())


def brand_key(value: str) -> str:
    """Short name used to match a job's company to a legal petitioner.

    'AMAZON.COM SERVICES LLC' and a posting at 'Amazon Web Services (AWS)' share 'amazon'.
    """
    text = re.sub(r"\([^)]*\)", " ", str(value or ""))
    tokens = _tokens(text)
    while len(tokens) > 1 and tokens[-1] in _CORP_TAILS | _BUSINESS_TAILS:
        tokens.pop()
    while len(tokens) > 1 and tokens[-1] == "and":
        tokens.pop()
    while tokens and tokens[0] == "the":
        tokens.pop(0)
    return "".join(tokens)


def sponsor_keys(name: str) -> list[str]:
    """Lookup keys for a job company or a USCIS employer row."""
    keys: list[str] = []
    for raw in _name_forms(name):
        full = normalize_name(raw)[:255]
        brand = brand_key(raw)[:255]
        if len(full) >= 4 and full not in keys:
            keys.append(full)
        if len(brand) >= 4 and brand not in keys and brand not in _GENERIC_BRANDS:
            keys.append(brand)
    return keys


def _name_forms(name: str) -> list[str]:
    text = str(name or "").strip()
    if not text:
        return []
    forms = [text]
    match = _DBA.search(text)
    if match:
        dba = match.group(1).strip(" ,.-")
        if dba:
            forms.append(dba)
    return forms


def _number(value: object) -> int:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def _read_csv(path: Path) -> list[dict]:
    data = path.read_bytes()
    if not data.strip():
        return []
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(StringIO(text), dialect=dialect))


def _column(row: dict, *names: str) -> str:
    wanted = set(names)
    for key, value in row.items():
        if _header_key(key or "") in wanted:
            return str(value or "").strip()
    return ""


def _sum_columns(row: dict, suffix: str) -> int:
    total = 0
    plural = suffix + "s"
    for key, value in row.items():
        header = _header_key(key or "")
        if header.endswith(suffix) or header.endswith(plural):
            total += _number(value)
    return total


def parse_sponsor_file(path: Path, *, fiscal_year: int | None = None) -> list[dict]:
    parsed = []
    for row in _read_csv(path):
        employer = _column(row, "employer", "employerpetitionername", "petitioner", "employername")
        if not employer:
            continue
        approvals = _sum_columns(row, "approval")
        denials = _sum_columns(row, "denial")
        year_text = _column(row, "fiscalyear", "year")
        year = _number(year_text) or int(fiscal_year or 0)
        if year < 1990 or year > 2100:
            year = int(fiscal_year or 0)
        parsed.append(
            {
                "employer": employer,
                "approvals": approvals,
                "denials": denials,
                "fiscal_year": year,
            }
        )
    return parsed


def aggregate_sponsors(rows: list[dict]) -> list[dict]:
    """One row per normalized employer. Keep only employers with an approval."""
    buckets: dict[str, dict] = {}
    for row in rows:
        employer = str(row.get("employer") or "").strip()
        key = normalize_name(employer)[:255]
        if len(key) < 4:
            continue
        approvals = int(row.get("approvals") or 0)
        denials = int(row.get("denials") or 0)
        year = int(row.get("fiscal_year") or 0)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = {
                "name_key": key,
                "employer": employer[:512],
                "approvals": approvals,
                "denials": denials,
                "first_fiscal_year": year or 9999,
                "last_fiscal_year": year,
                "best_n": approvals,
            }
            continue
        bucket["approvals"] += approvals
        bucket["denials"] += denials
        if year:
            bucket["first_fiscal_year"] = min(bucket["first_fiscal_year"], year)
            bucket["last_fiscal_year"] = max(bucket["last_fiscal_year"], year)
        if approvals >= bucket["best_n"]:
            bucket["best_n"] = approvals
            bucket["employer"] = employer[:512]
    sponsors = []
    for bucket in buckets.values():
        if bucket["approvals"] <= 0:
            continue
        if bucket["first_fiscal_year"] == 9999:
            bucket["first_fiscal_year"] = bucket["last_fiscal_year"]
        bucket.pop("best_n", None)
        sponsors.append(bucket)
    return sponsors


def download_year(year: int, dest: Path | None = None) -> Path | None:
    dest = dest or (DATA_DIR / f"h1b_datahubexport-{year}.csv")
    if dest.exists() and dest.stat().st_size > 500:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = SOURCE_URL.format(year=year)
    curl = "curl.exe" if os.name == "nt" else "curl"
    try:
        result = subprocess.run(
            [
                curl,
                "-fsSL",
                "--retry",
                "2",
                "--max-time",
                "180",
                "-A",
                "Mozilla/5.0",
                "-o",
                str(dest),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=200,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("H-1B file for FY%s was not downloaded: %s", year, exc)
        return None
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size < 500:
        dest.unlink(missing_ok=True)
        log.warning("H-1B file for FY%s was not downloaded: %s", year, (result.stderr or "").strip()[:200])
        return None
    if dest.read_bytes()[:1] == b"<":
        dest.unlink(missing_ok=True)
        log.warning("H-1B file for FY%s was not a CSV", year)
        return None
    return dest


def collect_sponsors(years: tuple[int, ...] = FISCAL_YEARS) -> list[dict]:
    """Download the USCIS yearly files and return employers with an approval."""
    combined: list[dict] = []
    for year in years:
        path = download_year(year)
        if path is None:
            continue
        parsed = parse_sponsor_file(path, fiscal_year=year)
        combined.extend(parsed)
        log.info("Read H-1B FY%s (%s petition rows)", year, len(parsed))
    return aggregate_sponsors(combined)


def build_index(rows: list[dict]) -> dict[str, dict]:
    """Map normalized and short company names to a sponsor row."""
    index: dict[str, dict] = {}
    for row in rows:
        if int(row.get("approvals") or 0) <= 0:
            continue
        for raw in _name_forms(row.get("employer") or ""):
            for key in sponsor_keys(raw):
                current = index.get(key)
                if current is None or int(row.get("approvals") or 0) > int(current.get("approvals") or 0):
                    index[key] = row
        stored = str(row.get("name_key") or "")
        if len(stored) >= 4:
            index.setdefault(stored, row)
    return index


def match_sponsor(company: str, index: dict[str, dict]) -> dict | None:
    if not company or not index:
        return None
    for key in sponsor_keys(company):
        hit = index.get(key)
        if hit:
            return hit
    return None
