# Clearly labeled synthetic demonstration records; never represented as live Crustdata.
COMPANIES = {
    "897127": {
        "crustdata_company_id": 897127,
        "basic_info": {"name": "Razorpay", "primary_domain": "razorpay.com"},
        "funding": {"last_fundraise_date": "2021-12-01"},
    },
    "6042422": {
        "crustdata_company_id": 6042422,
        "basic_info": {"name": "Freshworks", "primary_domain": "freshworks.com"},
        "funding": {"last_fundraise_date": "2021-01-01"},
    },
}


def person(pid, name, title, headline, city, school, cid, vertical):
    return {
        "person_id": pid,
        "basic_profile": {
            "name": name,
            "current_title": title,
            "headline": headline,
            "location": city,
        },
        "experience": {
            "employment_details": {
                "current": [
                    {
                        "crustdata_company_id": int(cid),
                        "title": title,
                        "start_date": "2018-01-01",
                    }
                ]
            }
        },
        "education": {"schools": [{"school": school}]},
        "vertical": vertical,
        "social_handles": {
            "professional_network_identifier": {
                "profile_url": "https://example.invalid/" + pid
            }
        },
    }


PEOPLE = [
    person(
        "demo-a",
        "Asha Rao",
        "VP Engineering",
        "VP Engineering at Razorpay",
        "Bengaluru",
        "IIT Kanpur",
        "897127",
        "Fintech",
    ),
    person(
        "demo-b",
        "Arjun Sen",
        "Product Leader",
        "Ex-Razorpay | Building fintech",
        "Bengaluru",
        "IIT Kanpur",
        "897127",
        "Fintech",
    ),
    person(
        "demo-c",
        "Maya Lee",
        "GTM Leader",
        "Former Freshworks | Stealth",
        "San Mateo",
        "Stanford",
        "6042422",
        "B2B SaaS",
    ),
    person(
        "demo-d",
        "",
        "Staff Engineer",
        "Staff Engineer",
        "NCR",
        "IIT Delhi",
        "6042422",
        "B2B SaaS",
    ),
]


def response(endpoint, payload):
    if endpoint == "/company/identify":
        val = (payload.get("domains") or payload.get("names") or [""])[0].lower()
        co = next(
            (
                x
                for x in COMPANIES.values()
                if val
                in (
                    x["basic_info"]["name"].lower(),
                    x["basic_info"]["primary_domain"],
                    str(x["crustdata_company_id"]),
                )
            ),
            None,
        )
        return (
            [{"matches": [{"confidence_score": 1, "company_data": co}]}]
            if co
            else fallback_response(endpoint, payload)
        )
    if endpoint == "/company/enrich":
        co = COMPANIES.get(str(payload["crustdata_company_ids"][0]))
        return (
            [{"matches": [{"company_data": co}]}]
            if co
            else fallback_response(endpoint, payload)
        )
    if endpoint == "/job/search":
        cid = str(payload.get("demo_company_id", "897127"))
        if cid not in COMPANIES:
            return {"job_listings": []}
        title = (
            "Lead Security Engineer"
            if cid == "897127"
            else "Staff Engineer - Machine Learning"
        )
        return {
            "job_listings": [
                {
                    "job_details": {
                        "title": title,
                        "url": "https://example.invalid/job/" + cid,
                    },
                    "company": {"basic_info": COMPANIES[cid]["basic_info"]},
                    "location": {"raw": "Bengaluru"},
                }
            ]
            + (
                [
                    {
                        "job_details": {
                            "title": "Staff Engineer - Payment Risk",
                            "url": "https://example.invalid/job/risk",
                        },
                        "company": {"basic_info": COMPANIES[cid]["basic_info"]},
                        "location": {"raw": "Bengaluru"},
                    }
                ]
                if cid == "897127"
                else []
            )
        }
    if endpoint == "/person/search":
        cid = str(payload.get("demo_company_id", "897127"))
        return {
            "profiles": [
                p
                for p in PEOPLE
                if str(
                    p["experience"]["employment_details"]["current"][0][
                        "crustdata_company_id"
                    ]
                )
                == cid
            ]
        }
    if endpoint == "/person/enrich":
        pid = payload.get("person_id")
        return {"profiles": [p for p in PEOPLE if p["person_id"] == pid]}
    return {}


def fallback_response(endpoint, payload):
    # Unseen entities are explicitly synthetic and carry no asserted employment,
    # funding, or hiring facts. The standing demo bench supplies examples.
    if endpoint == "/company/identify":
        import hashlib

        value = (payload.get("domains") or payload.get("names") or ["Unknown"])[0]
        cid = int(hashlib.sha256(value.encode()).hexdigest()[:12], 16)
        co = {
            "crustdata_company_id": cid,
            "basic_info": {
                "name": value + " (unverified fallback)",
                "primary_domain": value if "." in value else "",
            },
            "synthetic_fallback": True,
        }
        return [{"matches": [{"confidence_score": 0, "company_data": co}]}]
    if endpoint == "/company/enrich":
        return [{"matches": []}]
    if endpoint == "/job/search":
        return {"job_listings": []}
    if endpoint == "/person/search":
        return {"profiles": []}
    return {}
