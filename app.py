import json

import streamlit as st

import db
from bootstrap import DEFAULT_COMPANIES, initialize_app
from scheduler_service import (
    IST,
    dispatch_cohort_invites,
    parse_request,
    parse_time,
    reschedule_meeting,
    send_reminders,
)
from service_engine import pipeline, review

st.set_page_config(page_title="Foundry-Zero", page_icon="â—ˆ", layout="wide")
try:
    initialize_app()
except (OSError, RuntimeError) as exc:
    st.error("Unable to initialize the application: " + str(exc))
    st.stop()
with st.sidebar:
    st.markdown("## â—ˆ FOUNDRY-ZERO")
    st.caption("AUTONOMOUS VENTURE STUDIO")
    mode = st.selectbox("Data source", ["Demo dataset", "Live Crustdata"])
    identifiers = st.text_input(
        "Companies or domains", value="razorpay.com, freshworks.com, Razorpay"
    )
    crash = st.checkbox("Simulate crash after market research")
    if st.button("Run venture discovery", type="primary", width="stretch"):
        try:
            with st.spinner("Researching and building founder pathways…"):
                run = pipeline(
                    [x.strip() for x in identifiers.split(",") if x.strip()],
                    mode="demo" if mode.startswith("Demo") else "live",
                    simulate_crash_after_stage="MARKET" if crash else None,
                )
            st.session_state["selected_run"] = run
            st.success("Run complete")
        except Exception as e:
            latest = db.query("SELECT id FROM runs ORDER BY created_at DESC LIMIT 1")
            if latest:
                st.session_state["selected_run"] = latest[0]["id"]
            st.error(str(e))
    interrupted = db.query(
        "SELECT id FROM runs WHERE status='interrupted' ORDER BY created_at DESC LIMIT 1"
    )
    if st.button("Resume interrupted run", disabled=not interrupted, width="stretch"):
        try:
            st.session_state["selected_run"] = pipeline(run_id=interrupted[0]["id"])
            st.rerun()
        except Exception as e:
            st.error(str(e))
st.title("Foundry-Zero: Autonomous Pre-Day-0 Venture Studio & Co-Founder Matchmaker")
st.caption("Evidence-led founder matching and event coordination in a local sandbox.")
st.subheader("Judge's quick-tour & live stress-test")
q1, q2, q3 = st.columns(3)
if q1.button("Run default flagship batch"):
    try:
        st.session_state["selected_run"] = pipeline(DEFAULT_COMPANIES)
        st.rerun()
    except Exception as exc:
        st.error(str(exc))
if q2.button("Simulate stage-3 crash & resume"):
    try:
        try:
            pipeline(
                DEFAULT_COMPANIES,
                simulate_crash_after_stage="PEOPLE",
            )
        except RuntimeError as exc:
            if "Simulated crash" not in str(exc):
                raise
        crashed = db.query(
            "SELECT id FROM runs WHERE status='interrupted' ORDER BY created_at DESC LIMIT 1"
        )[0]["id"]
        st.session_state["selected_run"] = pipeline(run_id=crashed)
        st.success("Checkpoint recovery complete; completed stages reused.")
    except Exception as exc:
        st.error(str(exc))
if q3.button("Verify SHA-256 citation chain"):
    result = db.verify_ledger()
    if result["ok"]:
        st.success(f"Verified {result['count']} records · head {result['head']}")
    else:
        st.error(f"Ledger failed at record {result['failed_id']}")
runs = db.query("SELECT * FROM runs ORDER BY created_at DESC")
if not runs:
    st.info(
        "Start with the demo dataset in the sidebar, or connect Crustdata for live research."
    )
    st.stop()
ids = [x["id"] for x in runs]
run = st.selectbox(
    "Run",
    ids,
    key="selected_run",
    index=ids.index(st.session_state["selected_run"])
    if st.session_state.get("selected_run") in ids
    else 0,
    format_func=lambda x: (
        x[:8]
        + " · "
        + next(r["mode"] + " · " + r["status"] for r in runs if r["id"] == x)
    ),
)
current = next(x for x in runs if x["id"] == run)
if current["mode"] == "demo" or db.query(
    "SELECT id FROM audit WHERE run_id=? AND event='RESILIENCE_FALLBACK_TRIGGERED'",
    (run,),
):
    st.warning(
        "Demo or resilience fallback is active. Synthetic records are examples, not live Crustdata evidence; check citation sources."
    )
if current["error"]:
    st.error(current["error"])
st.caption(
    "Synthetic standing benchmarks may appear alongside live results; check each citation's source. Fallback and demonstration records are not verified people or live market evidence."
)


