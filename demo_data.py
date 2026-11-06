# Clearly labeled synthetic demonstration records; never represented as live Crustdata.

BENCHMARK_COMPANIES = {
    "897127": {
        "crustdata_company_id": 897127,
        "basic_info": {
            "name": "Razorpay",
            "primary_domain": "razorpay.com",
            "employee_count": 4027,
        },
        "funding": {
            "last_fundraise_date": "2021-12-01",
            "total_funding_usd": "$816.6M",
            "last_fundraise_amount": "$375M",
        },
        "headcount": {"total_headcount": 4027, "headcount": 4027},
    },
    "6042422": {
        "crustdata_company_id": 6042422,
        "basic_info": {
            "name": "Freshworks",
            "primary_domain": "freshworks.com",
            "employee_count": 5200,
        },
        "funding": {
            "last_fundraise_date": "2021-01-01",
            "total_funding_usd": "$484M",
            "last_fundraise_amount": "$250M",
        },
        "headcount": {"total_headcount": 5200, "headcount": 5200},
    },
}

ALL_DEMO_COMPANIES = {
    **BENCHMARK_COMPANIES,
    "6162764": {
        "crustdata_company_id": 6162764,
        "basic_info": {
            "name": "Zomato",
            "primary_domain": "zomato.com",
            "employee_count": 3440,
        },
        "funding": {
            "last_fundraise_date": "2021-07-23",
            "total_funding_usd": "$2.1B (IPO)",
            "last_fundraise_amount": "$1.3B",
        },
        "headcount": {"total_headcount": 3440, "headcount": 3440},
    },
    "5702": {
        "crustdata_company_id": 5702,
        "basic_info": {
            "name": "Swiggy",
            "primary_domain": "swiggy.com",
            "employee_count": 6000,
        },
        "funding": {
            "last_fundraise_date": "2022-01-24",
            "total_funding_usd": "$3.6B",
            "last_fundraise_amount": "$700M",
        },
        "headcount": {"total_headcount": 6000, "headcount": 6000},
    },
}

# Preserve backwards compatibility for benchmark tests
COMPANIES = BENCHMARK_COMPANIES


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
                        "crustdata_company_id": int(cid) if str(cid).isdigit() else cid,
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


