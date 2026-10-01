"""Fields the Explore Jobs filters need, inferred from a listing.

Boards rarely send experience, H1B, or clearance as columns. Title, location,
and description are enough to fill the values the dashboard already shows:

  work_model          remote | hybrid | onsite | unknown
  job_type            fulltime | parttime | contract | internship
  experience_level    Intern/New Grad | Entry Level | Mid Level | Senior Level
                      | Lead/Staff | Director/Executive | unknown
  years_experience    minimum years stated in the posting, or null
  h1b_sponsorship     yes | no | unknown
  clearance_required  yes | no | unknown
  us_citizen_required yes | no
"""

from __future__ import annotations

import re

WORK_MODELS = ("remote", "hybrid", "onsite")
JOB_TYPES = ("fulltime", "parttime", "contract", "internship")
EXPERIENCE_LEVELS = (
    "Intern/New Grad",
    "Entry Level",
    "Mid Level",
    "Senior Level",
    "Lead/Staff",
    "Director/Executive",
)
YES_NO = ("yes", "no")

_PLACEHOLDER_LOCS = {
    "",
    "united states",
    "usa",
    "us",
    "u.s.",
    "u.s.a.",
    "anywhere",
    "n/a",
    "na",
    "unknown",
    "remote",
}

_HYBRID = re.compile(r"\bhybrid\b", re.I)
_REMOTE = re.compile(r"\b(remote|work from home|work-from-home|\bwfh\b)\b", re.I)
_NOT_REMOTE = re.compile(r"\b(no|not|non)[\s-]+remote\b", re.I)
_ONSITE_WORD = re.compile(r"\b(on[\s-]?site|in[\s-]?office)\b", re.I)
_REMOTE_PHRASE = re.compile(
    r"\b(fully remote|100% remote|remote position|remote role|remote-first|"
    r"this (?:is|role is) remote|work remotely)\b",
    re.I,
)

_TITLE_LEVELS = (
    (
        "Director/Executive",
        re.compile(
            r"\b(director|vice[\s-]?president|\bvp\b|head of|\bchief\b|"
            r"\bcto\b|\bceo\b|\bcfo\b|\bcoo\b|\bciso\b|\bexecutive\b)\b",
            re.I,
        ),
    ),
    (
        "Lead/Staff",
        re.compile(r"\b(staff|principal|distinguished|tech lead|team lead|\blead\b)\b", re.I),
    ),
    (
        "Intern/New Grad",
        re.compile(r"\b(intern(?:ship|s)?|new grad|new graduate)\b", re.I),
    ),
    ("Senior Level", re.compile(r"\b(senior|sr\.?)\b", re.I)),
    ("Entry Level", re.compile(r"\b(junior|jr\.?|entry[\s-]?level)\b", re.I)),
    ("Mid Level", re.compile(r"\b(mid[\s-]?level|intermediate)\b", re.I)),
)

_SENIORITY_LINE = re.compile(r"seniority(?: level)?\s*[:\-]\s*([^\n.]{3,40})", re.I)
_SENIORITY_MAP = (
    ("intern", "Intern/New Grad"),
    ("entry", "Entry Level"),
    ("associate", "Entry Level"),
    ("mid-senior", "Senior Level"),
    ("mid senior", "Senior Level"),
    ("director", "Director/Executive"),
    ("executive", "Director/Executive"),
    ("senior", "Senior Level"),
)

_YEARS = re.compile(
    r"(?:"
    r"(?:at least|minimum(?: of)?|min\.?)\s+(\d{1,2})\s*\+?\s*years"
    r"|(\d{1,2})\s*\+\s*years"
    r"|(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*years"
    r"|(\d{1,2})\s+years(?:'|\s)+(?:of\s+)?(?:relevant\s+|professional\s+|work\s+|industry\s+|hands-on\s+)?"
    r"(?:experience|exp\b)"
    r")",
    re.I,
)
_NO_EXPERIENCE = re.compile(
    r"\bno (?:prior |previous )?experience (?:required|needed|necessary)\b",
    re.I,
)

