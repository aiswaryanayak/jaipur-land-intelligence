
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import hmac
import os
from flask import Flask, render_template_string, request, Response

app = Flask(__name__)
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")

@app.before_request
def require_site_password():
    if not SITE_PASSWORD:
        return Response("Site password is not configured.", status=503)

    auth = request.authorization
    if (
        auth is None
        or auth.username != "viewer"
        or not hmac.compare_digest(auth.password or "", SITE_PASSWORD)
    ):
        return Response(
            "Password required to access Jaipur Land Intelligence.",
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="Jaipur Land Intelligence"'},
        )
BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "deploy_data" / "auctions.json"
IST = timezone(timedelta(hours=5, minutes=30))

PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jaipur Land Intelligence</title>
<style>
:root{--navy:#173354;--blue:#245b91;--ink:#263447;--muted:#64748b;
--line:#dbe3ed;--bg:#f3f6fa;--white:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,Helvetica,sans-serif}
header{background:var(--navy);color:white;padding:24px max(20px,calc((100% - 1180px)/2))}
.brand{display:flex;align-items:center;gap:14px}
.emblem{border:2px solid #dbe6f3;border-radius:50%;width:46px;height:46px;
display:grid;place-items:center;font-size:25px;flex-shrink:0}
h1{font-size:24px;margin:0}.subtitle{font-size:13px;color:#dbe6f3;margin-top:7px}
nav{background:white;border-bottom:1px solid var(--line);padding:13px 20px}
.navinner{max-width:1180px;margin:auto;display:flex;gap:20px;flex-wrap:wrap}
nav a{font-size:12px;font-weight:bold;color:var(--navy);text-decoration:none}
main{max-width:1180px;margin:26px auto;padding:0 18px 45px}
h2{font-size:24px;color:var(--navy);margin:0 0 8px}
.muted{font-size:13px;color:var(--muted);line-height:1.5}
.notice{background:#fff9e9;border-left:4px solid #e6b951;padding:13px 15px;
font-size:13px;line-height:1.6;margin:20px 0}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:20px 0}
.stat,.filters,.card{background:white;border:1px solid var(--line);border-radius:8px}
.stat{padding:17px}.stat strong{display:block;font-size:25px;color:var(--navy);margin-bottom:5px}
.stat span{font-size:12px;color:var(--muted)}
.filters{padding:15px;display:grid;grid-template-columns:2fr 1fr auto;gap:12px;margin-bottom:18px}
input,select,button{min-width:0;width:100%;font:inherit;padding:12px;border:1px solid #cbd5e1;
border-radius:5px;background:white}
button{background:var(--navy);border-color:var(--navy);color:white;font-weight:bold;cursor:pointer}
.resultshead{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:18px 0 12px}
.results{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.card{padding:18px;min-width:0}
h3{font-size:16px;line-height:1.45;color:var(--navy);margin:12px 0 8px}
.pill{display:inline-block;font-size:11px;font-weight:bold;border-radius:20px;padding:5px 9px;
background:#edf2f7;color:#34445b;margin:2px 4px 2px 0}
.warn{background:#fff1d0;color:#805600}.past{background:#fce8e8;color:#9b2424}
.future{background:#e3f5e9;color:#17633a}
.details{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:15px 0}
.detail{border-top:1px solid #edf0f4;padding-top:9px;min-width:0}
.detail small{display:block;color:var(--muted);font-size:11px;margin-bottom:5px}
.detail strong{display:block;font-size:13px;overflow-wrap:anywhere;line-height:1.45}
.links{display:flex;flex-wrap:wrap;gap:12px;margin-top:14px}
.links a{color:var(--blue);font-size:12px;font-weight:bold}
.raw{font-size:12px;background:#f7f9fc;border-radius:5px;padding:10px;margin-top:14px;
overflow-wrap:anywhere;line-height:1.5}
.empty{grid-column:1/-1;text-align:center;background:white;padding:35px;border:1px solid var(--line);border-radius:8px}
footer{background:var(--navy);color:#dbe6f3;text-align:center;padding:22px;font-size:12px}
@media(max-width:720px){.stats{grid-template-columns:1fr}.filters,.results{grid-template-columns:1fr}h1{font-size:21px}}
</style>
</head>
<body>
<header><div class="brand">
<div class="emblem">⌂</div><div>
<h1>Jaipur Land Intelligence</h1>
<div class="subtitle">Government land and property opportunity information portal</div>
</div></div></header>
<nav><div class="navinner">
<a href="/">PROPERTY LISTINGS</a>
<a href="https://jda.rajasthan.gov.in/" target="_blank" rel="noopener">JDA OFFICIAL WEBSITE ↗</a>
<a href="https://udhonline.rajasthan.gov.in/Portal/AuctionListNew" target="_blank" rel="noopener">UDH E-AUCTIONS ↗</a>
<a href="https://eproc.rajasthan.gov.in/" target="_blank" rel="noopener">RAJASTHAN E-PROCUREMENT ↗</a>
</div></nav>
<main>
<h2>Property auction listings</h2>
<p class="muted">Collected catalogue records from Jaipur Development Authority (JDA) auction pages.</p>
<p class="muted">Catalogue retrieval timestamp: {{ retrieved_at }}</p>
<div class="notice"><strong>Verification notice:</strong> These records are collected from official catalogue pages.
A future deadline does not prove that an auction is open or a property is available.
Check the latest official auction details, corrigenda and notices before acting.</div>
<section class="stats">
<div class="stat"><strong>{{ total }}</strong><span>Unique auction IDs in collected catalogue</span></div>
<div class="stat"><strong>{{ upcoming }}</strong><span>Records with a future parsed bid deadline</span></div>
<div class="stat"><strong>{{ needs_check }}</strong><span>Records needing current official verification</span></div>
</section>
<form class="filters" method="get" action="/">
<input name="q" value="{{ q }}" placeholder="Search scheme, plot, property or auction ID">
<select name="category">
<option value="all" {% if category == 'all' %}selected{% endif %}>All property types</option>
<option value="residential" {% if category == 'residential' %}selected{% endif %}>Residential</option>
<option value="commercial" {% if category == 'commercial' %}selected{% endif %}>Commercial</option>
<option value="other" {% if category == 'other' %}selected{% endif %}>Other / unclassified</option>

</select>
<select name="deadline">
  <option value="all" {% if deadline_filter == 'all' %}selected{% endif %}>All deadline statuses</option>
  <option value="upcoming" {% if deadline_filter == 'upcoming' %}selected{% endif %}>Future deadline — verify</option>
  <option value="passed" {% if deadline_filter == 'passed' %}selected{% endif %}>Bid deadline passed</option>
  <option value="unknown" {% if deadline_filter == 'unknown' %}selected{% endif %}>Needs verification</option>
</select>
<button type="submit">Search listings</button>

</form>
<div class="resultshead"><strong>{{ results|length }} matching records</strong>
<span class="muted">Deadline status is based on dates published in the collected record.</span></div>
<section class="results">
{% for item in results %}
<article class="card">
<span class="pill">{{ item.category_label }}</span>
<span class="pill warn">Official status not verified</span>
<h3>{{ item.title }}</h3>
<div class="muted">Auction ID: {{ item.auction_id }}</div>
<div class="details">
<div class="detail"><small>Scheme</small><strong>{{ item.scheme }}</strong></div>
<div class="detail"><small>Property / plot number</small><strong>{{ item.property_number }}</strong></div>
<div class="detail"><small>Area</small><strong>{{ item.area }}</strong></div>
<div class="detail"><small>Usage</small><strong>{{ item.usage }}</strong></div>
<div class="detail"><small>EMD amount</small><strong>{{ item.emd }}</strong></div>
<div class="detail"><small>Assessed value</small><strong>{{ item.assessed_value }}</strong></div>
<div class="detail"><small>EMD deadline (listed)</small><strong>{{ item.emd_end }}</strong></div>
<div class="detail"><small>Bid deadline (listed)</small><strong>{{ item.bid_end }}</strong></div>
</div>
<span class="pill {{ item.status_class }}">{{ item.deadline_status }}</span>
<div class="muted" style="margin-top:8px">{{ item.provenance_count }} collected observations for this ID</div>
<div class="links">
{% for link in item.detail_links %}<a href="{{ link }}" target="_blank" rel="noopener">Official details{% if not loop.first %} (alternate){% endif %} ↗</a>{% endfor %}
{% for link in item.listing_links %}<a href="{{ link }}" target="_blank" rel="noopener">{% if loop.first %}Official auction listing{% else %}Alternate official listing{% endif %} ↗</a>{% endfor %}
</div>
<details class="raw"><summary>View original source row</summary><p>{{ item.raw_text }}</p></details>
</article>
{% else %}
<div class="empty">No matching records. Try another search or select All property types.</div>
{% endfor %}
</section></main>
<footer>Jaipur Land Intelligence · Prototype · Verify current status through official sources.</footer>
</body></html>
"""


def text(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, list):
        return " ".join(text(v) for v in value if text(v))
    if isinstance(value, dict):
        return " ".join(text(v) for v in value.values() if text(v))
    return str(value)


def field(row, label, next_labels):
    """Extract the text between two labelled columns in the scraped row."""
    labels = [re.escape(label)] + [re.escape(x) for x in next_labels]
    pattern = r"(?is)" + labels[0] + r"\s*:\s*(.*?)\s+(?:" + "|".join(
        labels[1:]
    ) + r")\s*:"
    match = re.search(pattern, row)
    return match.group(1).strip(" :-|") if match else ""


def find_date(row, label):
    pattern = (
        re.escape(label)
        + r"\s*:\s*(\d{1,2}-[A-Za-z]{3}-\d{4}\s+\d{1,2}:\d{2}\s+[APap][Mm])"
    )
    match = re.search(pattern, row, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d-%b-%Y %I:%M %p").replace(tzinfo=IST)
    except ValueError:
        return None


def official_links(value):
    """Keep exact official URLs from the collected data; never rebuild URLs."""
    found = []

    def add(candidate):
        if isinstance(candidate, str):
            if candidate.startswith("https://"):
                if urlparse(candidate).netloc.lower() == "udhonline.rajasthan.gov.in":
                    found.append(candidate)
        elif isinstance(candidate, dict):
            for key in ("url", "href", "link", "detail_url", "listing_url"):
                if candidate.get(key):
                    add(candidate[key])
            # Some collector formats store a URL as the dictionary value.
            for key, val in candidate.items():
                if isinstance(val, str) and val.startswith("https://"):
                    add(val)

    add(value)
    return list(dict.fromkeys(found))


def latest_snapshot_file():
    """Return the newest refresh snapshot containing parsed records, if any."""
    snapshot_dir = BASE_DIR / "data" / "udh_test" / "refresh_snapshots"
    candidates = sorted(
        snapshot_dir.glob("jda_refresh_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if snapshot_dir.exists() else []

    for path in candidates:
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict) and isinstance(payload.get("records"), list) and payload["records"]:
                return path
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return None


def load_catalogue():
    """Load the latest successful refresh snapshot, falling back to the original catalogue."""
    snapshot_file = latest_snapshot_file()
    data_file = snapshot_file or DATA_FILE

    if not data_file.exists():
        raise FileNotFoundError(f"Could not find a refresh snapshot or catalogue: {data_file}")

    with data_file.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, dict):
        records = payload.get("records", payload.get("observations", []))
        snapshot_timestamp = text(payload.get("retrieved_at_utc"))
    elif isinstance(payload, list):
        records = payload
        snapshot_timestamp = ""
    else:
        records = []
        snapshot_timestamp = ""

    grouped = defaultdict(list)
    for rec in records:
        if isinstance(rec, dict) and rec.get("auction_id") is not None:
            grouped[str(rec["auction_id"])].append(rec)

    now = datetime.now(IST)
    items = []
    timestamps = [snapshot_timestamp] if snapshot_timestamp else []

    for auction_id, observations in grouped.items():
        # Prefer the newest observation for the visible fields while retaining all links.
        rec = max(observations, key=lambda x: str(x.get("retrieved_at_utc", "")))
        row = text(rec.get("raw_row_text") or rec.get("auction_row_text"))

        title = text(rec.get("title")) or field(row, "Title", ["Scheme Name"]) or f"Auction ID {auction_id}"
        scheme = text(rec.get("scheme_name") or rec.get("catalog_scheme_name")) or field(row, "Scheme Name", ["Property Number", "PropertyNumber"]) or "Not stated"
        property_number = text(rec.get("property_number")) or field(row, "Property Number", ["Property Area"]) or field(row, "PropertyNumber", ["Property Area"]) or "Not stated"
        area = text(rec.get("property_area") or rec.get("area")) or field(row, "Property Area", ["Usage Type"]) or "Not stated"
        usage = text(rec.get("usage_type") or rec.get("usage")) or field(row, "Usage Type", ["EMD Deposit Start Date"]) or "Not stated"
        emd_start = text(rec.get("emd_start")) or find_date(row, "EMD Deposit Start Date")
        emd_end = text(rec.get("emd_end")) or find_date(row, "EMD Deposit End Date")
        emd = text(rec.get("emd_amount") or rec.get("emd")) or field(row, "EMD Amount (Rs.)", ["Bid Start Date"]) or "Not stated"
        bid_start = text(rec.get("bid_start")) or find_date(row, "Bid Start Date")
        bid_end = text(rec.get("bid_end")) or find_date(row, "Bid End Date")
        assessed = text(rec.get("assessed_value") or rec.get("assessed_value_raw"))
        if not assessed:
            assessed_row = text(rec.get("raw_assessed_value_row_text") or rec.get("assessed_value_row_text"))
            assessed_match = re.search(r"Assessed Property Value as per Bid Start Price\s*\(BSP\)\s*:\s*(.*?)(?=\s*\(Actual value|$)", assessed_row, re.I)
            assessed = assessed_match.group(1).strip() if assessed_match else "Not stated"

        usage_lower = usage.lower()
        title_lower = title.lower()
        if "residential" in usage_lower or "residential" in title_lower:
            category, category_label = "residential", "Residential"
        elif any(term in usage_lower or term in title_lower for term in ("commercial", "shop", "office", "transport")):
            category, category_label = "commercial", "Commercial / shop / transport"
        else:
            category, category_label = "other", "Other / unclassified"

        emd_dt = parse_date(emd_end)
        bid_dt = parse_date(bid_end)

        if bid_dt and bid_dt < now:
            status = "Published bid deadline has passed"
            status_class = "past"
        elif emd_dt and emd_dt < now:
            status = "Published EMD deadline has passed — verify eligibility"
            status_class = "warn"
        elif bid_dt and bid_dt >= now:
            status = "Future bid deadline listed — verify officially"
            status_class = "future"
        else:
            status = "Deadline not reliably parsed — verify"
            status_class = "warn"

        listing_links = []
        detail_links = []
        for observation in observations:
            listing_links.extend(official_links(observation.get("source_url")))
            listing_links.extend(official_links(observation.get("listing_url")))
            detail_links.extend(official_links(observation.get("detail_links")))
            stamp = observation.get("retrieved_at_utc")
            if stamp:
                timestamps.append(str(stamp))

        items.append({
            "auction_id": auction_id,
            "title": title,
            "scheme": scheme,
            "property_number": property_number,
            "area": area,
            "usage": usage,
            "emd": emd,
            "assessed_value": assessed or "Not stated",
            "emd_end": emd_end or "Not reliably extracted",
            "bid_end": bid_end or "Not reliably extracted",
            "category": category,
            "category_label": category_label,
            "deadline_status": status,
            "status_class": status_class,
            "listing_links": list(dict.fromkeys(listing_links)),
            "detail_links": list(dict.fromkeys(detail_links)),
            "provenance_count": len(observations),
            "raw_text": row,
            "_bid_dt": bid_dt,
        })

    items.sort(key=lambda item: (
        item["_bid_dt"] is None,
        item["_bid_dt"] or datetime.max.replace(tzinfo=IST),
        item["auction_id"],
    ))
    retrieved_at = max(timestamps) if timestamps else "Timestamp unavailable"
    if snapshot_file:
        retrieved_at = f"{retrieved_at} · latest refresh snapshot"
    return items, retrieved_at




    
@app.route("/")
def home():
    try:
        items, retrieved_at = load_catalogue()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"<h1>Could not load catalogue</h1><pre>{str(exc)}</pre>", 500

    now = datetime.now(IST)
    q = request.args.get("q", "").strip().lower()
    category = request.args.get("category", "all").strip().lower()
    deadline_filter = request.args.get("deadline", "all").strip().lower()

    if category not in {"all", "residential", "commercial", "other"}:
        category = "all"

    if deadline_filter not in {"all", "upcoming", "passed", "unknown"}:
        deadline_filter = "all"

    upcoming = sum(
        1 for item in items
        if item["_bid_dt"] and item["_bid_dt"] >= now
    )

    results = []
    for item in items:
        searchable = " ".join(
            str(item[k]) for k in (
                "auction_id", "title", "scheme", "property_number", "area", "usage"
            )
        ).lower()

        if q and q not in searchable:
            continue

        if category != "all" and item["category"] != category:
            continue

        bid_dt = item["_bid_dt"]
        emd_dt = parse_date(item["emd_end"])

        # A future bid deadline alone does not prove that bidding is possible.
        if bid_dt is None:
            item["deadline_status"] = "Deadline not reliably parsed — verify"
            item["status_class"] = "warn"
            status_group = "unknown"
        elif bid_dt < now:
            item["deadline_status"] = "Published bid deadline has passed"
            item["status_class"] = "past"
            status_group = "passed"
        elif emd_dt and emd_dt < now:
            item["deadline_status"] = "EMD deadline has passed — eligibility needs verification"
            item["status_class"] = "warn"
            status_group = "unknown"
        else:
            item["deadline_status"] = "Future published deadline — official status unverified"
            item["status_class"] = "future"
            status_group = "upcoming"

        if deadline_filter != "all" and status_group != deadline_filter:
            continue

        results.append(item)

    return render_template_string(
        PAGE,
        results=results,
        q=request.args.get("q", ""),
        category=category,
        total=len(items),
        upcoming=upcoming,
        needs_check=len(items),
        retrieved_at=retrieved_at,
        deadline_filter=deadline_filter,
    )



if __name__ == "__main__":
    print("Jaipur Land Intelligence is starting...")
    print("Open http://127.0.0.1:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=False)
