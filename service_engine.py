"""Checkpointed discovery, evidence collection, and founder pathway routing."""

import json
import re
import uuid

import db
from crustdata_client import CrustdataClient
from matching import (
    city,
    compute_cofounder_match_score,
    field,
    ident,
    person_id,
    role,
    vertical,
)
from scheduler_service import book_meeting

STAGES = ["IDENTIFY", "MARKET", "PEOPLE", "MATCH_AND_ROUTE"]


def checkpointed(run, scope, stage, fn, crash):
    data = db.load(run, scope, stage)
    if data is not None:
        db.execute(
            "INSERT INTO savings(run_id,source,endpoint,amount,created_at) VALUES(?,?,?,?,?)",
            (run, "checkpoint", stage, 0, db.now()),
        )
        return data
    data = fn()
    db.checkpoint(run, scope, stage, data)
    if crash == stage:
        raise RuntimeError("Simulated crash after " + stage)
    return data


def source_endpoint(client, endpoint):
    source = getattr(client, "last_source", client.mode)
    return (
        "POST " if source == "live" else "SYNTHETIC " + source.upper() + " "
    ) + endpoint


def cite_fields(run, pid, endpoint, raw, paths):
    return {p: db.cite(run, pid, endpoint, p, field(raw, p), raw) for p in paths}