_H1B_NO = re.compile(
    r"(?:\bno\b|\bnot\b|\bwithout\b|\bunable\b|\bcannot\b|\bcan't\b|\bdo not\b|"
    r"\bdon't\b|\bdoes not\b).{0,48}(?:\bsponsor(?:ship)?\b|\bh-?1b\b)"
    r"|(?:\bsponsor(?:ship)?\b).{0,24}(?:\bnot available\b|\bnot offered\b|\bunavailable\b)"
    r"|\bus citizens only\b"
    r"|\bmust be (?:a )?u\.?s\.? citizens?\b"
    r"|\bno visa sponsorship\b",
    re.I,
)
_H1B_YES = re.compile(
    r"\bh-?1b\b"
    r"|\bh1-?b\b"
    r"|\bvisa sponsorship\b"
    r"|\bsponsorship (?:is )?available\b"
    r"|\bwill sponsor\b"
    r"|\bwe sponsor\b"
    r"|\bopen to sponsor(?:ing|ship)?\b",
    re.I,
)

_CLEARANCE_NO = re.compile(
    r"\b(?:no|not|without|non)\b.{0,24}\bclearance\b"
    r"|\bclearance (?:is )?not required\b",
    re.I,
)
_CLEARANCE_YES = re.compile(
    r"\bts/sci\b|\btop secret\b|\bsecret clearance\b|\bsecurity clearance\b"
    r"|\bpublic trust\b|\bactive clearance\b|\bclearance required\b"
    r"|\bmust (?:have|hold|possess)(?: a)? clearance\b"
    r"|\bability to obtain(?: a)? (?:a )?clearance\b",
    re.I,
)
# Secret and Top Secret clearances are only granted to US citizens; Public Trust is not.
_CITIZEN_CLEARANCE = re.compile(
    r"\bts/sci\b|\btop secret\b|\bsecret clearance\b|\b(?:active|current) secret\b"
    r"|\b(?:obtain|maintain|hold|possess)(?: an?)? (?:active )?(?:dod |federal )?(?:security )?clearance\b",
    re.I,
)

_US_NAME = r"(?:us|united states|american)"
_CITIZEN_YES = re.compile(
    rf"\bmust be (?:an? )?{_US_NAME} citizens?\b"
    rf"|\bmust be (?:an? )?citizens? of the (?:us|united states)\b"
    rf"|\b{_US_NAME} citizens? only\b"
    rf"|\bonly (?:open to )?{_US_NAME} citizens?\b"
    rf"|\b{_US_NAME} citizens? (?:is |are )?(?:required|needed)\b"
    rf"|\b{_US_NAME} citizenship (?:is |will be )?(?:required|mandatory|needed|a must)\b"
    rf"|\b(?:requires?|requiring|required) (?:active )?{_US_NAME} citizenship\b"
    rf"|\bmust (?:have|hold|possess|maintain|provide proof of) {_US_NAME} citizenship\b"
    rf"|\b(?:applicants|candidates) must be {_US_NAME} citizens\b"
    rf"|\b(?:requirements?|eligibility)\s*:?\s*{_US_NAME} citizenship\b"
    r"|\bcitizenship (?:is )?required\b"
    r"|\bcitizenship requirement\b",
    re.I,
)
_CITIZEN_NO = re.compile(
    rf"\b(?:{_US_NAME} )?citizenship (?:is )?not (?:required|necessary|needed|a requirement)\b"
    r"|\bno (?:us )?citizenship (?:requirement|required)\b"
    r"|\b(?:do|does|did)\s*(?:not|n['’]t) (?:need|have) to be (?:an? )?(?:us )?citizens?\b"
    r"|\bcitizens?\s*(?:,|/|or|and)\s*(?:lawful )?(?:permanent residents?|green[\s-]?card(?: holders?)?|gc(?: holders?)?)\b"
    r"|\b(?:lawful )?(?:permanent residents?|green[\s-]?card(?: holders?)?|gc(?: holders?)?)\s*(?:,|/|or|and)\s*(?:us )?citizens?\b"
    r"|\busc\s*/\s*gc\b|\bgc\s*/\s*usc\b"
    r"|\bus persons?\b"
    r"|\b(?:h-?1b|opt|cpt|ead|tn|f-?1)\b.{0,40}\b(?:welcome|accepted|considered|eligible)\b"
    r"|\b(?:open to|accept(?:s|ing)?|welcome)\b.{0,20}\b(?:all|any) (?:work authorizations?|visa types?|visas)\b",
    re.I,
)
_US_DOTTED = re.compile(r"\bU\.\s?S\.(?:\s?A\.)?", re.I)

