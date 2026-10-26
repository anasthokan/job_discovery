"""Annual salaryMin and salaryMax from a scraped listing.

Boards send this in different shapes: Jobicy uses salaryMin/salaryMax,
JobSpy uses min_amount/max_amount plus an interval, and many postings only
mention a range in the description. Stored values are whole annual amounts.
"""

from __future__ import annotations

import json
import re

_MIN_KEYS = {
    "salarymin",
    "minsalary",
    "minamount",
    "compensationmin",
    "minvalue",
    "paymin",
    "minimumsalary",
    "salaryminimum",
}
_MAX_KEYS = {
    "salarymax",
    "maxsalary",
    "maxamount",
    "compensationmax",
    "maxvalue",
    "paymax",
    "maximumsalary",
    "salarymaximum",
}
_PERIOD_KEYS = {
    "salaryperiod",
    "interval",
    "payperiod",
    "compensationinterval",
    "salaryinterval",
}
_TEXT_KEYS = ("salary", "compensation", "salaryrange", "payrange", "basesalary")
_PAY_KEY = ("salary", "comp", "pay", "amount")

_MONEY = (
    r"\$\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\$\s*\d+(?:\.\d+)?\s*[kK]"
    r"|\$\s*\d+(?:\.\d+)?"
)
_PERIOD = r"hour|hourly|hr|year|yearly|annual|annum|month|monthly|mo|week|weekly|wk"
_RANGE = re.compile(
    rf"(?P<a>{_MONEY})(?:\s*(?:/|per|an)\s*(?P<period1>{_PERIOD}))?"
    rf"(?:\s*(?:-|–|—|to|and)\s*(?P<b>{_MONEY})"
    rf"(?:\s*(?:/|per|an)\s*(?P<period2>{_PERIOD}))?)?",
    re.I,
)
_WORD = re.compile(
    rf"(?:salary|compensation|base pay|pay range)\D{{0,30}}"
    rf"(?P<a>\$\s*\d[\d,]*(?:\.\d+)?\s*[kK]?|\$?\s*\d+(?:\.\d+)?\s*[kK]|\d{{1,3}}(?:,\d{{3}})+)"
    rf"(?:\s*(?:/|per|an)\s*(?P<period1>{_PERIOD}))?"
    rf"(?:\s*(?:-|–|—|to|and)\s*"
    rf"(?P<b>\$\s*\d[\d,]*(?:\.\d+)?\s*[kK]?|\$?\s*\d+(?:\.\d+)?\s*[kK]|\d{{1,3}}(?:,\d{{3}})+)"
    rf"(?:\s*(?:/|per|an)\s*(?P<period2>{_PERIOD}))?)?",
    re.I,
)
_KRANGE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?\s*[kK])(?:\s*(?:-|–|—|to)\s*(?P<b>\d+(?:\.\d+)?\s*[kK]))?",
    re.I,
)


def salary_bounds(job: dict | None) -> tuple[int | None, int | None]:
    """Return (salaryMin, salaryMax) as annual integers, or (None, None)."""
    if not isinstance(job, dict):
        return None, None
    explicit = _pair_from_mapping(job)
    if explicit != (None, None):
        return explicit
    for key in ("raw", "raw_description"):
        found = _from_source(job.get(key))
        if found != (None, None):
            return found
    text = "\n".join(str(job.get(key) or "") for key in ("description", "search_text"))
    return _from_text(text)


def _fold_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _plain(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float) and value != value:
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes, bytearray)):
        try:
            return _plain(value.item())
        except Exception:
            return value
    return value


def _period_kind(period: object) -> str | None:
    text = " ".join(re.sub(r"[^a-z]", " ", str(period or "").lower()).split())
    if not text:
        return None
    if "hour" in text or text == "hr":
        return "hour"
    if "week" in text:
        return "week"
    if "month" in text or text == "mo":
        return "month"
    if "day" in text:
        return "day"
    if "year" in text or "annual" in text or "annum" in text:
        return "year"
    return None


def _number(value) -> float | None:
    value = _plain(value)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(",", "").replace("$", "")
    text = re.sub(r"(\d)\s*k\b", r"\1k", text)
    match = re.search(r"(\d+(?:\.\d+)?)(k)?", text)
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2):
        number *= 1000
    return number