def discover_companies(run, identifiers, client, mode):
    ids = []
    expanded = []
    for value in identifiers or []:
        found = re.findall(r"[a-zA-Z0-9-]+\.[a-zA-Z]{2,}", value)
        if found:
            expanded.extend(found)
        elif re.search(r"(?i)\b(match|cofounders|founders|from|and)\b", value):
            expanded.extend(
                re.findall(
                    r"[a-zA-Z0-9-]+",
                    re.sub(
                        r"(?i)\b(match|cofounders|founders|from|and|for|the)\b",
                        " ",
                        value,
                    ),
                )
            )
        else:
            expanded.append(value)
    for item in expanded:
        item = item.strip()
        payload = {"domains" if "." in item else "names": [item]}
        result = client.post("/company/identify", payload)
        matches = (
            (result[0].get("matches") or [])
            if isinstance(result, list) and result
            else []
        )
        match = (
            max(matches, key=lambda x: x.get("confidence_score") or 0)
            if matches
            else {}
        )
        co = match.get("company_data") or {}
        cid = str(co.get("crustdata_company_id") or "")
        if not cid:
            continue
        basic = co.get("basic_info") or {}
        db.execute(
            "INSERT OR IGNORE INTO companies VALUES(?,?,?,?,?)",
            (
                run,
                cid,
                basic.get("name") or item,
                basic.get("primary_domain") or "",
                db.dump({}),
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO aliases VALUES(?,?,?)", (run, item.lower(), cid)
        )
        ids.append(cid)
        db.cite(
            run,
            cid,
            (
                "SYNTHETIC DEMO /company/identify"
                if mode == "demo" or co.get("synthetic_fallback")
                else "POST /company/identify"
            ),
            "crustdata_company_id",
            cid,
            match,
        )
    if not ids:
        raise RuntimeError("No companies resolved; enter valid names or domains.")
    return list(dict.fromkeys(ids))


def research_market(run, cid, client, mode):
    data = client.post(
        "/company/enrich",
        {
            "crustdata_company_ids": [int(cid)],
            "fields": ["crustdata_company_id", "basic_info", "funding"],
        },
    )
    matches = (data[0].get("matches") or []) if isinstance(data, list) and data else []
    raw = (matches[0].get("company_data") or {}) if matches else {}
    if field(raw, "funding.last_fundraise_date") is None:
        db.audit(
            run,
            "UNVERIFIED_FIELD_ABSENT",
            {
                "company_id": cid,
                "field": "funding.last_fundraise_date",
                "message": "UNVERIFIED: Field absent in Crustdata graph — Scored 0 without guessing",
            },
        )
    refs = cite_fields(
        run,
        cid,
        source_endpoint(client, "/company/enrich"),
        raw,
        ["funding.last_fundraise_date"],
    )
    payload = {
        "filters": {
            "op": "and",
            "conditions": [
                {
                    "field": "company.basic_info.company_id",
                    "type": "in",
                    "value": [int(cid)],
                },
                {"field": "job_details.title", "type": "(.)", "value": "engineer"},
            ],
        },
        "limit": 20,
        "fields": ["job_details", "company.basic_info", "location", "metadata"],
    }
    if mode == "demo":
        payload["demo_company_id"] = cid
    response = client.post("/job/search", payload)
    jobs = (response.get("job_listings") or []) if isinstance(response, dict) else []
    jobrefs = [
        {
            "title": field(j, "job_details.title"),
            "citation": db.cite(
                run,
                cid,
                source_endpoint(client, "/job/search"),
                "job_details.title",
                field(j, "job_details.title"),
                j,
            ),
        }
        for j in jobs
    ]
    value = {
        "funding_date": field(raw, "funding.last_fundraise_date"),
        "citations": refs,
        "jobs": jobrefs,
    }
    db.execute(
        "UPDATE companies SET data=? WHERE run_id=? AND company_id=?",
        (db.dump(value), run, cid),
    )
    return value


def discover_people(run, cid, client, mode):
    payload = {
        "filters": {
            "op": "and",
            "conditions": [
                {
                    "field": "experience.employment_details.current.company_id",
                    "type": "=",
                    "value": int(cid),
                }
            ],
        },
        "limit": 50,
        "fields": ["basic_profile", "experience", "education", "social_handles"],
    }
    if mode == "demo":
        payload["demo_company_id"] = cid
    result = client.post("/person/search", payload)
    profiles = (result.get("profiles") or []) if isinstance(result, dict) else []
    for p in profiles:
        if not isinstance(p, dict):
            continue
        pid = person_id(p)
        b = p.get("basic_profile") if isinstance(p.get("basic_profile"), dict) else {}
        headline = str(b.get("headline") or "")
        title = role(p)
        current = field(p, "experience.employment_details.current") or []
        matching = any(
            str(j.get("crustdata_company_id") or j.get("company_id")) == cid
            for j in current
            if isinstance(j, dict)
        )
        transition = bool(
            re.search(r"(?i)\b(ex[-\s]|former|building|stealth)", headline)
        )
        technical = bool(
            re.search(r"(?i)engineer|technical|security|machine learning|cto", title)
        )
        senior = bool(
            re.search(
                r"(?i)senior|staff|lead|head|director|vp|manager|chief|cto", title
            )
        )
        pool = (
            "A"
            if technical and senior and matching and not transition
            else "B"
            if transition or (matching and senior)
            else ""
        )
        if not pool:
            continue
        if transition:
            db.audit(
                run,
                "FREE_AGENT_DETECTED",
                {
                    "person_id": pid,
                    "signal": "headline transition; not verified departure",
                },
            )
        paths = [
            "basic_profile.name",
            "basic_profile.current_title",
            "basic_profile.headline",
            "basic_profile.location",
            "experience.employment_details.current",
            "education.schools",
            "vertical",
        ]
        for path in ("basic_profile.location", "education.schools"):
            if field(p, path) is None:
                db.audit(
                    run,
                    "UNVERIFIED_FIELD_ABSENT",
                    {
                        "person_id": pid,
                        "field": path,
                        "message": "UNVERIFIED: Field absent in Crustdata graph — Scored 0 without guessing",
                    },
                )
        refs = cite_fields(
            run,
            pid,
            source_endpoint(client, "/person/search"),
            p,
            paths,
        )
        db.execute(
            "INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?)",
            (
                run,
                pid,
                cid,
                b.get("name") or "Missing identity",
                pool,
                db.dump(p),
                db.dump(refs),
            ),
        )
        reasons = []
        if mode == "demo" and pid == "demo-d":
            reasons.append(
                "Synthetic vendor discrepancy case: conflicting employment/identity requires review"
            )
        if not b.get("name") or not (
            p.get("person_id")
            or p.get("crustdata_person_id")
            or field(p, "social_handles.professional_network_identifier.profile_url")
        ):
            reasons.append("Missing critical identity fields")
        if not matching and not transition:
            reasons.append("Target employment claim is unverified")
        if reasons:
            db.execute(
                "INSERT OR IGNORE INTO exception_queue(run_id,person_id,reasons,created_at) VALUES(?,?,?,?)",
                (run, pid, db.dump(reasons), db.now()),
            )
    return {"count": len(profiles)}


def route_matches(run):
    candidates = db.query("SELECT * FROM candidates WHERE run_id=?", (run,))
    a = [x for x in candidates if x["pool"] == "A"]
    b = [x for x in candidates if x["pool"] == "B"]
    held = {
        x["person_id"]
        for x in db.query(
            "SELECT person_id FROM exception_queue WHERE run_id=? AND status!='approved'",
            (run,),
        )
    }
    groups = {}
    for x in a:
        co = db.query(
            "SELECT * FROM companies WHERE run_id=? AND company_id=?",
            (run, x["company_id"]),
        )[0]
        for y in b:
            if x["person_id"] == y["person_id"]:
                continue
            score = compute_cofounder_match_score(x, y, co)
            mid = ident(run + ":" + x["person_id"] + ":" + y["person_id"])
            high = score["score"] >= 50
            jobs = json.loads(co["data"]).get("jobs", [])
            evidence = (
                "; ".join(
                    f"{j['title']} [citation #{j['citation']['id']} · {j['citation']['record_hash']}]"
                    for j in jobs
                )
                or "No indexed job gap available."
            )
            thesis = f"# Day-0 thesis: {vertical(json.loads(x['profile']))} infrastructure\n\n{x['name']} and {y['name']} can explore a product addressing operational bottlenecks signaled by indexed hiring: {evidence}\n\nValidate customer pain, interview operators, prototype a narrow workflow, and test willingness to pay. Indexed jobs are signals, not proof of unmet demand."
            safe = "Conditional draft: $250,000 post-money SAFE investment, subject to chemistry meeting, diligence, investment committee approval, mutually agreed valuation cap and definitive legal documents. No funding commitment."
            db.execute(
                "INSERT OR REPLACE INTO matches VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    mid,
                    run,
                    x["person_id"],
                    y["person_id"],
                    score["score"],
                    db.dump(score["breakdown"]),
                    "ONE_ON_ONE" if high else "WORK_PARTY",
                    thesis if high else "",
                    safe if high else "",
                ),
            )
            if x["person_id"] in held or y["person_id"] in held:
                continue
            if high:
                book_meeting(
                    run,
                    "one_on_one",
                    mid,
                    "Founder chemistry: " + x["name"] + " + " + y["name"],
                    [x["person_id"], y["person_id"]],
                    "Introductions; evidence review; founder chemistry; next steps.",
                )
            else:
                for p in (x, y):
                    profile = json.loads(p["profile"])
                    group = (city(profile), vertical(profile))
                    groups.setdefault(group, set()).add(p["person_id"])
    for (location, theme), attendees in groups.items():
        cohort = ident(run + location + theme)
        stations = []
        for co in db.query("SELECT * FROM companies WHERE run_id=?", (run,)):
            for j in json.loads(co["data"]).get("jobs", []):
                stations.append(
                    {
                        "company": co["name"],
                        "problem": j["title"],
                        "citation": j["citation"],
                    }
                )
        plan = {
            "venue": f"{location} — venue to be confirmed",
            "theme": theme,
            "stations": stations,
            "icebreakers": [
                "What problem would you work on for five years?",
                "What skill do you need in a co-founder?",
            ],
            "rsvp": "Sandbox invitation; venue and attendance require confirmation.",
        }
        people = sorted(attendees)
        meeting = book_meeting(
            run,
            "work_party",
            cohort,
            "Foundry-Zero Weekend Work-Party & Co-Founder Mixer",
            people,
            db.dump(plan),
        )
        db.execute(
            "INSERT OR REPLACE INTO work_party_cohorts VALUES(?,?,?,?,?,?,?)",
            (cohort, run, location, theme, db.dump(people), db.dump(plan), meeting),
        )
    return {"pairs": len(a) * len(b)}