_JOB_TYPE_ALIASES = {
    "full-time": "fulltime",
    "full time": "fulltime",
    "fulltime": "fulltime",
    "part-time": "parttime",
    "part time": "parttime",
    "parttime": "parttime",
    "contract": "contract",
    "contractor": "contract",
    "freelance": "contract",
    "temporary": "contract",
    "internship": "internship",
    "intern": "internship",
}
_WORK_ALIASES = {
    "remote": "remote",
    "wfh": "remote",
    "work from home": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
    "on-site": "onsite",
    "on site": "onsite",
    "in-office": "onsite",
    "in office": "onsite",
}
_LEVEL_ALIASES = {label.lower(): label for label in EXPERIENCE_LEVELS}
_LEVEL_ALIASES.update(
    {
        "intern": "Intern/New Grad",
        "new grad": "Intern/New Grad",
        "entry": "Entry Level",
        "mid": "Mid Level",
        "senior": "Senior Level",
        "lead": "Lead/Staff",
        "staff": "Lead/Staff",
        "director": "Director/Executive",
        "executive": "Director/Executive",
    }
)


def _clip_text(*parts: object, limit: int = 20000) -> str:
    text = " ".join(str(part or "") for part in parts)
    return text[:limit]


def normalize_job_type(*values: object, description: str = "") -> str:
    """fulltime, parttime, contract, or internship. Unknown stays fulltime."""
    found = _explicit_job_type(" ".join(str(value or "") for value in values))
    if found:
        return found
    desc = description or ""
    if re.search(r"\bintern(?:ship|s)?\b", desc, re.I):
        return "internship"
    if re.search(r"part[\s_-]?time", desc, re.I):
        return "parttime"
    return "fulltime"


def _explicit_job_type(text: str) -> str | None:
    if not text.strip():
        return None
    if re.search(r"\bintern(?:ship|s)?\b", text, re.I):
        return "internship"
    if re.search(r"part[\s_-]?time", text, re.I):
        return "parttime"
    if re.search(r"\b(contract|contractor|freelance|temporary)\b", text, re.I):
        return "contract"
    if re.search(r"full[\s_-]?time", text, re.I):
        return "fulltime"
    return None


def infer_work_model(location: str = "", state: str = "", title: str = "", description: str = "") -> str:
    head = f"{location or ''} {state or ''} {title or ''}"
    if _HYBRID.search(head):
        return "hybrid"
    if _REMOTE.search(head) and not _NOT_REMOTE.search(head):
        return "remote"
    if (state or "").strip().lower() == "remote":
        return "remote"
    if _ONSITE_WORD.search(head):
        return "onsite"
    short = (description or "")[:2500]
    if _HYBRID.search(short):
        return "hybrid"
    if _REMOTE_PHRASE.search(short) and not _NOT_REMOTE.search(short):
        return "remote"
    place = (location or "").strip().lower()
    if place and place not in _PLACEHOLDER_LOCS:
        return "onsite"
    return "unknown"


def infer_years(title: str = "", description: str = "") -> int | None:
    text = _clip_text(title, description, limit=12000)
    found: list[int] = []
    for match in _YEARS.finditer(text):
        for group in match.groups():
            if not group:
                continue
            value = int(group)
            if 0 <= value <= 25:
                found.append(value)
    if found:
        return min(found)
    if _NO_EXPERIENCE.search(text):
        return 0
    return None


def infer_experience_level(
    title: str = "",
    description: str = "",
    job_type: str = "",
    years: int | None = None,
) -> str:
    for label, pattern in _TITLE_LEVELS:
        if pattern.search(title or ""):
            return label
    seniority = _SENIORITY_LINE.search((description or "")[:4000])
    if seniority:
        line = seniority.group(1).lower()
        for token, label in _SENIORITY_MAP:
            if token in line:
                return label
    if job_type == "internship":
        return "Intern/New Grad"
    if years is None:
        return "unknown"
    if years <= 1:
        return "Entry Level"
    if years <= 5:
        return "Mid Level"
    if years <= 9:
        return "Senior Level"
    return "Lead/Staff"


