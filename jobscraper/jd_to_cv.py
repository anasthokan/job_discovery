"""Turn a job description (and optional resume) into CV-ready structured data."""

from __future__ import annotations

import re
from typing import Any

from jobscraper.resume_parser import (
    CITY_STATE_RE,
    SKILL_CANONICAL,
    SKILL_PATTERN,
    TITLE_HINTS,
    parse_resume_text,
)

MIN_JD_CHARS = 40

JD_SECTION_ALIASES = {
    "responsibilities": (
        "responsibilities",
        "what you will do",
        "what you'll do",
        "what youll do",
        "the role",
        "about the role",
        "about this role",
        "role overview",
        "duties",
        "day to day",
        "day-to-day",
        "you will",
        "in this role",
        "job description",
        "the job",
        "what you'll be doing",
        "key responsibilities",
    ),
    "requirements": (
        "requirements",
        "qualifications",
        "what we're looking for",
        "what we are looking for",
        "who you are",
        "must have",
        "must-have",
        "minimum qualifications",
        "required qualifications",
        "required skills",
        "basic qualifications",
        "you have",
        "about you",
        "what you bring",
    ),
    "preferred": (
        "preferred",
        "preferred qualifications",
        "nice to have",
        "nice-to-have",
        "bonus",
        "plus",
        "good to have",
        "additional qualifications",
    ),
    "skills": (
        "skills",
        "technical skills",
        "tech stack",
        "technologies",
        "our stack",
    ),
    "benefits": (
        "benefits",
        "perks",
        "what we offer",
        "compensation",
        "why join",
    ),
    "about": (
        "about the company",
        "about us",
        "who we are",
        "our company",
        "about the team",
    ),
}

JD_SECTION_LOOKUP = {
    alias: key
    for key, aliases in JD_SECTION_ALIASES.items()
    for alias in aliases
}

SKILL_RELATED = {
    "sql": {"postgresql", "mysql", "sqlite", "oracle", "sql server"},
    "postgresql": {"sql"},
    "mysql": {"sql"},
}

STOPWORDS = {
    "the", "and", "for", "with", "you", "will", "our", "this", "that", "from",
    "have", "are", "was", "were", "been", "being", "your", "their", "they",
    "who", "what", "when", "where", "which", "into", "onto", "about", "over",
    "such", "than", "then", "them", "these", "those", "also", "able", "work",
    "role", "job", "team", "plus", "etc", "including", "using", "used", "use",
    "across", "within", "other", "more", "most", "some", "any", "all", "can",
    "must", "should", "need", "needs", "required", "requirement", "experience",
    "years", "year", "strong", "good", "great", "well", "high", "new", "join",
}

TITLE_LINE_RE = re.compile(
    r"^(?:job\s*title|title|position|role|we(?:'re| are) hiring(?: a| an)?)\s*[:\-]\s*(.+)$",
    re.I,
)
COMPANY_LINE_RE = re.compile(r"^(?:company|employer|organization)\s*[:\-]\s*(.+)$", re.I)
HIRING_RE = re.compile(
    r"(?:hiring|seeking|looking for)\s+(?:a|an)\s+([A-Z][A-Za-z0-9 /+&,\-]{6,80})",
)
AT_COMPANY_RE = re.compile(r"\bat\s+([A-Z][A-Za-z0-9.&' \-]{1,60})$")


class JobToCvError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def build_cv_from_job(
    job_description: str = "",
    *,
    job_title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    job_skills: list[str] | None = None,
    resume_text: str | None = None,
    resume: dict[str, Any] | None = None,
    include_cv_text: bool = True,
) -> dict[str, Any]:
    jd_text = (job_description or "").strip()
    if job_skills and not jd_text:
        jd_text = _synthetic_jd(job_title, company, job_skills)
    if len(jd_text) < MIN_JD_CHARS and not (job_title or job_skills):
        raise JobToCvError("Job description is too short. Paste the posting text or provide a job title and skills.")

    job = parse_job_description(
        jd_text,
        title=job_title,
        company=company,
        location=location,
        extra_skills=job_skills,
    )
    parsed_resume = _coerce_resume(resume_text, resume)
    if parsed_resume:
        cv, match, mode = _tailor_resume(parsed_resume, job)
    else:
        cv, match, mode = _template_cv(job)

    payload: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "job": job,
        "match": match,
        "cv": cv,
        "cv_text": None,
    }
    if include_cv_text:
        payload["cv_text"] = render_cv_text(cv)
    return payload