BENCHMARK_PEOPLE = [
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

ALL_DEMO_PEOPLE = [
    *BENCHMARK_PEOPLE,
    person(
        "zomato-eng",
        "Abhishek Sagar",
        "VP Engineering",
        "VP Engineering at Zomato | Ex-Blinkit",
        "Bengaluru",
        "IIT Delhi",
        "6162764",
        "FoodTech & Quick Commerce",
    ),
    person(
        "zomato-prod",
        "Priya Sharma",
        "VP Product",
        "Ex-Zomato | Building hyper-local delivery",
        "Bengaluru",
        "IIT Delhi",
        "6162764",
        "FoodTech & Quick Commerce",
    ),
    person(
        "swiggy-eng",
        "Gursimran Singh",
        "VP Engineering",
        "VP Engineering at Swiggy | Ex-Myntra",
        "Bengaluru",
        "BITS Pilani",
        "5702",
        "FoodTech & Quick Commerce",
    ),
    person(
        "swiggy-prod",
        "Ananya Roy",
        "Director of Product",
        "Former Swiggy | Instamart scale operator",
        "Bengaluru",
        "BITS Pilani",
        "5702",
        "FoodTech & Quick Commerce",
    ),
    person(
        "swiggy-exc",
        "",
        "Staff Logistics Architect",
        "Staff Logistics Architect at Swiggy",
        "Bengaluru",
        "IIT Madras",
        "5702",
        "FoodTech & Quick Commerce",
    ),
]

# Preserve backwards compatibility for benchmark tests
PEOPLE = BENCHMARK_PEOPLE


def response(endpoint, payload):
    if endpoint in ("/company/identify", "/company/search"):
        query_val = payload.get("query")
        if query_val:
            val = query_val.lower().strip()
        else:
            val = (payload.get("domains") or payload.get("names") or [""])[0].lower().strip()
        co = next(
            (
                x
                for x in ALL_DEMO_COMPANIES.values()
                if val
                in (
                    x["basic_info"]["name"].lower(),
                    x["basic_info"]["primary_domain"],
                    str(x["crustdata_company_id"]),
                )
                or x["basic_info"]["name"].lower() in val
                or val in x["basic_info"]["primary_domain"]
            ),
            None,
        )
        return (
            [{"matches": [{"confidence_score": 1, "company_data": co}]}]
            if co
            else fallback_response(endpoint, payload)
        )
    if endpoint == "/company/enrich":
        cid = str(payload["crustdata_company_ids"][0])
        co = ALL_DEMO_COMPANIES.get(cid)
        return (
            [{"matches": [{"company_data": co}]}]
            if co
            else fallback_response(endpoint, payload)
        )
    if endpoint == "/job/search":
        cid = str(payload.get("demo_company_id", "897127"))
        if cid == "897127":
            jobs = [
                {"title": "Lead Security Engineer", "url": "https://example.invalid/job/897127"},
                {"title": "Staff Engineer - Payment Risk", "url": "https://example.invalid/job/risk"},
            ]
        elif cid == "6042422":
            jobs = [
                {"title": "Staff Engineer - Machine Learning", "url": "https://example.invalid/job/6042422"},
                {"title": "Principal Architect - CRM Platform", "url": "https://example.invalid/job/crm"},
            ]
        elif cid == "6162764":
            jobs = [
                {"title": "Staff Logistics Optimization Engineer", "url": "https://example.invalid/job/zomato-dispatch"},
                {"title": "Lead Real-time Dispatch Architect", "url": "https://example.invalid/job/zomato-routing"},
            ]
        elif cid == "5702":
            jobs = [
                {"title": "Staff Infrastructure Engineer - Instamart Fleet", "url": "https://example.invalid/job/swiggy-fleet"},
                {"title": "Principal Architect - Delivery Routing", "url": "https://example.invalid/job/swiggy-routing"},
            ]
        else:
            jobs = [
                {"title": "Staff Backend Engineer", "url": "https://example.invalid/job/be"},
                {"title": "Principal Platform Architect", "url": "https://example.invalid/job/plat"},
            ]

        co_info = ALL_DEMO_COMPANIES.get(cid, {}).get("basic_info", {"name": f"Company {cid}"})
        return {
            "job_listings": [
                {
                    "job_details": {"title": j["title"], "url": j["url"]},
                    "company": {"basic_info": co_info},
                    "location": {"raw": "Bengaluru"},
                }
                for j in jobs
            ]
        }
    if endpoint == "/person/search":
        cid = str(payload.get("demo_company_id", "897127"))
        profiles = [
            p
            for p in ALL_DEMO_PEOPLE
            if str(
                p["experience"]["employment_details"]["current"][0][
                    "crustdata_company_id"
                ]
            )
            == cid
        ]
        if not profiles:
            profiles = [
                person(
                    f"synth-eng-{cid}",
                    f"VP Engineering ({cid})",
                    "VP Engineering",
                    f"VP Engineering at Entity #{cid}",
                    "Bengaluru",
                    "IIT Delhi",
                    cid,
                    "Technology",
                ),
                person(
                    f"synth-prod-{cid}",
                    f"VP Product ({cid})",
                    "VP Product",
                    f"Ex-Founder | VP Product at Entity #{cid}",
                    "Bengaluru",
                    "IIT Delhi",
                    cid,
                    "Technology",
                ),
            ]
        return {"profiles": profiles}
    if endpoint == "/person/enrich":
        pid = payload.get("person_id")
        return {"profiles": [p for p in ALL_DEMO_PEOPLE if p["person_id"] == pid]}
    return {}


def fallback_response(endpoint, payload):
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
