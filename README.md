# Jaipur Land Intelligence — Data Prototype 0.1

This prototype tests a public JDA post-auction table as the first extraction target.
**Important:** this page contains completed auction outcomes. It is used to prove the extraction/storage pipeline, not to populate a feed of active offers.

## What it does
- Makes one HTTP GET to the public JDA post-auction page.
- Parses the first HTML table into normalized fields.
- Stores each run and each observation in a local SQLite database.
- Exports the latest extraction to JSON and CSV.
- Keeps source URL, fetch timestamp, row fingerprint, and raw row data for traceability.
- Fails visibly if the source page or expected table structure changes.

## What it does not do
- It does not log in, bid, submit forms, bypass CAPTCHAs, rotate proxies, or evade access controls.
- It does not assert legal title, JDA approval of private property, or current availability.
- It does not treat post-auction records as live auctions.
- It does not yet establish that automated collection is permitted by the source's terms. Check applicable terms and contact the authority if unclear before scheduling repeated collection.

## Setup (Windows / PowerShell)
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python collector_jda_post_auction.py
```

If PowerShell blocks activation, run the script using `.venv\Scripts\python.exe` directly after installing requirements.

## Outputs
- `data/jaipur_land_intelligence.sqlite`
- `data/jda_post_auction_latest.json`
- `data/jda_post_auction_latest.csv`

The script makes a single request per execution. Do not schedule it until source access rules and operational frequency have been reviewed.

## Acceptance checks
1. The request succeeds with an HTTP 200 status.
2. Expected column headers are detected.
3. At least one row is parsed.
4. Parsed rows are stored with a source URL and timestamp.
5. Re-running does not create a new canonical record for an identical row, but creates a new observation/run record.
6. If the source layout changes, the collector fails rather than silently emitting misleading data.