def _yes_no(text: str, no_re: re.Pattern[str], yes_re: re.Pattern[str]) -> str:
    """Negation only counts inside the same sentence, so 'No experience. Visa sponsorship' stays yes."""
    saw_no = False
    saw_yes = False
    text = _US_DOTTED.sub("US", text)
    for sentence in re.split(r"[\n.;]+", text):
        if no_re.search(sentence):
            saw_no = True
        elif yes_re.search(sentence):
            saw_yes = True
    if saw_yes and not saw_no:
        return "yes"
    if saw_no and not saw_yes:
        return "no"
    return "unknown"


def infer_h1b(title: str = "", description: str = "") -> str:
    return _yes_no(_clip_text(title, description), _H1B_NO, _H1B_YES)


def infer_clearance(title: str = "", description: str = "") -> str:
    return _yes_no(_clip_text(title, description), _CLEARANCE_NO, _CLEARANCE_YES)


def infer_us_citizen(title: str = "", description: str = "") -> str:
    """yes only when the posting demands US citizenship; otherwise no.

    Secret / Top Secret clearance implies citizenship. Green card holders, US persons
    (ITAR), visa sponsorship, or no mention at all mean citizenship is not required.
    """
    text = _clip_text(title, description)
    if _yes_no(text, _CITIZEN_NO, _CITIZEN_YES) == "yes":
        return "yes"
    if infer_clearance(title, description) == "yes" and _CITIZEN_CLEARANCE.search(text):
        return "yes"
    return "no"


def infer_listing_fields(
    *,
    title: str = "",
    location: str = "",
    state: str = "",
    description: str = "",
    job_type: object = "",
) -> dict:
    kind = normalize_job_type(job_type, title, description=description or "")
    years = infer_years(title or "", description or "")
    return {
        "job_type": kind,
        "work_model": infer_work_model(location or "", state or "", title or "", description or ""),
        "experience_level": infer_experience_level(title or "", description or "", kind, years),
        "years_experience": years,
        "h1b_sponsorship": infer_h1b(title or "", description or ""),
        "clearance_required": infer_clearance(title or "", description or ""),
        "us_citizen_required": infer_us_citizen(title or "", description or ""),
    }


def _split(raw: str) -> list[str]:
    return [part.strip().lower() for part in (raw or "").split(",") if part.strip()]


def apply_listing_filters(
    jobs: list[dict],
    *,
    work_model: str = "",
    job_type: str = "",
    experience_level: str = "",
    h1b_sponsorship: str = "",
    clearance_required: str = "",
    us_citizen_required: str = "",
    years: int | None = None,
) -> list[dict]:
    """Keep listings that match the Explore Jobs dropdowns. Blank filters are ignored."""
    work = {_WORK_ALIASES.get(part, part) for part in _split(work_model)}
    types = {_JOB_TYPE_ALIASES.get(part, part) for part in _split(job_type)}
    levels = {_LEVEL_ALIASES.get(part, part) for part in _split(experience_level)}
    h1b = set(_split(h1b_sponsorship))
    clearance = set(_split(clearance_required))
    citizen = set(_split(us_citizen_required))
    if not any((work, types, levels, h1b, clearance, citizen)) and years is None:
        return jobs
    kept = []
    for job in jobs:
        if work and (job.get("work_model") or "unknown") not in work:
            continue
        if types and (job.get("job_type") or "fulltime") not in types:
            continue
        if levels and (job.get("experience_level") or "unknown") not in levels:
            continue
        if h1b and (job.get("h1b_sponsorship") or "unknown") not in h1b:
            continue
        if clearance and (job.get("clearance_required") or "unknown") not in clearance:
            continue
        if citizen and (job.get("us_citizen_required") or "no") not in citizen:
            continue
        if years is not None:
            required = job.get("years_experience")
            if required is not None and int(required) > int(years):
                continue
        kept.append(job)
    return kept
