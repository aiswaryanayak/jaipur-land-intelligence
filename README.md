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

## Render deployment checklist

The active web entry point is `app.py`, exposing the Flask application as
`app`. The expected Render start command is:

```text
gunicorn app:app
```

`requirements.txt` declares Flask, requests, Beautiful Soup, and gunicorn.
Set `SITE_PASSWORD` as a Render environment variable; the app intentionally
returns HTTP 503 when it is absent and requires HTTP Basic authentication with
the `viewer` username.

The location database is read from `data/jaipur_land_intelligence.sqlite3`.
That directory is gitignored and the database is not currently tracked, so a
new Render instance will not contain the local location records unless a build
or release step creates/restores the database. Render's ephemeral filesystem
must not be treated as durable storage: use a persistent disk mounted at the
database path, or move the location data to a managed database before relying
on changes made at runtime.

Before deployment, confirm all of the following:

1. A release/build step initializes the schema without deleting reviewed
   location rows and imports the catalogue from `deploy_data/auctions.json`.
2. The initial database is restored or created with the five current location
   records, including their source metadata.
3. The service has durable storage for any future location imports.
4. `SITE_PASSWORD` is configured in Render and is not committed to the
   repository.
5. A health check exercises `/`, a mapped property, an approximate-locality
   property, and an unmapped property after deployment.

Until those persistence and initialization steps are configured and verified,
the current Render deployment cannot safely serve the reviewed SQLite location
data. Do not deploy this repository as if the ignored local database were
available remotely.

## Automated source refresh (local staging only)

The repeatable refresh command is:

```powershell
.venv\Scripts\python.exe refresh_pipeline.py
```

It fetches independent official-source inputs for RIICO e-auctions and
direct allotments, JDA post-auction results, and the UDH/JDA live index. Each
run writes a manifest and raw parsed records under
`data\refresh_pipeline\runs\<run-id>\`, with a copy at
`data\refresh_pipeline\latest.json`. Requests use bounded retries, a one-second
interval, and a fifteen-minute response cache by default:

```powershell
.venv\Scripts\python.exe refresh_pipeline.py --retries 2 --interval 1 --cache-ttl 900
```

Refresh output is staging only. It does not promote records into
`data\jaipur_land_intelligence.sqlite3`, replace the 238-record catalogue, or
publish closed records as available. A source failure or HTTP 429 is recorded
in the run manifest and does not cancel records by itself.

For Windows Task Scheduler, create a task that runs at an authority-approved
frequency (for example once daily, not continuously), with:

```text
Program: C:\path\to\jaipur_land_intelligence_prototype\.venv\Scripts\python.exe
Arguments: C:\path\to\jaipur_land_intelligence_prototype\refresh_pipeline.py --retries 2 --interval 1 --cache-ttl 900
Start in: C:\path\to\jaipur_land_intelligence_prototype
```

The repository includes a non-registering preview helper:

```powershell
.\setup_refresh_task.ps1
```

It checks for an existing task, verifies the virtual environment and runner,
and prints the intended task configuration without registering or enabling
anything. The runner is [run_refresh_task.ps1](./run_refresh_task.ps1); it
executes from the repository root, writes timestamped logs under
`data\refresh_pipeline\logs`, and returns the Python process exit code. After
review, registration requires the explicit `-Register` switch:

```powershell
.\setup_refresh_task.ps1 -Register
```

An existing task is never replaced automatically.

## Staging promotion

Review staged records without changing SQLite:

```powershell
.venv\Scripts\python.exe promote_staging.py
```

The command is dry-run by default and prints additions, updates, conflicts,
skipped records, and closed/historical records. Current RIICO staging rows are
not approved for catalogue promotion, so the default preview intentionally
does not propose importing them. A real promotion, when separately approved,
requires both `--apply` and `--confirm-production-promotion` and creates a
database backup first. No promotion is part of the refresh command.

Review source terms and rate limits before scheduling. The pipeline has not
been tested as a deployed service, and tenders, acquisitions, and additional
urban-authority collectors remain outside its implemented coverage.
