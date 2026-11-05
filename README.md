# FDE-Zero

Install dependencies with `python -m pip install -r requirements.txt`, set `CRUSTDATA_API_KEY` in `.env`, then run `streamlit run app.py`.

The database `fde_zero.db` contains source excerpts, profiles, API responses and local deliverables. Keep it private. The API cache has no expiry: clear individual `cache` rows to refresh stale data. Cache savings are estimates, not billing records. Completed checkpoints are not replayed. Citation and audit chains detect alteration only if their head hashes are saved to a trusted external location: an attacker who controls SQLite can rewrite a whole chain. Existing rows were chained at migration, not authenticated retroactively.

Runs recover completed checkpoints. Incomplete stages may repeat an API call if a crash occurs between its response and cache write. A unique SHA-256 dispatch key prevents duplicate local outbox rows. Web reconciliation only recognizes a narrow, explicitly attributed employee-count pattern; no detected mismatch is not confirmation, and dates and scopes may differ. The discrepancy preset is a hypothetical user-provided claim. Watch configs are local drafts and require a free preview and a separate approval before creating a recurring watch. No external outreach is sent.
