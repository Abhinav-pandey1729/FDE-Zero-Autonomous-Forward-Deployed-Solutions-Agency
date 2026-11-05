"""Application startup and restoration of the bundled demonstration."""

from datetime import datetime, timedelta

import db
from scheduler_service import IST, parse_time, reschedule_meeting
from service_engine import pipeline

DEFAULT_COMPANIES = ["razorpay.com", "freshworks.com", "Razorpay"]


def initialize_app():
    """Restore an empty container and move expired fixture events into the future."""
    hydrated = db.hydrate_flagship_if_missing()
    db.init_db()
    if hydrated:
        tomorrow = (datetime.now(IST) + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        for meeting in db.query("SELECT * FROM meetings ORDER BY starts_at"):
            if parse_time(meeting["starts_at"]) >= tomorrow:
                continue
            proposed = tomorrow
            if meeting["kind"] == "work_party":
                proposed += timedelta(days=(5 - proposed.weekday()) % 7)
            reschedule_meeting(
                meeting["id"], proposed, request_id="restore-expired-fixture"
            )
    if not db.query("SELECT id FROM runs WHERE status='completed' LIMIT 1"):
        return pipeline(DEFAULT_COMPANIES, mode="demo")
    return None
