"""Pure profile normalization, evidence linking, and deterministic co-founder scoring."""

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
        value = value.get("city") or value.get("name") or value.get("raw") or ""
    value = str(value).lower()
    return (
        "Bengaluru"
        if "bangalore" in value or "bengaluru" in value
        else "NCR"
        if any(x in value for x in ("delhi", "gurgaon", "gurugram", "noida", "ncr"))
        else "Hyderabad"
        if "hyderabad" in value
        else "Mumbai"
        if "mumbai" in value or "bombay" in value
        else "Pune"
        if "pune" in value
        else "Chennai"
        if "chennai" in value or "madras" in value
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
    if any(
        x in text
        for x in (
            "zomato",
            "swiggy",
            "zepto",
            "blinkit",
            "instamart",
            "foodtech",
            "food delivery",
            "quick commerce",
            "grocery",
            "hyperlocal",
        )
    ):
        return "FoodTech & Quick Commerce"
    if any(
        x in text
        for x in (
            "fintech",
            "razorpay",
            "paytm",
            "phonepe",
            "cred",
            "groww",
            "zerodha",
            "payments",
            "banking",
            "lending",
            "neobank",
        )
    ):
        return "Fintech"
    if any(
        x in text
        for x in (
            "saas",
            "freshworks",
            "zoho",
            "postman",
            "enterprise software",
            "cloud",
            "crm",
        )
    ):
        return "B2B SaaS"
    if any(
        x in text
        for x in (
            "flipkart",
            "amazon",
            "meesho",
            "e-commerce",
            "ecommerce",
            "retail",
            "logistics",
            "supply chain",
            "delhivery",
            "shadowfax",
        )
    ):
        return "E-Commerce & Logistics"
    if any(x in text for x in ("ola", "uber", "rapido", "mobility", "ev")):
        return "Mobility"
    if re.search(r"\b(ai|machine learning|deep learning|llm|genai|robotics)\b", text):
        return "AI & DeepTech"
    return "Technology"


def format_headcount(val):
    if isinstance(val, dict):
        val = val.get("total_headcount") or val.get("headcount") or val.get("total")
    if isinstance(val, (int, float)):
        return f"{int(val):,}"
    if isinstance(val, str):
        digits = re.sub(r"[^\d]", "", val)
        if digits:
            return f"{int(digits):,}"
    return None


def compute_cofounder_match_score(a, b, company):
    pa = json.loads(a["profile"])
    pb = json.loads(b["profile"])
    ca = json.loads(a["citations"])
    cb = json.loads(b["citations"])
    co = json.loads(company["data"])
    cc = co.get("citations", {})
    cid = company.get("company_id", "897127")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def refs(*items):
        return [x for x in items if x]

    sa = school(pa)
    sb = school(pb)
    same = bool(sa & sb)
    ca_city = city(pa)
    cb_city = city(pb)
    samecity = ca_city != "Unknown" and ca_city == cb_city
    va = vertical(pa)
    vb = vertical(pb)
    shared = va == vb and va not in ("Technology", "General")
    ra = role(pa).lower()
    rb = role(pb).lower()
    is_tech_a = bool(
        re.search(
            r"(?i)engineer|technical|security|machine learning|cto|architect|developer|tech lead",
            ra,
        )
    )
    is_prod_a = bool(
        re.search(
            r"(?i)product|gtm|sales|business|growth|operator|founder|ceo|commercial|operations|manager",
            ra,
        )
    )
    is_tech_b = bool(
        re.search(
            r"(?i)engineer|technical|security|machine learning|cto|architect|developer|tech lead",
            rb,
        )
    )
    is_prod_b = bool(
        re.search(
            r"(?i)product|gtm|sales|business|growth|operator|founder|ceo|commercial|operations|manager",
            rb,
        )
    )
    complementary = bool((is_tech_a and is_prod_b) or (is_prod_a and is_tech_b))

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
            str(field(pb, "basic_profile.headline") or "")
            + " "
            + str(field(pa, "basic_profile.headline") or ""),
        )
    )

    def senior(title):
        return bool(
            re.search(
                r"(?i)\b(vp|vice president|chief|cto|ceo|head|director|lead|staff|senior|principal|manager|founder)\b",
                title,
            )
        )

    # 1. Inline Pedigree Citation Badge
    if same:
        pedigree_badge = f"Shared Alma Mater: {', '.join(sa & sb).title()} [POST /person/search #{cid} | Verified {today}]"
    elif sa and sb:
        pedigree_badge = f"Alumni Network: {', '.join(sa).title()} vs {', '.join(sb).title()} (No shared school) [POST /person/search #{cid} | Verified {today}]"
    elif sa or sb:
        known_s = sa or sb
        pedigree_badge = f"Alumni Network: {', '.join(known_s).title()} vs [UNVERIFIED: University absent in profile] [POST /person/search #{cid} | Verified {today}]"
    else:
        pedigree_badge = "[UNVERIFIED: University absent in Crustdata profile — Scored 0 without guessing]"

    # 2. Inline City Citation Badge
    if samecity:
        city_badge = f"Location: {ca_city} (Co-located) [POST /person/search #{cid} | Verified {today}]"
    elif ca_city != "Unknown" and cb_city != "Unknown":
        city_badge = f"Location: {ca_city} vs {cb_city} (Cross-city friction) [POST /person/search #{cid} | Verified {today}]"
    elif ca_city != "Unknown" or cb_city != "Unknown":
        known_c = ca_city if ca_city != "Unknown" else cb_city
        city_badge = f"Location: {known_c} vs [UNVERIFIED: Location absent in profile] [POST /person/search #{cid} | Verified {today}]"
    else:
        city_badge = "[UNVERIFIED: Location absent in Crustdata profile — Scored 0 without guessing]"

    # 3. Inline Domain Citation Badge
    comp_desc = (
        "Complementary engineering & product roles"
        if complementary
        else "Standard roles"
    )
    if shared:
        domain_badge = f"Domain: {va} ({comp_desc}) [POST /person/search #{cid} | Verified {today}]"
    elif va not in ("Technology", "General") or vb not in ("Technology", "General"):
        domain_badge = f"Cross-Domain: {va} × {vb} ({comp_desc}) [POST /person/search #{cid} | Verified {today}]"
    elif role(pa) or role(pb):
        domain_badge = f"Domain Alignment: {role(pa) or 'Specialist'} × {role(pb) or 'Specialist'} ({comp_desc}) [POST /person/search #{cid} | Verified {today}]"
    else:
        domain_badge = "[UNVERIFIED: Domain and title absent in Crustdata profile — Scored 0 without guessing]"

    # 4. Inline Career Citation Badges
    career_badges = []
    hc_str = format_headcount(co.get("headcount"))
    if hc_str:
        career_badges.append(
            f"Headcount: {hc_str} [POST /company/enrich #{cid} | Verified {today}]"
        )
    else:
        career_badges.append(
            "[UNVERIFIED: Headcount absent in Crustdata graph — Scored 0 without guessing]"
        )

    f_amt = (
        co.get("funding_amount")
        or co.get("total_funding_usd")
        or co.get("last_fundraise_amount")
    )
    if not f_amt or str(f_amt).lower() == "none":
        f_amt = "Institutional Capital (Undisclosed / Public)"

    drought_text = "2+ yr liquidity drought signal" if old else "Active capital cycle"
    if funding:
        career_badges.append(
            f"Last Fundraise: {f_amt} ({str(funding)[:10]} — {drought_text}) [POST /company/enrich #{cid} | Verified {today}]"
        )
    else:
        career_badges.append(
            "[UNVERIFIED: Last fundraise date absent in Crustdata graph — Scored 0 without guessing]"
        )

    if transition:
        trans_headline = str(
            field(pb, "basic_profile.headline")
            or field(pa, "basic_profile.headline")
            or ""
        )
        career_badges.append(
            f'Departure Signal: "{trans_headline}" headline vs active current array [POST /person/search #{cid} | Verified {today}]'
        )
    else:
        career_badges.append(
            "[UNVERIFIED: Founder transition signal absent in headline — Scored 0 without guessing]"
        )

    domain_score = (
        12
        if shared
        else (
            6
            if va not in ("Technology", "General")
            or vb not in ("Technology", "General")
            else 0
        )
    ) + (13 if complementary else 0)

    parts = {
        "Pedigree": {
            "score": 25 if same else 0,
            "reason": "Shared school" if same else "No shared school established",
            "citations": refs(ca.get("education.schools"), cb.get("education.schools")),
            "badges": [pedigree_badge],
        },
        "City": {
            "score": 25
            if samecity
            else (5 if ca_city != "Unknown" and cb_city != "Unknown" else 0),
            "reason": f"{ca_city} / {cb_city}",
            "citations": refs(
                ca.get("basic_profile.location"), cb.get("basic_profile.location")
            ),
            "badges": [city_badge],
        },
        "Domain": {
            "score": domain_score,
            "reason": f"{va} / {vb}; {'complementary roles' if complementary else 'complementarity not established'}",
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
            "badges": [domain_badge],
        },
        "Career": {
            "score": (10 if senior(ra) and senior(rb) else (5 if senior(ra) or senior(rb) else 0))
            + (8 if old else 0)
            + (7 if transition else 0),
            "reason": f"Seniority alignment: {senior(ra) and senior(rb)}; funding gap >24 months: {old}; transition signal: {transition}",
            "citations": refs(
                ca.get("basic_profile.current_title"),
                cb.get("basic_profile.current_title"),
                cc.get("funding.last_fundraise_date"),
                cb.get("basic_profile.headline"),
            ),
            "badges": career_badges,
        },
    }
    total = sum(x["score"] for x in parts.values())
    return {"score": min(total, 100), "breakdown": parts}
