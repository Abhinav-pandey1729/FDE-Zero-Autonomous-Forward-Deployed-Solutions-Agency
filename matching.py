"""Pure profile normalization and deterministic co-founder scoring."""

import hashlib
import json
import re
from datetime import datetime, timezone

import db


def ident(x):
    return hashlib.sha256(str(x).encode()).hexdigest()[:20]


def field(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def person_id(p):
    return str(
        p.get("person_id")
        or p.get("crustdata_person_id")
        or field(p, "social_handles.professional_network_identifier.profile_url")
        or ident(db.dump(p))
    )


def role(p):
    return str(field(p, "basic_profile.current_title") or "")


def city(p):
    value = field(p, "basic_profile.location") or field(p, "basic_profile.city") or ""
    if isinstance(value, dict):
        value = value.get("city") or value.get("name") or ""
    value = str(value).lower()
    return (
        "Bengaluru"
        if "bangalore" in value or "bengaluru" in value
        else "NCR"
        if any(x in value for x in ("delhi", "gurgaon", "gurugram", "noida", "ncr"))
        else value.strip().title() or "Unknown"
    )


def school(p):
    return {
        str(x.get("school", "")).lower().strip()
        for x in (
            field(p, "education.schools")
            if isinstance(field(p, "education.schools"), list)
            else []
        )
        if isinstance(x, dict) and x.get("school")
    }


def vertical(p):
    text = (str(p.get("vertical") or "") + " " + db.dump(p)).lower()
    if any(x in text for x in ("fintech", "razorpay", "payments")):
        return "Fintech"
    if any(x in text for x in ("saas", "freshworks")):
        return "B2B SaaS"
    if re.search(r"\b(ai|machine learning)\b", text):
        return "AI"
    return "General"


def compute_cofounder_match_score(a, b, company):
    pa = json.loads(a["profile"])
    pb = json.loads(b["profile"])
    ca = json.loads(a["citations"])
    cb = json.loads(b["citations"])
    co = json.loads(company["data"])
    cc = co.get("citations", {})

    def refs(*items):
        return [x for x in items if x]

    same = bool(school(pa) & school(pb))
    samecity = city(pa) != "Unknown" and city(pa) == city(pb)
    shared = vertical(pa) == vertical(pb) and vertical(pa) != "General"
    ra = role(pa).lower()
    rb = role(pb).lower()
    complementary = bool(
        re.search("engineer|technical|security|machine learning|cto", ra)
        and re.search("product|gtm|sales|business|growth|operator", rb)
    )
    funding = co.get("funding_date")
    old = False
    try:
        old = (
            datetime.now(timezone.utc).date()
            - datetime.fromisoformat(funding[:10]).date()
        ).days > 730
    except (TypeError, ValueError):
        pass
    transition = bool(
        re.search(
            r"(?i)\b(ex[-\s]|former|building|stealth)",
            str(field(pb, "basic_profile.headline") or ""),
        )
    )

    def senior(title):
        return bool(
            re.search(
                r"(?i)\b(vp|chief|cto|head|director|lead|staff|senior|manager)\b", title
            )
        )

    parts = {
        "Pedigree": {
            "score": 25 if same else 0,
            "reason": "Shared school" if same else "No shared school established",
            "citations": refs(ca.get("education.schools"), cb.get("education.schools")),
        },
        "City": {
            "score": 25
            if samecity
            else (5 if city(pa) != "Unknown" and city(pb) != "Unknown" else 0),
            "reason": f"{city(pa)} / {city(pb)}",
            "citations": refs(
                ca.get("basic_profile.location"), cb.get("basic_profile.location")
            ),
        },
        "Domain": {
            "score": (12 if shared else 0) + (13 if complementary else 0),
            "reason": f"{vertical(pa)} / {vertical(pb)}; {'complementary roles' if complementary else 'complementarity not established'}",
            "citations": refs(
                ca.get("basic_profile.current_title"),
                cb.get("basic_profile.current_title"),
                ca.get("basic_profile.headline"),
                cb.get("basic_profile.headline"),
                ca.get("experience.employment_details.current"),
                cb.get("experience.employment_details.current"),
                ca.get("vertical"),
                cb.get("vertical"),
            ),
        },
        "Career": {
            "score": (10 if senior(ra) and senior(rb) else 0)
            + (8 if old else 0)
            + (7 if transition else 0),
            "reason": f"Seniority alignment: {senior(ra) and senior(rb)}; funding gap >24 months: {old}; transition signal: {transition}",
            "citations": refs(
                ca.get("basic_profile.current_title"),
                cb.get("basic_profile.current_title"),
                cc.get("funding.last_fundraise_date"),
                cb.get("basic_profile.headline"),
            ),
        },
    }
    return {"score": sum(x["score"] for x in parts.values()), "breakdown": parts}
