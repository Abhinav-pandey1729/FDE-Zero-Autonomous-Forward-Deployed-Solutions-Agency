# Foundry-Zero

A local venture-studio prototype with two founder pathways, a Crustdata client and cache, field-level hash-chained citations, resumable checkpoints, and a sandbox scheduler. No notifications are sent externally.

Install `pip install -r requirements.txt`, then run `streamlit run app.py`. Choose **Demo dataset** for clearly labeled synthetic records, or set `CRUSTDATA_API_KEY` in `.env` and choose **Live Crustdata**. Live data availability and response shapes depend on your Crustdata access. API cache entries do not expire automatically; delete cache rows to refresh them. Run tests with `python -m unittest discover -v`.

Citations provide provenance for deterministic heuristics, not independent verification of profile claims or proof of market demand. The ledger detects changes relative to its trusted head; export and retain that head outside SQLite to detect a complete rewrite. The $250,000 SAFE is a conditional discussion draft, not a binding funding commitment. Dates and calendars use UTC; sandbox participants are internal IDs and do not represent delivered invitations. Credit savings are not estimated without reliable pricing.

## Deploy on Streamlit Community Cloud

1. Sign in at <https://share.streamlit.io> with the GitHub account that owns this repo and authorize access to it.
2. **Create app** > **Deploy a public app from GitHub**: repository `Abhinav-pandey1729/FDE-Zero-Autonomous-Forward-Deployed-Solutions-Agency`, branch `main`, main file path `app.py`.
3. **Advanced settings** > **Secrets**, paste:

```toml
CRUSTDATA_API_KEY = "your_crustdata_api_key_here"
```

4. **Deploy**. Live Crustdata mode reads the key via `st.secrets` and falls back to `CRUSTDATA_API_KEY` in `.env` for local runs. `python-dotenv` is only needed for that local fallback.

Notes: the Community Cloud filesystem is ephemeral, so the SQLite database (`foundry_zero.db`) resets whenever the app sleeps or redeploys. Keep the **Demo dataset** source for shareable links when you do not want live API credit use.