def parse_job_description(
    text: str,
    *,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    extra_skills: list[str] | None = None,
) -> dict[str, Any]:
    cleaned = _normalize(text)
    lines = [line.strip(" -•\t") for line in cleaned.splitlines() if line.strip()]
    sections = _split_jd_sections(lines)

    title = (title or "").strip() or _extract_title(lines)
    company = (company or "").strip() or _extract_company(lines, title)
    location = (location or "").strip() or _extract_location(lines, cleaned)
    employment_type = _extract_employment_type(cleaned)
    workplace_type = _extract_workplace(cleaned)

    skill_lines = sections.get("skills", []) + sections.get("requirements", [])
    required_skills = _extract_skills("\n".join(skill_lines) or cleaned)
    preferred_skills = _extract_skills("\n".join(sections.get("preferred", [])))
    if extra_skills:
        required_skills = _unique([*(_canonical_skill(skill) for skill in extra_skills), *required_skills])

    preferred_skills = [skill for skill in preferred_skills if skill.lower() not in {item.lower() for item in required_skills}]
    responsibilities = _bullet_items(sections.get("responsibilities", []) or lines[8:24])
    qualifications = _bullet_items(sections.get("requirements", []))
    keywords = _keywords(cleaned, required_skills)

    return {
        "title": title,
        "company": company,
        "location": location,
        "employment_type": employment_type,
        "workplace_type": workplace_type,
        "required_skills": required_skills[:40],
        "preferred_skills": preferred_skills[:20],
        "responsibilities": responsibilities[:20],
        "qualifications": qualifications[:20],
        "keywords": keywords[:40],
    }


def render_cv_text(cv: dict[str, Any]) -> str:
    profile = cv.get("profile") or {}
    header_bits = [profile.get("full_name") or "[Your Name]"]
    contact = [
        value
        for value in (
            profile.get("location"),
            profile.get("email"),
            profile.get("phone"),
        )
        if value
    ]
    links = [value for value in (profile.get("linkedin"), profile.get("github"), profile.get("website")) if value]
    parts = ["\n".join(bit for bit in [header_bits[0], " | ".join(contact), " | ".join(links)] if bit)]

    if profile.get("summary"):
        parts.append("SUMMARY\n" + profile["summary"])
    if cv.get("skills"):
        parts.append("SKILLS\n" + ", ".join(cv["skills"]))

    if cv.get("experience"):
        blocks = ["EXPERIENCE"]
        for job in cv["experience"]:
            meta = " | ".join(value for value in (job.get("title"), job.get("company"), job.get("location")) if value)
            dates = " - ".join(value for value in (job.get("start_date"), job.get("end_date")) if value)
            if meta:
                blocks.append(meta)
            if dates:
                blocks.append(dates)
            for bullet in job.get("bullets") or []:
                blocks.append(f"- {bullet}")
            blocks.append("")
        parts.append("\n".join(blocks).strip())

    if cv.get("projects"):
        blocks = ["PROJECTS"]
        for project in cv["projects"]:
            blocks.append(project.get("name") or "Project")
            if project.get("description"):
                blocks.append(f"- {project['description']}")
        parts.append("\n".join(blocks))

    if cv.get("education"):
        blocks = ["EDUCATION"]
        for item in cv["education"]:
            degree = " ".join(value for value in (item.get("degree"), item.get("field")) if value).strip()
            years = " - ".join(value for value in (item.get("start_year"), item.get("end_year")) if value)
            line = " | ".join(value for value in (degree or None, item.get("institution"), years or None) if value)
            if line:
                blocks.append(line)
        parts.append("\n".join(blocks))

    if cv.get("certifications"):
        parts.append("CERTIFICATIONS\n" + "\n".join(f"- {item}" for item in cv["certifications"]))
    if cv.get("languages"):
        parts.append("LANGUAGES\n" + ", ".join(cv["languages"]))
    return "\n\n".join(parts).strip() + "\n"


