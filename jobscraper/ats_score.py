"""Score a resume for ATS parseability and optional job-description match."""

from __future__ import annotations

from typing import Any

from jobscraper.jd_to_cv import (
    JobToCvError,
    _coerce_resume,
    _match_score,
    _skill_match,
    parse_job_description,
)
from jobscraper.resume_parser import ResumeParseError

ATS_FRIENDLY_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
}


class AtsScoreError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def score_resume(
    *,
    resume_text: str | None = None,
    resume: dict[str, Any] | None = None,
    job_description: str = "",
    job_title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    job_skills: list[str] | None = None,
) -> dict[str, Any]:
    parsed = _load_resume(resume_text, resume)
    job = _load_job(job_description, job_title, company, location, job_skills)
    profile = parsed.get("profile") or {}
    source = parsed.get("source") or {}
    skills = list(parsed.get("skills") or [])
    experience = list(parsed.get("experience") or [])
    education = list(parsed.get("education") or [])
    projects = list(parsed.get("projects") or [])
    certifications = list(parsed.get("certifications") or [])

    parseability, parse_checks = _score_parseability(source)
    contact, contact_checks = _score_contact(profile)
    structure, structure_checks = _score_structure(profile, skills, experience, education)
    content, content_checks = _score_content(skills, experience, projects, certifications)

    match = None
    job_match_score = None
    if job:
        match = _job_match(skills, experience, job)
        job_match_score = int(match["score"] or 0)

    breakdown = {
        "parseability": parseability,
        "contact": contact,
        "structure": structure,
        "content": content,
        "job_match": job_match_score,
    }
    score = _overall(breakdown)
    checks = parse_checks + contact_checks + structure_checks + content_checks
    if match:
        checks.extend(_match_checks(match))
    suggestions = [item["message"] for item in checks if not item["ok"]]
    matched_keywords = list((match or {}).get("matched_skills") or [])
    suggested_additions = list((match or {}).get("missing_skills") or [])
    echoed_title = (job or {}).get("title") or job_title
    return {
        "ok": True,
        "score": score,
        "grade": _grade(score),
        "label": _label(score),
        "job_title": echoed_title,
        "matched_keywords": matched_keywords,
        "suggested_additions": suggested_additions,
        "breakdown": breakdown,
        "checks": checks,
        "suggestions": suggestions,
        "match": match,
        "job": job,
        "skills": skills,
        "profile": {
            "full_name": profile.get("full_name"),
            "email": profile.get("email"),
            "phone": profile.get("phone"),
            "location": profile.get("location"),
            "linkedin": profile.get("linkedin"),
            "github": profile.get("github"),
        },
        "source": {
            "filename": source.get("filename"),
            "content_type": source.get("content_type"),
            "pages": source.get("pages"),
            "char_count": source.get("char_count"),
        },
    }


def _load_resume(resume_text: str | None, resume: dict[str, Any] | None) -> dict[str, Any]:
    try:
        parsed = _coerce_resume(resume_text, resume)
    except (JobToCvError, ResumeParseError) as exc:
        status = getattr(exc, "status_code", 400)
        raise AtsScoreError(str(exc), status) from exc
    if not parsed:
        raise AtsScoreError("Send a resume file, resume_text, or parsed resume JSON.")
    if resume and isinstance(resume.get("source"), dict):
        parsed["source"] = {**resume["source"], **(parsed.get("source") or {})}
        parsed["source"].update({k: v for k, v in resume["source"].items() if v not in (None, "")})
    source = parsed.setdefault("source", {})
    if source.get("char_count") is None:
        blob = " ".join(
            [
                str((parsed.get("profile") or {}).get("summary") or ""),
                " ".join(parsed.get("skills") or []),
            ]
        )
        source["char_count"] = len(blob)
    return parsed


def _load_job(
    job_description: str,
    job_title: str | None,
    company: str | None,
    location: str | None,
    job_skills: list[str] | None,
) -> dict[str, Any] | None:
    jd_text = (job_description or "").strip()
    if not jd_text and not job_title and not job_skills:
        return None
    if job_skills and not jd_text:
        bits = [job_title or "Open role"]
        if company:
            bits.append(f"Company: {company}")
        bits.append("Required skills: " + ", ".join(job_skills))
        jd_text = "\n".join(bits)
    if len(jd_text) < 40 and not (job_title or job_skills):
        raise AtsScoreError("Job description is too short. Paste the posting text or provide a job title and skills.")
    return parse_job_description(
        jd_text,
        title=job_title,
        company=company,
        location=location,
        extra_skills=job_skills,
    )


def _score_parseability(source: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    filename = str(source.get("filename") or "").lower()
    ctype = str(source.get("content_type") or "").split(";")[0].strip().lower()
    pages = source.get("pages")
    chars = int(source.get("char_count") or 0)
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1]

    friendly = ext in {".pdf", ".docx", ".txt"} or ctype in ATS_FRIENDLY_TYPES
    length_ok = chars >= 600
    page_ok = pages is None or 1 <= int(pages) <= 2
    too_long = pages is not None and int(pages) > 3

    score = 40 if friendly else 15
    score += 35 if length_ok else 15 if chars >= 250 else 5
    if pages is None:
        score += 20
    elif 1 <= int(pages) <= 2:
        score += 25
    elif int(pages) == 3:
        score += 15
    else:
        score += 5

    checks = [
        _check(
            "file_type",
            friendly,
            "PDF or DOCX is ATS-friendly." if friendly else "Upload a PDF or DOCX. Scanned images and old .doc files parse poorly.",
        ),
        _check(
            "extractable_text",
            length_ok,
            "Enough plain text was extracted." if length_ok else "Too little text was extracted. Avoid image-only or heavily designed layouts.",
        ),
        _check(
            "length",
            page_ok and not too_long,
            "Resume length looks ATS-friendly (about 1-2 pages)."
            if page_ok
            else "Keep the resume to 1-2 pages. Very long files can be truncated by ATS parsers.",
        ),
    ]
    return min(100, score), checks