def _amount(value, period: object = "") -> int | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    kind = _period_kind(period)
    if kind == "hour" and number < 1000:
        number *= 2080
    elif kind == "day" and number < 2000:
        number *= 260
    elif kind == "week" and number < 10000:
        number *= 52
    elif kind == "month" and number < 20000:
        number *= 12
    elif kind is None and number < 1000:
        return None
    rounded = int(round(number))
    if rounded < 10000 or rounded > 2_000_000:
        return None
    return rounded


def _ordered(low: int | None, high: int | None) -> tuple[int | None, int | None]:
    if low is None and high is None:
        return None, None
    if low is None:
        low = high
    if high is None:
        high = low
    if low > high:
        low, high = high, low
    return low, high


def _keyed_amount(folded: dict, keys: set[str], period: object) -> int | None:
    for key in keys:
        if key not in folded:
            continue
        amount = _amount(folded[key], period)
        if amount is not None:
            return amount
    return None


def _pair_from_mapping(data: dict) -> tuple[int | None, int | None]:
    folded = {_fold_key(key): value for key, value in data.items()}
    period = ""
    for key in _PERIOD_KEYS:
        if key in folded and str(folded[key] or "").strip():
            period = folded[key]
            break
    low = _keyed_amount(folded, _MIN_KEYS, period)
    high = _keyed_amount(folded, _MAX_KEYS, period)
    if low is None and high is None:
        for key in _TEXT_KEYS:
            if key not in folded:
                continue
            value = folded[key]
            if isinstance(value, (dict, list)):
                continue
            if isinstance(value, str) and value.strip():
                found = _from_text(value)
                if found != (None, None):
                    return found
            one = _amount(value, period)
            if one is not None:
                return _ordered(one, one)
        return None, None
    return _ordered(low, high)


def _from_source(value, depth: int = 0) -> tuple[int | None, int | None]:
    if depth > 4:
        return None, None
    value = _plain(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"{}", "[]", "null"}:
            return None, None
        if text[:1] in "{[":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return _from_text(text)
            return _from_source(parsed, depth + 1)
        return _from_text(text)
    if isinstance(value, list):
        for item in value[:12]:
            found = _from_source(item, depth + 1)
            if found != (None, None):
                return found
        return None, None
    if not isinstance(value, dict):
        return None, None
    found = _pair_from_mapping(value)
    if found != (None, None):
        return found
    texts: list[str] = []
    for key, item in value.items():
        if isinstance(item, (dict, list)):
            found = _from_source(item, depth + 1)
            if found != (None, None):
                return found
            continue
        if not isinstance(item, str) or "$" not in item:
            continue
        folded = _fold_key(key)
        if any(token in folded for token in _PAY_KEY):
            texts.append(item[:12000])
    for text in texts:
        found = _from_text(text)
        if found != (None, None):
            return found
    return None, None


def _pair_from_match(match: re.Match[str]) -> tuple[int | None, int | None]:
    groups = match.groupdict()
    period_a = groups.get("period1") or groups.get("period2") or ""
    period_b = groups.get("period2") or groups.get("period1") or ""
    low = _amount(groups.get("a"), period_a)
    high = _amount(groups.get("b"), period_b) if groups.get("b") else None
    return _ordered(low, high)


def _from_text(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    sample = text[:20000]
    has_pay_word = re.search(r"\b(salary|compensation|pay)\b", sample, re.I) is not None
    if "$" not in sample and not has_pay_word and not re.search(r"\d\s*k\b", sample, re.I):
        return None, None
    best: tuple[int, tuple[int | None, int | None]] | None = None
    for pattern in (_RANGE, _WORD, _KRANGE):
        if pattern is _RANGE and "$" not in sample:
            continue
        for match in pattern.finditer(sample):
            pair = _pair_from_match(match)
            if pair == (None, None):
                continue
            window = sample[max(0, match.start() - 48) : match.end() + 48].lower()
            near_pay = any(word in window for word in ("salary", "compensation", "pay", "hour", "year", "annual"))
            if pattern is _KRANGE and pair[0] == pair[1] and not near_pay and len(sample) > 40:
                continue
            score = 0
            if pair[0] != pair[1]:
                score += 2
            if near_pay:
                score += 2
            low = pair[0] or 0
            if 30000 <= low <= 400000:
                score += 1
            if best is None or score > best[0]:
                best = (score, pair)
    return best[1] if best else (None, None)
