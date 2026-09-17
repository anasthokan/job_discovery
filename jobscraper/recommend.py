"""Rank scraped job listings against a candidate skill list."""

from __future__ import annotations

from jobscraper.jd_to_cv import SKILL_RELATED
from jobscraper.resume_parser import SKILL_CANONICAL

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def normalize_skills(raw: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in raw or []:
        skill = str(value or "").strip(" ,;|/").strip()
        if not skill:
            continue
        skill = SKILL_CANONICAL.get(skill.lower(), skill)
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(skill)
    return out


def parse_skill_query(raw: str) -> list[str]:
    parts = [part.strip() for part in (raw or "").replace(";", ",").split(",")]
    return normalize_skills(parts)


def recommend_jobs(jobs: list[dict], skills: list[str], *, limit: int = DEFAULT_LIMIT) -> list[dict]:
    wanted = normalize_skills(skills)
    if not wanted:
        return []
    capped = _clamp_limit(limit)
    ranked = []
    for job in jobs:
        match = score_job(job, wanted)
        if match["score"] <= 0:
            continue
        ranked.append({**job, "match": match})
    ranked.sort(key=lambda row: (-row["match"]["score"], row.get("daysAgo") or 0, row.get("title") or ""))
    return ranked[:capped]


def score_job(job: dict, skills: list[str]) -> dict:
    wanted = normalize_skills(skills)
    job_skills = normalize_skills(job.get("skills") or [])
    blob = " ".join(
        [
            str(job.get("title") or ""),
            str(job.get("company") or ""),
            str(job.get("location") or ""),
            str(job.get("state") or ""),
            " ".join(job_skills),
        ]
    ).lower()
    title = str(job.get("title") or "").lower()
    job_keys = {item.lower() for item in job_skills}

    matched: list[str] = []
    missing: list[str] = []
    points = 0
    max_points = max(len(wanted) * 2, 1)
    for skill in wanted:
        key = skill.lower()
        related = SKILL_RELATED.get(key, set())
        in_listed = key in job_keys or any(item in job_keys for item in related)
        in_title = key in title or any(item in title for item in related)
        in_blob = key in blob or any(item in blob for item in related)
        if in_listed or in_title or in_blob:
            matched.append(skill)
            points += 2 if in_listed or in_title else 1
        else:
            missing.append(skill)
    score = int(round(100 * points / max_points))
    return {
        "score": min(100, score),
        "matched_skills": matched,
        "missing_skills": missing,
    }


def _clamp_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))