def _score_contact(profile: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    name = bool(profile.get("full_name"))
    email = bool(profile.get("email"))
    phone = bool(profile.get("phone") or profile.get("phones"))
    location = bool(profile.get("location"))
    linkedin = bool(profile.get("linkedin"))
    score = (25 if name else 0) + (25 if email else 0) + (20 if phone else 0) + (15 if location else 0) + (15 if linkedin else 0)
    checks = [
        _check("name", name, "Name found." if name else "Add a clear full name at the top of the resume."),
        _check("email", email, "Email found." if email else "Add a professional email address."),
        _check("phone", phone, "Phone number found." if phone else "Add a phone number ATS systems can parse."),
        _check("location", location, "Location found." if location else "Add a city and state/country."),
        _check("linkedin", linkedin, "LinkedIn URL found." if linkedin else "Add a LinkedIn profile URL."),
    ]
    return score, checks


def _score_structure(
    profile: dict[str, Any],
    skills: list[str],
    experience: list[dict[str, Any]],
    education: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    has_summary = bool((profile.get("summary") or "").strip())
    has_skills = len(skills) >= 3
    has_experience = bool(experience)
    has_education = bool(education)
    dated = any(item.get("start_date") or item.get("end_date") for item in experience)
    score = (
        (10 if has_summary else 0)
        + (30 if has_skills else 10 if skills else 0)
        + (30 if has_experience else 0)
        + (20 if has_education else 0)
        + (10 if dated else 0)
    )
    checks = [
        _check("summary", has_summary, "Professional summary found." if has_summary else "Add a short professional summary near the top."),
        _check("skills_section", has_skills, "Skills section found." if has_skills else "Add a Skills section with standard keywords (Python, SQL, AWS, ...)."),
        _check("experience_section", has_experience, "Work experience found." if has_experience else "Add a Work Experience section with job titles and employers."),
        _check("education_section", has_education, "Education found." if has_education else "Add an Education section."),
        _check("dates", dated, "Employment dates found." if dated else "Add start and end dates on each role (ATS uses dates for timeline parsing)."),
    ]
    return min(100, score), checks


def _score_content(
    skills: list[str],
    experience: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    certifications: list[str],
) -> tuple[int, list[dict[str, Any]]]:
    bullets = sum(len(item.get("bullets") or []) for item in experience)
    skill_score = min(40, len(skills) * 5)
    bullet_score = min(30, bullets * 5)
    role_score = min(20, len(experience) * 8)
    extra_score = min(10, (4 if projects else 0) + (6 if certifications else 0))
    score = skill_score + bullet_score + role_score + extra_score
    checks = [
        _check(
            "skill_count",
            len(skills) >= 6,
            f"{len(skills)} skills found." if skills else "List 6+ job-relevant skills using common ATS keywords.",
        ),
        _check(
            "bullets",
            bullets >= 4,
            "Experience bullets found." if bullets >= 4 else "Add 3-5 result-focused bullets under each role.",
        ),
        _check(
            "roles",
            len(experience) >= 1,
            "At least one role found." if experience else "Include at least one work or internship role.",
        ),
    ]
    return min(100, score), checks


def _job_match(skills: list[str], experience: list[dict[str, Any]], job: dict[str, Any]) -> dict[str, Any]:
    required = list(job.get("required_skills") or [])
    preferred = list(job.get("preferred_skills") or [])
    target = _unique([*required, *preferred])
    matched, missing, extra = _skill_match(skills, target)
    matched_required, missing_required, _ = _skill_match(skills, required or target)
    return {
        "score": _match_score(matched_required, missing_required, experience),
        "matched_skills": matched,
        "missing_skills": missing,
        "extra_skills": extra,
        "job_title": job.get("title"),
        "company": job.get("company"),
    }


def _match_checks(match: dict[str, Any]) -> list[dict[str, Any]]:
    missing = list(match.get("missing_skills") or [])
    matched = list(match.get("matched_skills") or [])
    ok = (match.get("score") or 0) >= 70
    if not missing:
        message = f"Resume keywords overlap this job ({len(matched)} matched)."
    else:
        preview = ", ".join(missing[:8])
        message = f"Add missing job keywords: {preview}."
    return [_check("job_keywords", ok, message)]


def _overall(breakdown: dict[str, Any]) -> int:
    job_match = breakdown.get("job_match")
    if job_match is None:
        weights = {
            "parseability": 0.20,
            "contact": 0.25,
            "structure": 0.25,
            "content": 0.30,
        }
    else:
        weights = {
            "parseability": 0.15,
            "contact": 0.15,
            "structure": 0.15,
            "content": 0.15,
            "job_match": 0.40,
        }
    total = 0.0
    for key, weight in weights.items():
        total += float(breakdown.get(key) or 0) * weight
    return int(round(total))


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 50:
        return "D"
    return "F"


def _label(score: int) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 80:
        return "Strong"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Needs work"


def _check(check_id: str, ok: bool, message: str) -> dict[str, Any]:
    return {"id": check_id, "ok": ok, "message": message}


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