def seed_bench(run):
    # Synthetic benchmarks are explicitly identified and never called verified profiles.
    from demo_data import COMPANIES, PEOPLE

    for cid, raw in COMPANIES.items():
        if db.query(
            "SELECT 1 FROM companies WHERE run_id=? AND company_id=?", (run, cid)
        ):
            continue
        funding = (raw.get("funding") or {}).get("last_fundraise_date")
        ref = db.cite(
            run,
            cid,
            "SYNTHETIC DEMO /company/enrich",
            "funding.last_fundraise_date",
            funding,
            raw,
        )
        data = {
            "funding_date": funding,
            "citations": {"funding.last_fundraise_date": ref},
            "jobs": [],
            "synthetic": True,
        }
        basic = raw["basic_info"]
        db.execute(
            "INSERT INTO companies VALUES(?,?,?,?,?)",
            (
                run,
                cid,
                basic["name"] + " (demo benchmark)",
                basic["primary_domain"],
                db.dump(data),
            ),
        )
    for p in PEOPLE:
        pid = p["person_id"]
        cid = str(
            p["experience"]["employment_details"]["current"][0]["crustdata_company_id"]
        )
        if db.query(
            "SELECT 1 FROM candidates WHERE run_id=? AND person_id=?", (run, pid)
        ):
            continue
        pool = "A" if pid in ("demo-a", "demo-d") else "B"
        paths = [
            "basic_profile.name",
            "basic_profile.current_title",
            "basic_profile.headline",
            "basic_profile.location",
            "experience.employment_details.current",
            "education.schools",
            "vertical",
        ]
        refs = cite_fields(run, pid, "SYNTHETIC DEMO /person/search", p, paths)
        db.execute(
            "INSERT OR IGNORE INTO candidates VALUES(?,?,?,?,?,?,?)",
            (
                run,
                pid,
                cid,
                p["basic_profile"].get("name") or "Missing identity",
                pool,
                db.dump(p),
                db.dump(refs),
            ),
        )
        if pid == "demo-d":
            db.execute(
                "INSERT OR IGNORE INTO exception_queue(run_id,person_id,reasons,created_at) VALUES(?,?,?,?)",
                (
                    run,
                    pid,
                    db.dump(
                        [
                            "Synthetic vendor discrepancy and missing identity: partner review required"
                        ]
                    ),
                    db.now(),
                ),
            )


