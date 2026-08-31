"""Strict profile-only job matching.

There is deliberately no scoring or hidden role taxonomy here. A job is either
accepted because it satisfies the user's saved profile or rejected. Every
criterion used by this matcher comes directly from SearchProfile.
"""
import re
import unicodedata
from app.schemas import SearchProfile
from app.collectors.base import RawJob


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower().replace("ß", "ss")
    return re.sub(r"[^a-z0-9äöü\s+#./-]", " ", value)


def _contains(text: str, phrase: str) -> bool:
    p = _norm(phrase).strip()
    return bool(p and p in _norm(text))


EMPLOYMENT_ALIASES = {
    "werkstudent": ["werkstudent", "working student", "studentische hilfskraft", "student assistant"],
    "vollzeit": ["vollzeit", "full-time", "full time", "fulltime"],
    "teilzeit": ["teilzeit", "part-time", "part time", "parttime"],
    "praktikum": ["praktikum", "praktikant", "internship", "intern"],
}


def _employment_type(job: RawJob) -> str:
    text = " ".join((job.employment_type or "", job.title or "", job.description or ""))
    for canonical, aliases in EMPLOYMENT_ALIASES.items():
        if any(_contains(text, a) for a in aliases):
            return canonical
    return ""


def _profile_types(profile: SearchProfile) -> set[str]:
    out = set()
    for value in profile.employment_types or []:
        n = _norm(value)
        for canonical, aliases in EMPLOYMENT_ALIASES.items():
            if n == canonical or any(_contains(n, a) for a in aliases):
                out.add(canonical)
                break
        else:
            out.add(n)
    return out


def _hours(job: RawJob) -> list[int]:
    text = " ".join((job.hours or "", job.description or ""))
    values = []
    for m in re.finditer(r"\b(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*(?:stunden|std\.?|hours|hrs?)(?:\s*(?:/|pro)\s*(?:woche|week))?\b", text, re.I):
        values.append(int(m.group(1)))
        if m.group(2):
            values.append(int(m.group(2)))
    return values


def _location_ok(job: RawJob, profile: SearchProfile) -> tuple[bool, str]:
    wanted = _norm(profile.location)
    if not wanted:
        return True, "Ort nicht eingeschränkt"
    loc = _norm(job.location)
    body = _norm(job.description)
    remote = _norm(job.remote_type)
    remote_allowed = any(_contains(remote, x) for x in (profile.remote_types or []))
    if wanted in loc:
        return True, f"Ort: {profile.location}"
    if remote_allowed and "remote" in remote and "hybrid" not in remote:
        return True, "Remote gemäß Profil"
    if not loc and wanted in body:
        return True, f"Ort: {profile.location}"
    if wanted == "berlin" and re.search(r"\b(?:10|12|13|14)\d{3}\b", loc):
        return True, "Ort: Berlin"
    return False, f"Ort passt nicht zu {profile.location}"


def match_profile(job: RawJob, profile: SearchProfile) -> tuple[bool, list[str]]:
    """Return PASS/REJECT using only fields explicitly stored in the profile."""
    title = job.title or ""
    body = " ".join((job.description or "", job.employment_type or "", job.hours or "", job.remote_type or ""))
    full = " ".join((title, job.company or "", job.location or "", body))
    reasons: list[str] = []

    # Explicit user exclusions are absolute.
    for exclusion in profile.exclusions or []:
        if _contains(full, exclusion):
            return False, [f"Ausschlussbegriff: {exclusion}"]

    # Employment type is a profile requirement when the user selected one.
    wanted_types = _profile_types(profile)
    detected = _employment_type(job)
    if wanted_types:
        if detected and detected not in wanted_types:
            return False, [f"Falsche Anstellungsart: {detected}"]
        if detected:
            reasons.append(f"Anstellungsart: {detected.title()}")
        else:
            reasons.append("Anstellungsart im Angebot nicht eindeutig")

    ok, location_reason = _location_ok(job, profile)
    if not ok:
        return False, [location_reason]
    reasons.append(location_reason)

    roles = [x.strip() for x in (profile.target_roles or []) if x and x.strip()]
    keywords = [x.strip() for x in (profile.keywords or []) if x and x.strip()]
    skills = [x.strip() for x in (profile.skills or []) if x and x.strip()]

    # If the user specified roles/keywords, at least one of those profile terms
    # must actually occur in the concrete job. No built-in semantic families.
    role_hits = [r for r in roles if _contains(title, r) or _contains(body, r)]
    keyword_hits = [k for k in keywords if _contains(full, k)]
    if roles or keywords:
        if not role_hits and not keyword_hits:
            return False, ["Keine gewünschte Position bzw. kein Profil-Keyword gefunden"]
        if role_hits:
            reasons.append("Position: " + ", ".join(role_hits[:6]))
        if keyword_hits:
            reasons.append("Keywords: " + ", ".join(keyword_hits[:6]))

    skill_hits = [s for s in skills if _contains(full, s)]
    # Skills are only mandatory when they are the only relevance information in
    # the profile. Otherwise they provide transparent evidence, never points.
    if skills and not roles and not keywords and not skill_hits:
        return False, ["Keine Profil-Skills in der Stelle gefunden"]
    if skill_hits:
        reasons.append("Skills: " + ", ".join(skill_hits[:8]))

    if profile.hours_min <= profile.hours_max and (profile.hours_min > 0 or profile.hours_max < 80):
        hours = _hours(job)
        if hours and not any(profile.hours_min <= h <= profile.hours_max for h in hours):
            return False, [f"Wochenstunden außerhalb des Profils: {job.hours or 'nicht angegeben'}"]
        if hours:
            reasons.append(f"Stunden: {profile.hours_min}–{profile.hours_max}")
        else:
            reasons.append("Wochenstunden nicht angegeben")

    if profile.remote_types:
        remote = _norm(job.remote_type)
        if remote and not any(_contains(remote, x) for x in profile.remote_types):
            # Do not reject a job solely because the parser has no exact remote
            # label; reject only an explicit conflicting model.
            if "remote" in remote or "hybrid" in remote or "vor ort" in remote or "onsite" in remote:
                return False, [f"Arbeitsmodell passt nicht: {job.remote_type}"]
        elif remote:
            reasons.append(f"Arbeitsmodell: {job.remote_type}")

    return True, reasons or ["Stelle erfüllt die Profilfilter"]


# Backwards-compatible name for integrations/tests. It returns only 100 for a
# profile PASS and 0 for REJECT; the scan does not use it as a ranking score.
def score_job(job: RawJob, profile: SearchProfile) -> tuple[int, list[str]]:
    matched, reasons = match_profile(job, profile)
    return (100 if matched else 0), reasons
