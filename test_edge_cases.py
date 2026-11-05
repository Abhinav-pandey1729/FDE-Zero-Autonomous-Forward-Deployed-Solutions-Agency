import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import db
from scheduler_service import IST, parse_request, reschedule_meeting
from service_engine import pipeline


class EdgeCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = db.DB
        db.DB = str(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        db.DB = self.old
        self.tmp.cleanup()

    def test_snapshot(self):
        t = time.perf_counter()
        self.assertTrue(db.hydrate_flagship_if_missing())
        elapsed = time.perf_counter() - t
        self.assertTrue(db.query("SELECT id FROM runs WHERE status='completed'"))
        self.assertTrue(db.verify_ledger()["ok"])
        print(f"  snapshot hydration {elapsed * 1000:.1f} ms")

    def test_unseen_and_offline(self):
        db.init_db()
        with patch("crustdata_client.api_token", return_value=None):
            run = pipeline(
                ["match cofounders from physicswallah and zerodha"], mode="live"
            )
        self.assertTrue(
            db.query(
                "SELECT * FROM audit WHERE run_id=? AND event='RESILIENCE_FALLBACK_TRIGGERED'",
                (run,),
            )
        )
        self.assertTrue(
            db.query("SELECT * FROM matches WHERE run_id=? AND score>=50", (run,))
        )
        self.assertTrue(
            db.query("SELECT * FROM matches WHERE run_id=? AND score<50", (run,))
        )
        self.assertTrue(
            db.query("SELECT * FROM work_party_cohorts WHERE run_id=?", (run,))
        )

    def test_ist(self):
        db.hydrate_flagship_if_missing()
        row = db.query("SELECT * FROM meetings WHERE kind='one_on_one' LIMIT 1")[0]
        result = reschedule_meeting(
            row["id"], parse_request("Sunday 4:00 PM IST"), "test"
        )
        start = datetime.fromisoformat(result["starts_at"]).astimezone(IST)
        end = datetime.fromisoformat(result["ends_at"]).astimezone(IST)
        self.assertGreaterEqual(start.hour, 10)
        self.assertLessEqual(end.hour, 19)
        self.assertEqual(result["status"], "RESCHEDULED_CONFIRMED")

    def test_messy_natural_language_and_alias_normalization(self):
        from service_engine import extract_entities_and_intents, pipeline
        # 1. Natural language sentence with conversational noise
        prompt = "We want to synthesize AI fintech founders from Zepto, Groww, and Razorpay in Bengaluru — check their claims and book their intros"
        entities = extract_entities_and_intents(prompt)
        self.assertTrue(any("zepto" in e.lower() for e in entities))
        self.assertTrue(any("groww" in e.lower() for e in entities))
        self.assertTrue(any("razorpay" in e.lower() for e in entities))

        # 2. Duplicate aliases collapsing
        aliases_prompt = "razorpay.com, Razorpay, RAZORPAY SOFTWARE"
        norm = extract_entities_and_intents(aliases_prompt)
        self.assertEqual(len(norm), 1)

        # 3. Dual-pathway matrix guarantee on arbitrary unseen request
        db.init_db()
        run = pipeline(prompt, mode="demo")
        matches = db.query("SELECT * FROM matches WHERE run_id=?", (run,))
        self.assertTrue(any(m["score"] >= 50 for m in matches))
        self.assertTrue(any(m["score"] < 50 for m in matches))

        # 4. Verify inline citation badges exist on all 4 pillars
        high_m = next(m for m in matches if m["score"] >= 50)
        bd = json.loads(high_m["breakdown"])
        for pillar in ("Pedigree", "City", "Domain", "Career"):
            self.assertIn("badges", bd[pillar])
            self.assertTrue(len(bd[pillar]["badges"]) > 0)
            badge_str = " ".join(bd[pillar]["badges"])
            self.assertTrue("POST /" in badge_str or "[UNVERIFIED:" in badge_str)

    def test_exception_queue_immediate_downstream_execution_and_quarantine(self):
        from service_engine import pipeline, review
        db.init_db()
        run = pipeline(["razorpay.com"], mode="demo")
        
        # Exception should be present for demo-d
        pending = db.query("SELECT * FROM exception_queue WHERE run_id=? AND status='pending'", (run,))
        self.assertTrue(pending)
        exc_id = pending[0]["id"]
        pid = pending[0]["person_id"]

        # Approve and check immediate downstream execution
        res = review(exc_id, approve=True)
        self.assertEqual(res["status"], "approved")
        self.assertEqual(db.query("SELECT status FROM exception_queue WHERE id=?", (exc_id,))[0]["status"], "approved")
        
        # Downstream matching must have re-executed
        matches = db.query("SELECT * FROM matches WHERE run_id=? AND (founder_a=? OR founder_b=?)", (run, pid, pid))
        self.assertTrue(matches)

        # Now test rejection/quarantine on a separate candidate
        db.execute("INSERT INTO exception_queue(run_id,person_id,reasons,created_at,status) VALUES(?,?,'Test hold',?, 'pending')", (run, "test-held", db.now()))
        held_id = db.query("SELECT id FROM exception_queue WHERE person_id='test-held'")[0]["id"]
        q_res = review(held_id, approve=False)
        self.assertEqual(q_res["status"], "quarantined")
        self.assertTrue(q_res["sha256"])
        audit_q = db.query("SELECT * FROM audit WHERE run_id=? AND event='EXCEPTION_REJECTED_QUARANTINE'", (run,))
        self.assertTrue(audit_q)
        # Verify 0 invites in outbox for quarantined person
        self.assertEqual(len(db.query("SELECT * FROM notifications_outbox WHERE run_id=? AND recipient='test-held'", (run,))), 0)

    def test_rescheduler_collision_avoidance_and_receipt(self):
        from scheduler_service import book_meeting, next_slot, reschedule_meeting, parse_time, IST
        from datetime import datetime, timedelta
        db.init_db()
        
        slot_time = (datetime.now(IST) + timedelta(days=3)).replace(hour=11, minute=0, second=0, microsecond=0)
        m1 = book_meeting("run-col", "one_on_one", "m1", "First Meeting", ["founder-x"], "Chat", slot_time)
        m2 = book_meeting("run-col", "one_on_one", "m2", "Second Meeting", ["founder-x"], "Chat", slot_time + timedelta(hours=2))

        # Attempt to reschedule m2 into m1's slot -> collision avoidance triggered!
        moved = reschedule_meeting(m2, slot_time, request_id="col-req-1")
        self.assertTrue(moved["collision_avoided"])
        self.assertIn("COLLISION AVOIDED", moved["collision_note"])
        self.assertGreaterEqual(parse_time(moved["starts_at"]), parse_time(db.query("SELECT ends_at FROM meetings WHERE id=?", (m1,))[0]["ends_at"]))

        # Check receipt in outbox
        receipt = db.query("SELECT payload FROM notifications_outbox WHERE meeting_id=? AND kind='RESCHEDULE_RECEIPT'", (m2,))
        self.assertTrue(receipt)
        payload = json.loads(receipt[0]["payload"])
        self.assertTrue(payload["collision_avoided"])
        self.assertIn("COLLISION AVOIDED", payload["collision_note"])

    def test_resilience_simulation_telemetry(self):
        from service_engine import simulate_resilience_test
        db.init_db()
        results = simulate_resilience_test("resilience-unit-test")
        self.assertEqual(results["rate_limit_test"]["status"], "PASSED")
        self.assertIn("x-ratelimit-reset", results["rate_limit_test"]["header_detected"])
        self.assertEqual(results["malformed_schema_test"]["status"], "PASSED")
        self.assertIn("[UNVERIFIED:", results["malformed_schema_test"]["badge_applied"])
        self.assertEqual(results["crash_resume_test"]["status"], "PASSED")
        self.assertGreater(results["crash_resume_test"]["stages_reused_from_sqlite"], 0)
        self.assertEqual(results["crash_resume_test"]["credits_rebilled"], 0)
        self.assertEqual(results["crash_resume_test"]["duplicate_outbox_dispatches"], 0)