def _synthetic_jd(title: str | None, company: str | None, skills: list[str]) -> str:
    bits = [
        title or "Open role",
        f"Company: {company}" if company else "",
        "Required skills: " + ", ".join(skills),
    ]
    return "\n".join(bit for bit in bits if bit)


def _coerce_resume(resume_text: str | None, resume: dict[str, Any] | None) -> dict[str, Any] | None:
    if resume_text and resume_text.strip():
        if len(resume_text.strip()) < 40:
            raise JobToCvError("Resume text is too short to tailor.")
        return parse_resume_text(resume_text)
    if not resume:
        return None
    profile = resume.get("profile") if isinstance(resume.get("profile"), dict) else {}
    return {
        "profile": {
            "full_name": profile.get("full_name"),
            "email": profile.get("email"),
            "phone": profile.get("phone"),
            "phones": profile.get("phones") or [],
            "location": profile.get("location"),
            "linkedin": profile.get("linkedin"),
            "github": profile.get("github"),
            "website": profile.get("website"),
            "summary": profile.get("summary"),
        },
        "skills": list(resume.get("skills") or []),
        "experience": list(resume.get("experience") or []),
        "education": list(resume.get("education") or []),
        "projects": list(resume.get("projects") or []),
        "certifications": list(resume.get("certifications") or []),
        "languages": list(resume.get("languages") or []),
    }