def review(exception_id, approve):
    """Apply a pending partner decision and reroute affected candidates."""
    rows = db.query(
        "SELECT * FROM exception_queue WHERE id=? AND status='pending'", (exception_id,)
    )
    if not rows:
        return
    row = rows[0]
    db.execute(
        "UPDATE exception_queue SET status=? WHERE id=?",
        ("approved" if approve else "rejected", exception_id),
    )
    db.execute(
        "DELETE FROM checkpoints WHERE run_id=? AND stage='MATCH_AND_ROUTE'",
        (row["run_id"],),
    )
    try:
        pipeline(run_id=row["run_id"])
    except Exception:
        db.execute(
            "UPDATE exception_queue SET status='pending' WHERE id=?", (exception_id,)
        )
        raise


def pipeline(
    identifiers=None, mode="demo", run_id=None, simulate_crash_after_stage=None
):
    """Run discovery or resume the saved request without replaying completed stages."""
    db.init_db()
    if mode not in {"demo", "live"}:
        raise ValueError("Mode must be demo or live")
    if isinstance(identifiers, str):
        identifiers = [identifiers]
    run = run_id or str(uuid.uuid4())
    if run_id:
        rows = db.query("SELECT * FROM runs WHERE id=?", (run,))
        if not rows:
            raise ValueError("Unknown run")
        identifiers = json.loads(rows[0]["identifiers"])
        mode = rows[0]["mode"]
        db.execute("UPDATE runs SET status='running', error=NULL WHERE id=?", (run,))
    else:
        identifiers = [
            value.strip()
            for value in identifiers or []
            if isinstance(value, str) and value.strip()
        ]
        if not identifiers:
            raise ValueError("Enter at least one company name or domain")
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,NULL)",
            (run, db.dump(identifiers), mode, "running", db.now()),
        )
    client = CrustdataClient(run, mode)

    def stage(scope, name, action):
        return checkpointed(run, scope, name, action, simulate_crash_after_stage)

    try:
        company_ids = stage(
            "run",
            "IDENTIFY",
            lambda: discover_companies(run, identifiers, client, mode),
        )
        for cid in company_ids:
            stage(
                cid, "MARKET", lambda cid=cid: research_market(run, cid, client, mode)
            )
            stage(
                cid, "PEOPLE", lambda cid=cid: discover_people(run, cid, client, mode)
            )
        stage("run", "BENCH", lambda: seed_bench(run) or {"seeded": True})
        stage("run", "MATCH_AND_ROUTE", lambda: route_matches(run))
        db.execute("UPDATE runs SET status='completed', error=NULL WHERE id=?", (run,))
        return run
    except Exception as exc:
        db.execute(
            "UPDATE runs SET status='interrupted', error=? WHERE id=?", (str(exc), run)
        )
        raise
