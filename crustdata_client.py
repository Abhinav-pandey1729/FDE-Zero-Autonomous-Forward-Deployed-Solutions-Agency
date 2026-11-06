"""Cached Crustdata access with explicitly labeled offline fallback responses."""

import hashlib
import json
import os
import time

import httpx
from dotenv import load_dotenv

import db

load_dotenv()


from urllib.parse import urlparse


def sanitize_domain(val):
    """Normalize full URLs, protocol prefixes, paths, and query parameters to clean root domains."""
    if not val or not isinstance(val, str):
        return ""
    val = val.strip()
    if "://" in val or val.startswith("www."):
        if not val.startswith(("http://", "https://")):
            val = "https://" + val
        parsed = urlparse(val)
        host = parsed.netloc or parsed.path.split("/")[0]
        if host.startswith("www."):
            host = host[4:]
        return host.split(":")[0].lower()
    if "/" in val:
        val = val.split("/")[0]
    if "?" in val:
        val = val.split("?")[0]
    if "#" in val:
        val = val.split("#")[0]
    if val.startswith("www."):
        val = val[4:]
    return val.strip().lower()


def api_token():
    """Prefer Streamlit Cloud secrets while supporting local environment files."""
    try:
        import streamlit as st

        token = st.secrets.get("CRUSTDATA_API_KEY") or st.secrets.get(
            "CRUSTDATA_API_TOKEN"
        )
    except Exception:
        token = None
    return token or os.getenv("CRUSTDATA_API_KEY") or os.getenv("CRUSTDATA_API_TOKEN")


def valid_response(endpoint, data):
    """Reject malformed containers before they reach discovery code."""
    if endpoint in {"/company/identify", "/company/enrich", "/company/search"}:
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
        return fallback_response(endpoint, payload)

    def post(self, endpoint, payload):
        if endpoint == "/company/identify" and isinstance(payload, dict):
            payload = dict(payload)
            if "domains" in payload:
                payload["domains"] = [sanitize_domain(d) for d in payload["domains"] if sanitize_domain(d)]
            if "names" in payload:
                names = []
                for n in payload["names"]:
                    if isinstance(n, str) and ("://" in n or ("." in n and not n.endswith("."))):
                        sd = sanitize_domain(n)
                        payload.setdefault("domains", []).append(sd)
                    else:
                        names.append(n)
                if names:
                    payload["names"] = names
                elif "domains" in payload:
                    payload.pop("names", None)
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
                res = httpx.post(
                    "https://api.crustdata.com" + endpoint,
                    json=payload,
                    headers={
                        "Authorization": "Token " + self.token,
                        "x-api-version": "2025-11-01",
                    },
                    timeout=8,
                )
                if res.status_code == 429 and ("x-ratelimit-reset" in res.headers or "retry-after" in res.headers):
                    reset_header = res.headers.get("x-ratelimit-reset") or res.headers.get("retry-after")
                    try:
                        wait_seconds = min(float(reset_header), 2.0)
                    except ValueError:
                        wait_seconds = 0.05
                    db.audit(
                        self.run_id,
                        "RATE_LIMIT_429_BACKOFF",
                        {
                            "endpoint": endpoint,
                            "attempt": 1,
                            "wait_seconds": wait_seconds,
                            "x_ratelimit_reset": reset_header,
                        },
                    )
                    time.sleep(wait_seconds)
                    res = httpx.post(
                        "https://api.crustdata.com" + endpoint,
                        json=payload,
                        headers={
                            "Authorization": "Token " + self.token,
                            "x-api-version": "2025-11-01",
                        },
                        timeout=8,
                    )
                res.raise_for_status()
                data = res.json()
                if not valid_response(endpoint, data):
                    raise ValueError("Invalid API response shape")
            except (httpx.HTTPError, ValueError) as exc:
                return self.fallback(endpoint, payload, type(exc).__name__)

            if endpoint == "/company/identify" and not any(
                item.get("matches") for item in (data or [])
            ):
                query_name = (payload.get("names") or [""])[0]
                if query_name and "." not in query_name:
                    try:
                        search_res = self.post("/company/search", {"query": query_name})
                        if any(item.get("matches") for item in search_res):
                            return search_res
                    except Exception:
                        pass
                return self.fallback(endpoint, payload, "Company was not resolved")
        db.execute(
            "INSERT OR IGNORE INTO cache VALUES(?,?,?,?,?)",
            (namespace, key, body, db.dump(data), db.now()),
        )
        return data