def _tailor_resume(parsed: dict[str, Any], job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    resume_skills = list(parsed.get("skills") or [])
    required_skills = list(job.get("required_skills") or [])
    preferred_skills = list(job.get("preferred_skills") or [])
    target_skills = _unique([*required_skills, *preferred_skills])
    matched, missing_all, extra = _skill_match(resume_skills, target_skills)
    matched_required, missing_required, _ = _skill_match(resume_skills, required_skills)
    keywords = set(word.lower() for word in (job.get("keywords") or []) + target_skills)

    experience = [_score_experience(item, keywords) for item in parsed.get("experience") or []]
    experience.sort(key=lambda item: item.pop("_score"), reverse=True)
    projects = [_score_project(item, keywords) for item in parsed.get("projects") or []]
    projects.sort(key=lambda item: item.pop("_score"), reverse=True)

    ordered_skills = _unique([*matched_required, *matched, *extra])
    profile = dict(parsed.get("profile") or {})
    profile["summary"] = _tailor_summary(profile.get("summary"), job, matched_required or matched)

    match_score = _match_score(matched_required, missing_required, experience)
    cv = {
        "profile": profile,
        "skills": ordered_skills,
        "experience": experience,
        "education": parsed.get("education") or [],
        "projects": projects,
        "certifications": parsed.get("certifications") or [],
        "languages": parsed.get("languages") or [],
    }
    match = {
        "score": match_score,
        "matched_skills": matched,
        "missing_skills": missing_all,
        "extra_skills": extra,
    }
    return cv, match, "tailored"


def _template_cv(job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    title = job.get("title") or "this role"
    company = job.get("company")
    skills = job.get("required_skills") or []
    company_bit = f" at {company}" if company else ""
    skill_bit = f" Core skills for this posting: {', '.join(skills[:8])}." if skills else ""
    summary = (
        f"[Write a 2-3 sentence summary targeting {title}{company_bit}.]"
        f"{skill_bit} Replace placeholders with real experience only."
    )
    prompt_bullets = [
        f"[Describe your work related to: {item}]"
        for item in (job.get("responsibilities") or [])[:6]
    ] or ["[Add a result-focused bullet that matches this job.]"]
    experience = [
        {
            "title": title,
            "company": "[Your Company]",
            "location": job.get("location"),
            "start_date": "[Start]",
            "end_date": "Present",
            "is_current": True,
            "bullets": prompt_bullets,
        }
    ]
    cv = {
        "profile": {
            "full_name": "[Your Name]",
            "email": "[email@example.com]",
            "phone": None,
            "phones": [],
            "location": job.get("location"),
            "linkedin": None,
            "github": None,
            "website": None,
            "summary": summary,
        },
        "skills": skills,
        "experience": experience,
        "education": [],
        "projects": [],
        "certifications": [],
        "languages": [],
    }
    match = {
        "score": None,
        "matched_skills": [],
        "missing_skills": skills,
        "extra_skills": [],
    }
    return cv, match, "template"


def _tailor_summary(original: str | None, job: dict[str, Any], matched: list[str]) -> str:
    role = job.get("title") or "this role"
    company = f" at {job['company']}" if job.get("company") else ""
    target = f"{role}{company}"
    extras = []
    if matched:
        extras.append(f"Core strengths for this role include {', '.join(matched[:6])}.")
    extras.append(f"Prepared for {target}.")
    extra_text = " ".join(extras)
    if original and len(original.strip()) >= 40:
        body = original.strip().rstrip(".")
        lowered = body.lower()
        bits = [body]
        if matched and not any(skill.lower() in lowered for skill in matched[:3]):
            bits.append(extras[0])
        if target.lower() not in lowered:
            bits.append(f"Prepared for {target}.")
        return " ".join(dict.fromkeys(bits)).strip()
    return extra_text


def _skill_match(resume_skills: list[str], target_skills: list[str]) -> tuple[list[str], list[str], list[str]]:
    resume_map = {skill.lower(): skill for skill in resume_skills}
    resume_keys = set(resume_map)
    matched = []
    missing = []
    for skill in target_skills:
        key = skill.lower()
        if key in resume_map:
            matched.append(resume_map[key])
            continue
        related = SKILL_RELATED.get(key, set())
        related_hit = next((resume_map[item] for item in related if item in resume_keys), None)
        if related_hit:
            matched.append(related_hit)
            continue
        hit = next((value for value in resume_skills if key in value.lower() or value.lower() in key), None)
        if hit:
            matched.append(hit)
        else:
            missing.append(skill)
    matched = _unique(matched)
    matched_keys = {item.lower() for item in matched}
    extra = [skill for skill in resume_skills if skill.lower() not in matched_keys]
    return matched, _unique(missing), extra


def _match_score(matched: list[str], missing: list[str], experience: list[dict[str, Any]]) -> int:
    total = len(matched) + len(missing)
    skill_score = (len(matched) / total) if total else 0.0
    aligned = 0
    for job in experience:
        aligned += sum(1 for bullet in job.get("bullets") or [] if bullet)
    exp_score = min(1.0, aligned / 4) if experience else 0.0
    return int(round(100 * (0.85 * skill_score + 0.15 * exp_score)))


def _score_experience(item: dict[str, Any], keywords: set[str]) -> dict[str, Any]:
    job = dict(item)
    bullets = list(job.get("bullets") or [])
    scored = []
    for bullet in bullets:
        scored.append((_keyword_hits(bullet, keywords), bullet))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    job["bullets"] = [bullet for _, bullet in scored]
    blob = " ".join(
        [
            str(job.get("title") or ""),
            str(job.get("company") or ""),
            " ".join(job["bullets"]),
        ]
    )
    job["_score"] = _keyword_hits(blob, keywords)
    return job


def _score_project(item: dict[str, Any], keywords: set[str]) -> dict[str, Any]:
    project = dict(item)
    blob = " ".join(value for value in (project.get("name"), project.get("description")) if value)
    project["_score"] = _keyword_hits(blob, keywords)
    return project


def _keyword_hits(text: str, keywords: set[str]) -> int:
    lowered = (text or "").lower()
    return sum(1 for key in keywords if key and key.lower() in lowered)


def _extract_title(lines: list[str]) -> str | None:
    for line in lines[:12]:
        match = TITLE_LINE_RE.match(line)
        if match:
            return _clean_title(match.group(1))
        hiring = HIRING_RE.search(line)
        if hiring:
            return _clean_title(hiring.group(1))
    for line in lines[:8]:
        lowered = line.lower()
        if any(hint in lowered for hint in TITLE_HINTS) and 8 <= len(line) <= 90:
            if not any(token in lowered for token in ("we are", "we're", "looking for", "about ")):
                return _clean_title(line)
    return _clean_title(lines[0]) if lines and 8 <= len(lines[0]) <= 90 else None


def _clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" :-")
    value = AT_COMPANY_RE.sub("", value).strip(" -")
    return value[:120] or None


def _extract_company(lines: list[str], title: str | None) -> str | None:
    for line in lines[:15]:
        match = COMPANY_LINE_RE.match(line)
        if match:
            return match.group(1).strip()[:80]
        at_match = AT_COMPANY_RE.search(line)
        if at_match and title and title.lower() in line.lower():
            return at_match.group(1).strip()[:80]
    return None


def _extract_location(lines: list[str], text: str) -> str | None:
    for line in lines[:20]:
        match = CITY_STATE_RE.search(line)
        if match:
            return f"{match.group(1).strip()}, {match.group(2).strip()}"
        if line.lower() in {"remote", "united states", "usa"}:
            return line
    match = CITY_STATE_RE.search(text)
    if match:
        return f"{match.group(1).strip()}, {match.group(2).strip()}"
    return None


def _extract_employment_type(text: str) -> str | None:
    lowered = text.lower()
    for label, patterns in (
        ("Full-time", ("full-time", "full time", "fulltime")),
        ("Part-time", ("part-time", "part time")),
        ("Contract", ("contract", "contractor")),
        ("Internship", ("intern", "internship")),
    ):
        if any(pattern in lowered for pattern in patterns):
            return label
    return None


def _extract_workplace(text: str) -> str | None:
    lowered = text.lower()
    if "hybrid" in lowered:
        return "Hybrid"
    if "remote" in lowered:
        return "Remote"
    if "on-site" in lowered or "onsite" in lowered or "in-office" in lowered:
        return "On-site"
    return None


def _extract_skills(text: str) -> list[str]:
    found = []
    for match in SKILL_PATTERN.findall(text or ""):
        found.append(SKILL_CANONICAL[match.lower()])
    listed = re.split(r"[,;/|•\n]+", text or "")
    for part in listed:
        skill = re.sub(r"^[\-–—*]\s*", "", part).strip(" .")
        skill = re.sub(r"^(?:required|preferred|must have)\s*[:\-]\s*", "", skill, flags=re.I)
        key = skill.lower()
        if key in SKILL_CANONICAL:
            found.append(SKILL_CANONICAL[key])
    return _unique(found)


def _canonical_skill(skill: str) -> str:
    return SKILL_CANONICAL.get(skill.lower(), skill.strip())


def _bullet_items(lines: list[str]) -> list[str]:
    items = []
    for line in lines:
        value = re.sub(r"^[\-–—*]\s*", "", line).strip(" .")
        if 20 <= len(value) <= 240:
            items.append(value)
    return _unique(items)


def _keywords(text: str, skills: list[str]) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9.+#\-]{2,}", text or "")
    ranked: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        key = skill.lower()
        if key not in seen:
            seen.add(key)
            ranked.append(skill)
    for word in words:
        key = word.lower()
        if key in STOPWORDS or key in seen or len(key) < 4:
            continue
        if key in SKILL_CANONICAL:
            ranked.append(SKILL_CANONICAL[key])
        elif word[0].isupper() or key in {"backend", "frontend", "fullstack", "microservices", "pipelines"}:
            ranked.append(word if word[0].isupper() else word.title())
        else:
            continue
        seen.add(key)
    return _unique(ranked)


def _split_jd_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "header"
    bucket: list[str] = []
    for line in lines:
        key = _heading_key(line)
        if key:
            sections.setdefault(current, [])
            sections[current].extend(bucket)
            current = key
            bucket = []
            continue
        bucket.append(line)
    sections.setdefault(current, [])
    sections[current].extend(bucket)
    return sections


def _heading_key(line: str) -> str | None:
    compact = re.sub(r"[\s|:.\-]+", " ", line).strip().lower().strip(":-")
    return JD_SECTION_LOOKUP.get(compact)


def _normalize(text: str) -> str:
    text = (text or "").replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u2022", "-").replace("•", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _unique(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value:
            continue
        key = str(value).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
