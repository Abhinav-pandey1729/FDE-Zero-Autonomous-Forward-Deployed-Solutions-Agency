"""Cached Crustdata access with explicitly labeled offline fallback responses."""

import hashlib
import json
import os

import httpx
from dotenv import load_dotenv

import db

load_dotenv()


def api_token():
    """Prefer Streamlit Cloud secrets while supporting local environment files."""
    try:
        import streamlit as st

        token = st.secrets.get("CRUSTDATA_API_KEY") or st.secrets.get(
            "CRUSTDATA_API_TOKEN"
        )
    except Exception:
        # Streamlit raises different errors for missing files and bare-mode access.
        token = None
    return token or os.getenv("CRUSTDATA_API_KEY") or os.getenv("CRUSTDATA_API_TOKEN")


def valid_response(endpoint, data):
    """Reject malformed containers before they reach discovery code."""
    if endpoint in {"/company/identify", "/company/enrich"}:
        return isinstance(data, list) and all(
            isinstance(item, dict)
            and isinstance(item.get("matches") or [], list)
            and all(
                isinstance(match, dict)
                and (
                    match.get("company_data") is None
                    or isinstance(match["company_data"], dict)
                )
                and (
                    not match.get("company_data")
                    or match["company_data"].get("basic_info") is None
                    or isinstance(match["company_data"]["basic_info"], dict)
                )
                for match in item.get("matches") or []
            )
            for item in data
        )
    key = "job_listings" if endpoint == "/job/search" else "profiles"
    return isinstance(data, dict) and isinstance(data.get(key) or [], list)


class CrustdataClient:
    def __init__(self, run_id=None, mode="live"):
        if mode not in {"live", "demo"}:
            raise ValueError("Mode must be demo or live")
        self.run_id = run_id
        self.mode = mode
        self.token = api_token() if mode == "live" else None
        self.last_source = "demo" if mode == "demo" else "live"

    def fallback(self, endpoint, payload, reason):
        from demo_data import fallback_response

        self.last_source = "fallback"
        db.audit(
            self.run_id,
            "RESILIENCE_FALLBACK_TRIGGERED",
            {
                "endpoint": endpoint,
                "reason": reason,
                "source": "synthetic fixture; not live Crustdata",
            },
        )
        # Fallback records never enter the live API cache.
        return fallback_response(endpoint, payload)

    def post(self, endpoint, payload):
        self.last_source = "demo" if self.mode == "demo" else "live"
        body = db.dump(payload)
        key = hashlib.sha256(body.encode()).hexdigest()
        namespace = endpoint if self.mode == "live" else "demo:" + endpoint
        rows = db.query(
            "SELECT response FROM cache WHERE endpoint=? AND payload_hash=?",
            (namespace, key),
        )
        if rows:
            try:
                data = json.loads(rows[0]["response"])
                if not valid_response(endpoint, data):
                    raise ValueError("Invalid cached response shape")
            except (ValueError, TypeError):
                db.execute(
                    "DELETE FROM cache WHERE endpoint=? AND payload_hash=?",
                    (namespace, key),
                )
            else:
                db.execute(
                    "INSERT INTO savings(run_id,source,endpoint,amount,created_at) VALUES(?,?,?,?,?)",
                    (self.run_id, "cache", namespace, 0, db.now()),
                )
                return data
        if self.mode == "demo":
            from demo_data import response

            data = response(endpoint, payload)
        else:
            if not self.token:
                return self.fallback(endpoint, payload, "API key unavailable")
            try:
                response = httpx.post(
                    "https://api.crustdata.com" + endpoint,
                    json=payload,
                    headers={
                        "Authorization": "Token " + self.token,
                        "x-api-version": "2025-11-01",
                    },
                    timeout=5,
                )
                response.raise_for_status()
                data = response.json()
                if not valid_response(endpoint, data):
                    raise ValueError("Invalid API response shape")
            except (httpx.HTTPError, ValueError) as exc:
                return self.fallback(endpoint, payload, type(exc).__name__)
            if endpoint == "/company/identify" and not any(
                item.get("matches") for item in data
            ):
                return self.fallback(endpoint, payload, "Company was not resolved")
        db.execute(
            "INSERT OR IGNORE INTO cache VALUES(?,?,?,?,?)",
            (namespace, key, body, db.dump(data), db.now()),
        )
        return data
