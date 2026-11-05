"""Behavior regressions for scheduling, fallback provenance, and run selection."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

import db
from crustdata_client import CrustdataClient
from scheduler_service import (
    IST,
    book_meeting,
    next_slot,
    parse_request,
    reschedule_meeting,
)


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = db.DB
        db.DB = str(Path(self.tmp.name) / "regression.db")
        db.init_db()

    def tearDown(self):
        db.DB = self.original_db
        self.tmp.cleanup()

    def test_run_scoping_and_collision_negotiation(self):
        proposed = (datetime.now(IST) + timedelta(days=2)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        first = book_meeting(
            "run-a", "one_on_one", "a", "First", ["founder"], "Chat", proposed
        )
        independent = book_meeting(
            "run-b", "one_on_one", "b", "Independent", ["founder"], "Chat", proposed
        )
        second = book_meeting(
            "run-a", "one_on_one", "c", "Second", ["founder"], "Chat", proposed
        )
        rows = {m["id"]: m for m in db.query("SELECT * FROM meetings")}
        self.assertEqual(rows[first]["starts_at"], rows[independent]["starts_at"])
        self.assertGreaterEqual(rows[second]["starts_at"], rows[first]["ends_at"])
        moved = reschedule_meeting(second, proposed, "collision-request")
        self.assertGreaterEqual(moved["starts_at"], rows[first]["ends_at"])

    def test_invalid_duration_and_naive_date(self):
        with db.connect() as conn:
            for duration in (timedelta(0), timedelta(hours=-1), timedelta(hours=10)):
                with self.assertRaises(ValueError):
                    next_slot(conn, ["founder"], datetime.now(IST), duration)
        with self.assertRaises(ValueError):
            book_meeting(
                "run", "one_on_one", "a", "Title", ["founder"], "", datetime(2030, 1, 1)
            )
        self.assertEqual(db.query("SELECT count(*) n FROM meetings")[0]["n"], 0)

    def test_same_day_future_time_parser(self):
        now = datetime(2030, 1, 6, 10, tzinfo=IST)  # Sunday
        self.assertEqual(parse_request("Sunday 4 PM IST", now).date(), now.date())
        self.assertEqual(
            parse_request("Sunday 9 AM IST", now).date(),
            (now + timedelta(days=7)).date(),
        )

    def test_malformed_api_and_http_errors_fall_back_without_caching(self):
        client = CrustdataClient("test", mode="demo")
        client.mode = "live"
        client.token = "test-token"
        request = httpx.Request("POST", "https://api.crustdata.com/company/identify")
        for status, data in [
            (200, {"bad": "shape"}),
            (200, [{"matches": [{"company_data": "invalid"}]}]),
            (429, {}),
            (503, {}),
        ]:
            with self.subTest(status=status, data=data):
                response = httpx.Response(status, json=data, request=request)
                with patch("httpx.post", return_value=response):
                    result = client.post("/company/identify", {"names": ["Unseen"]})
                self.assertTrue(
                    result[0]["matches"][0]["company_data"]["synthetic_fallback"]
                )
                self.assertEqual(client.last_source, "fallback")
        self.assertFalse(db.query("SELECT * FROM cache"))
        self.assertEqual(len(db.query("SELECT * FROM audit")), 4)

    def test_corrupt_cache_is_replaced(self):
        from hashlib import sha256

        payload = {"names": ["Razorpay"]}
        body = db.dump(payload)
        db.execute(
            "INSERT INTO cache VALUES(?,?,?,?,?)",
            (
                "demo:/company/identify",
                sha256(body.encode()).hexdigest(),
                body,
                "broken json",
                db.now(),
            ),
        )
        result = CrustdataClient("test", mode="demo").post("/company/identify", payload)
        self.assertEqual(
            result[0]["matches"][0]["company_data"]["crustdata_company_id"], 897127
        )
        self.assertIsInstance(
            json.loads(db.query("SELECT response FROM cache")[0]["response"]), list
        )

    def test_cold_start_refreshes_expired_seed(self):
        import os

        from bootstrap import initialize_app
        from scheduler_service import parse_time

        os.unlink(db.DB)

        class FutureDate(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2040, 1, 1, tzinfo=IST).astimezone(tz)

        with patch("bootstrap.datetime", FutureDate):
            initialize_app()
        for meeting in db.query("SELECT * FROM meetings"):
            start = parse_time(meeting["starts_at"]).astimezone(IST)
            self.assertGreaterEqual(start.year, 2040)
            self.assertEqual(meeting["status"], "RESCHEDULED_CONFIRMED")
            if meeting["kind"] == "work_party":
                self.assertEqual(start.weekday(), 5)
        self.assertTrue(db.verify_ledger()["ok"])