def count(sql):
    return db.query(sql, (run,))[0]["n"]


ledger = db.verify_ledger()
metrics = [
    ("Founders evaluated", count("SELECT count(*) n FROM candidates WHERE run_id=?")),
    (
        "≥50% pairs",
        count("SELECT count(*) n FROM matches WHERE run_id=? AND pathway='ONE_ON_ONE'"),
    ),
    (
        "<50% cohort pool",
        count(
            "SELECT count(DISTINCT value) n FROM work_party_cohorts,json_each(attendees) WHERE run_id=?"
        ),
    ),
    (
        "Meetings booked / rescheduled",
        count("SELECT count(*) n FROM meetings WHERE run_id=?"),
    ),
    (
        "Verified citations",
        ledger["count"] if ledger["ok"] else 0,
    ),
    (
        "Cache / checkpoint reuse",
        count("SELECT count(*) n FROM savings WHERE run_id=?"),
    ),
]
for col, (label, value) in zip(st.columns(6), metrics, strict=True):
    col.metric(label, value, border=True)
st.caption(
    "Reuse counts represent avoided calls or stage replays; unknown credit prices are not estimated. Citation chains require an externally saved head to detect complete database rewrites."
)
tabs = st.tabs(
    [
        "Execution & matching",
        "1-on-1 thesis & SAFE",
        "Work-party mixer",
        "Scheduler & notifications",
        "Exceptions & ledger",
    ]
)
people = {
    x["person_id"]: x
    for x in db.query("SELECT * FROM candidates WHERE run_id=?", (run,))
}
matches = db.query("SELECT * FROM matches WHERE run_id=? ORDER BY score DESC", (run,))
with tabs[0]:
    st.subheader("Live execution DAG")
    for co in db.query("SELECT * FROM companies WHERE run_id=?", (run,)):
        st.write(f"**{co['name']}** · canonical ID {co['company_id']}")
        st.caption(
            " → ".join(
                f"{'✓' if db.load(run, scope, stage) is not None else '○'} {stage}"
                for scope, stage in [
                    ("run", "IDENTIFY"),
                    (co["company_id"], "MARKET"),
                    (co["company_id"], "PEOPLE"),
                    ("run", "MATCH_AND_ROUTE"),
                ]
            )
        )
    rows = []
    for m in matches:
        parts = json.loads(m["breakdown"])
        rows.append(
            {
                "Founder A": people[m["founder_a"]]["name"],
                "Founder B": people[m["founder_b"]]["name"],
                **{k: v["score"] for k, v in parts.items()},
                "Total %": m["score"],
                "Routing": "PATHWAY 1: ≥50% 1-ON-1 THESIS"
                if m["pathway"] == "ONE_ON_ONE"
                else "PATHWAY 2: <50% WORK-PARTY MIXER",
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")
    for m in matches:
        with st.expander(
            f"Evidence · {people[m['founder_a']]['name']} + {people[m['founder_b']]['name']} · {m['score']}%"
        ):
            for label, part in json.loads(m["breakdown"]).items():
                st.progress(
                    part["score"] / 25,
                    text=f"{label}: {part['score']}/25 · {part['reason']}",
                )
                st.caption(
                    "Citations: "
                    + ", ".join(
                        f"#{x['id']} {x['field']} · {x['record_hash']}"
                        for x in part["citations"]
                    )
                )
with tabs[1]:
    st.subheader("1-on-1 startup thesis & conditional SAFE")
    high = [m for m in matches if m["pathway"] == "ONE_ON_ONE"]
    if not high:
        st.info("No qualifying pairs yet.")
    for m in high:
        with st.container(border=True):
            st.markdown(
                f"### {people[m['founder_a']]['name']} × {people[m['founder_b']]['name']} · {m['score']}%"
            )
            st.markdown(m["thesis"])
            st.markdown("**Conditional $250,000 post-money SAFE**")
            st.info(m["safe"])
            st.caption(
                "Draft for discussion; meeting completion alone does not create a funding commitment."
            )
with tabs[2]:
    st.subheader("Weekend work-party & co-founder mixer")
    cohorts = db.query("SELECT * FROM work_party_cohorts WHERE run_id=?", (run,))
    if not cohorts:
        st.info("No eligible cohorts yet; held profiles are excluded from invitations.")
    for co in cohorts:
        plan = json.loads(co["plan"])
        st.markdown(f"### {co['city']} · {co['theme']} · {co['id']}")
        st.caption(
            "Saturday six-hour sprint · 10:00–16:00 IST: introductions, discovery, build, demo and reflection."
        )
        st.write(plan["venue"])
        event = db.query(
            "SELECT starts_at, ends_at FROM meetings WHERE id=?", (co["meeting_id"],)
        )[0]
        st.caption(
            "Schedule (IST): "
            + parse_time(event["starts_at"]).astimezone(IST).strftime("%A %d %b, %H:%M")
            + " to "
            + parse_time(event["ends_at"]).astimezone(IST).strftime("%H:%M")
        )
        st.write(
            "Attendees: "
            + ", ".join(
                people[p]["name"] for p in json.loads(co["attendees"]) if p in people
            )
        )
        st.dataframe(plan["stations"], width="stretch")
        st.write("Icebreakers: " + " / ".join(plan["icebreakers"]))
        st.caption(plan["rsvp"])
        for pid in json.loads(co["attendees"]):
            relevant = [
                m
                for m in matches
                if m["score"] < 50 and pid in (m["founder_a"], m["founder_b"])
            ]
            reason = (
                f"{min(m['score'] for m in relevant)}% direct match; explore fit and location friction"
                if relevant
                else "Curated mixer participant"
            )
            st.write(
                f"**{people.get(pid, {}).get('name', pid)}** · {reason}. Icebreaker: What problem would you build for five years?"
            )
        if st.button(
            "Dispatch cohort work-party invites & RSVP links", key="cohort" + co["id"]
        ):
            n = dispatch_cohort_invites(co["meeting_id"])
            st.success(
                f"{n} sandbox RSVP invitations queued."
                if n
                else "Idempotent lock active: no duplicate invites."
            )
with tabs[3]:
    st.subheader("Meeting scheduler & sandbox notifications")
    for m in db.query(
        "SELECT * FROM meetings WHERE run_id=? ORDER BY starts_at", (run,)
    ):
        with st.container(border=True):
            st.markdown(f"**{m['title']}** · {m['status']} · version {m['version']}")
            st.write(
                f"{m['starts_at']} → {m['ends_at']} · "
                + ", ".join(
                    people[p]["name"] if p in people else p
                    for p in json.loads(m["participants"])
                )
            )
            a, b = st.columns(2)
            a.download_button(
                "Download .ics calendar invite",
                m["ics"],
                file_name=m["id"] + ".ics",
                mime="text/calendar",
                key="ics" + m["id"],
            )
            if b.button("Simulate founder reschedule request", key="res" + m["id"]):
                proposed = parse_request("Sunday 4:00 PM IST")
                reschedule_meeting(m["id"], proposed)
                st.rerun()
            request = st.text_input(
                "Reschedule request (weekday and time in IST)",
                key="request" + m["id"],
                placeholder="Move to Sunday 4:00 PM IST",
            )
            if st.button("Apply reschedule request", key="apply" + m["id"]):
                try:
                    reschedule_meeting(
                        m["id"],
                        parse_request(request),
                        request_id=f"{m['version']}:{request.strip().lower()}",
                    )
                    st.rerun()
                except ValueError as exc:
                    st.warning(str(exc))
    if st.button("Send T-24h / T-1h reminders"):
        n = send_reminders(force=True, run_id=run)
        if n:
            st.success(f"{n} sandbox reminders queued.")
        else:
            st.warning(
                "IDEMPOTENT LOCK ACTIVE: Reminder already dispatched for this meeting version (0 duplicate messages sent)"
            )
    st.dataframe(
        db.query(
            "SELECT created_at,meeting_id,recipient,kind,idempotency_key,payload FROM notifications_outbox WHERE run_id=? ORDER BY id DESC",
            (run,),
        ),
        width="stretch",
        hide_index=True,
    )
with tabs[4]:
    st.subheader("Human exception queue")
    for e in db.query(
        "SELECT * FROM exception_queue WHERE run_id=? AND status='pending'", (run,)
    ):
        st.warning(
            f"{people.get(e['person_id'], {}).get('name', e['person_id'])}: {e['reasons']}"
        )
        a, b = st.columns(2)
        if a.button("Partner approve", key="a" + str(e["id"])):
            review(e["id"], True)
            st.rerun()
        if b.button("Reject", key="b" + str(e["id"])):
            review(e["id"], False)
            st.rerun()
    if st.button("Verify cryptographic ledger integrity"):
        result = db.verify_ledger()
        if result["ok"]:
            st.success(f"Verified {result['count']} records · head {result['head']}")
        else:
            st.error(f"Ledger failed at record {result['failed_id']}")
    st.dataframe(
        db.query(
            "SELECT id,entity_id,endpoint,field,value,record_hash FROM citations WHERE run_id=? ORDER BY id DESC",
            (run,),
        ),
        width="stretch",
        hide_index=True,
    )
