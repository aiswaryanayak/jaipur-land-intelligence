
from __future__ import annotations

import json
import re
import sqlite3
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
AUTHORITY_DATA_FILE = BASE_DIR / "deploy_data" / "authority_records.json"
LOCATION_DB_FILE = BASE_DIR / "data" / "jaipur_land_intelligence.sqlite3"
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
<a href="/">OVERVIEW</a>
<a href="/authority/jda">JDA</a>
<a href="/authority/riico">RIICO</a>
<a href="/authority/other">OTHER AUTHORITIES</a>
<a href="https://jda.rajasthan.gov.in/" target="_blank" rel="noopener">JDA OFFICIAL WEBSITE ↗</a>
<a href="https://udhonline.rajasthan.gov.in/Portal/AuctionListNew" target="_blank" rel="noopener">UDH E-AUCTIONS ↗</a>
<a href="https://www.riicoerp.industries.rajasthan.gov.in/eauction" target="_blank" rel="noopener">RIICO E-AUCTIONS ↗</a>
<a href="https://eproc.rajasthan.gov.in/" target="_blank" rel="noopener">RAJASTHAN E-PROCUREMENT ↗</a>
</div></nav>
<main>
<h2>Property auction listings</h2>
<p class="muted">Collected catalogue records from Jaipur Development Authority (JDA) auction pages.</p>
<p class="muted">Catalogue retrieval timestamp: {{ retrieved_at }}</p>
<div class="notice"><strong>Verification notice:</strong> These records are collected from official catalogue pages.
A future deadline does not prove that an auction is open or a property is available.
Check the latest official auction details, corrigenda and notices before acting.</div>
<section class="card">
<h3>Automated source refresh</h3>
{% if refresh_status %}
<p class="muted">Run {{ refresh_status.run_id }} · checked {{ refresh_status.checked_at }}</p>
<div class="details">{% for source in refresh_status.sources %}
<div class="detail"><small>{{ source.source_key }}</small><strong>{{ source.fetch_status }} · {{ source.record_count }} records{% if source.error %} · {{ source.error }}{% endif %}</strong></div>
{% endfor %}</div>
<p class="muted">Refresh output is staged for review. It is not automatically promoted into this catalogue.</p>
{% else %}
<p class="muted">No refresh manifest is available. Run the local refresh command to populate source health information.</p>
{% endif %}
</section>
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
<div class="price-filter">
<label for="min_price">Minimum price: ₹{{ "{:,.0f}".format(min_price) }}</label>
<input id="min_price" name="min_price" type="range" min="{{ price_min }}" max="{{ price_max }}" step="1" value="{{ min_price }}">
<label for="max_price">Maximum price: ₹{{ "{:,.0f}".format(max_price) }}</label>
<input id="max_price" name="max_price" type="range" min="{{ price_min }}" max="{{ price_max }}" step="1" value="{{ max_price }}">
</div>
<button type="submit">Search listings</button>

</form>
<div class="resultshead"><strong>{{ results|length }} matching records</strong>
<span class="muted">Deadline status is based on dates published in the collected record.</span></div>
<section class="results">
{% for item in results %}
<article class="card">
<span class="pill">{{ item.category_label }}</span>
<span class="pill warn">Official status not verified</span>
<h3><a href="/property/{{ item.auction_id }}">{{ item.title }}</a></h3>
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
{% for link in item.listing_links %}<a href="{{ link }}" target="_blank" rel="noopener">Source listing ↗</a>{% endfor %}
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
            "emd_start": emd_start or "Not reliably extracted",
            "emd_end": emd_end or "Not reliably extracted",
            "bid_start": bid_start or "Not reliably extracted",
            "bid_end": bid_end or "Not reliably extracted",
            "category": category,
            "category_label": category_label,
            "deadline_status": status,
            "status_class": status_class,
            "listing_links": list(dict.fromkeys(listing_links)),
            "detail_links": list(dict.fromkeys(detail_links)),
            "provenance_count": len(observations),
            "raw_text": row,
            "source_authority": text(rec.get("source_authority")) or "Unspecified authority",
            "_bid_dt": bid_dt,
            "_price": numeric_price(assessed),
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


def numeric_price(value):
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def load_authority_records(authority_key):
    if not AUTHORITY_DATA_FILE.exists():
        return []
    try:
        payload = json.loads(AUTHORITY_DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    records = payload.get("records", []) if isinstance(payload, dict) else []
    selected = []
    for record in records:
        if not isinstance(record, dict) or record.get("_display_authority") != authority_key:
            continue
        status = text(record.get("current_status")) or "status not verified"
        selected.append({
            "auction_id": text(record.get("original_identifier")) or text(record.get("record_key")),
            "title": text(record.get("title")) or "Untitled authority record",
            "scheme": text(record.get("scheme_or_industrial_area")) or "Not stated",
            "property_number": text(record.get("property_or_plot_number")) or "Not stated",
            "area": text(record.get("area")) or "Not stated",
            "usage": text(record.get("land_use_category")) or "Not stated",
            "emd": text(record.get("emd")) or "Not stated",
            "assessed_value": text(record.get("reserve_price_or_rate")) or "Not stated",
            "emd_start": text(record.get("application_start")) or "Not stated",
            "emd_end": text(record.get("application_end")) or "Not stated",
            "bid_start": text(record.get("bid_start")) or "Not stated",
            "bid_end": text(record.get("bid_end")) or "Not stated",
            "category": "other",
            "category_label": "Historical allotment" if authority_key != "riico" else "RIICO industrial record",
            "deadline_status": status,
            "status_class": "past" if "historical" in status or "closed" in status else "warn",
            "listing_links": [record["source_url"]] if text(record.get("source_url")).startswith("https://") else [],
            "detail_links": [],
            "provenance_count": 1,
            "raw_text": text(record.get("notes")),
            "source_authority": text(record.get("authority")),
            "_bid_dt": parse_date(text(record.get("bid_end"))),
            "_price": numeric_price(record.get("reserve_price_or_rate")),
            "_source_status": status.casefold(),
            "_display_record": True,
        })
    return selected


def load_refresh_status():
    path = BASE_DIR / "data" / "refresh_pipeline" / "latest.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None




    
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
    price_values = [item["_price"] for item in items if item["_price"] is not None]
    price_min = min(price_values) if price_values else 0
    price_max = max(price_values) if price_values else 0
    try:
        min_price = max(price_min, float(request.args.get("min_price", price_min)))
    except (TypeError, ValueError):
        min_price = price_min
    try:
        max_price = min(price_max, float(request.args.get("max_price", price_max)))
    except (TypeError, ValueError):
        max_price = price_max
    if min_price > max_price:
        min_price, max_price = max_price, min_price

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
        if item["_price"] is not None and not (min_price <= item["_price"] <= max_price):
            continue
        if item["_price"] is None and (min_price > price_min or max_price < price_max):
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
        min_price=min_price,
        max_price=max_price,
        price_min=price_min,
        price_max=price_max,
        refresh_status=load_refresh_status(),
    )


AUTHORITY_PAGE = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} · Jaipur Land Intelligence</title>
<style>
body{margin:0;background:#f3f6fa;color:#263447;font-family:Arial,Helvetica,sans-serif}
header{background:#173354;color:white;padding:24px max(20px,calc((100% - 1180px)/2))}
nav{background:white;border-bottom:1px solid #dbe3ed;padding:13px 20px}
nav div{max-width:1180px;margin:auto;display:flex;gap:18px;flex-wrap:wrap}
nav a{font-size:12px;font-weight:bold;color:#173354;text-decoration:none}
main{max-width:1180px;margin:26px auto;padding:0 18px 45px}
h1{margin:0;font-size:24px}h2{color:#173354}.muted{font-size:13px;color:#64748b;line-height:1.5}
.notice{background:#fff9e9;border-left:4px solid #e6b951;padding:13px 15px;font-size:13px;line-height:1.6;margin:20px 0}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:20px 0}
.stat,.filters,.card,.empty{background:white;border:1px solid #dbe3ed;border-radius:8px}
.stat{padding:17px}.stat strong{display:block;font-size:25px;color:#173354}.stat span{font-size:12px;color:#64748b}
.filters{padding:15px;display:grid;grid-template-columns:2fr 1fr 1fr 2fr auto;gap:12px}
.price-filter{display:grid;gap:4px;color:#64748b;font-size:12px}.price-filter input{padding:0}
input,select,button{min-width:0;width:100%;font:inherit;padding:12px;border:1px solid #cbd5e1;border-radius:5px;background:white}
button{background:#173354;border-color:#173354;color:white;font-weight:bold}
.results{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:18px}
.card{padding:18px}.card h3{color:#173354;line-height:1.45}.pill{display:inline-block;font-size:11px;font-weight:bold;border-radius:20px;padding:5px 9px;background:#edf2f7;color:#34445b;margin:2px 4px 2px 0}
.warn{background:#fff1d0;color:#805600}.past{background:#fce8e8;color:#9b2424}.future{background:#e3f5e9;color:#17633a}
.details{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:15px 0}.detail{border-top:1px solid #edf0f4;padding-top:9px}.detail small{display:block;color:#64748b;font-size:11px}.detail strong{display:block;font-size:13px;line-height:1.45}
.links{display:flex;flex-wrap:wrap;gap:12px}.links a{color:#245b91;font-size:12px;font-weight:bold}.empty{padding:35px;text-align:center;grid-column:1/-1}
@media(max-width:720px){.stats,.filters,.results{grid-template-columns:1fr}}
</style></head><body>
<header><h1>{{ title }}</h1><div class="muted" style="color:#dbe6f3">Authority-filtered opportunity records</div></header>
<nav><div><a href="/">OVERVIEW</a><a href="/authority/jda">JDA</a><a href="/authority/riico">RIICO</a><a href="/authority/other">OTHER AUTHORITIES</a></div></nav>
<main><h2>{{ heading }}</h2><p class="muted">{{ description }}</p>
<div class="notice"><strong>Status caution:</strong> Published dates and source records do not certify current availability. Confirm the latest official notice before acting.</div>
<section class="stats"><div class="stat"><strong>{{ results|length }}</strong><span>Matching records</span></div><div class="stat"><strong>{{ total }}</strong><span>Canonical records for this authority</span></div><div class="stat"><strong>{{ upcoming }}</strong><span>Future published bid deadlines</span></div></section>
<form class="filters" method="get"><input name="q" value="{{ q }}" placeholder="Search scheme, plot or auction ID">
<select name="category"><option value="all">All property types</option><option value="residential" {% if category=='residential' %}selected{% endif %}>Residential</option><option value="commercial" {% if category=='commercial' %}selected{% endif %}>Commercial</option><option value="other" {% if category=='other' %}selected{% endif %}>Other</option></select>
<select name="deadline"><option value="all">All statuses</option><option value="upcoming" {% if deadline=='upcoming' %}selected{% endif %}>Upcoming</option><option value="passed" {% if deadline=='passed' %}selected{% endif %}>Closed / passed</option><option value="unknown" {% if deadline=='unknown' %}selected{% endif %}>Needs verification</option></select>
<button type="submit">Filter</button></form>
<section class="results">{% for item in results %}<article class="card">
<span class="pill">{{ item.category_label }}</span><span class="pill {{ item.status_class }}">{{ item.deadline_status }}</span>
<h3><a href="/property/{{ item.auction_id }}">{{ item.title }}</a></h3><div class="muted">{{ item.source_authority }} · Auction ID {{ item.auction_id }}</div>
<div class="details"><div class="detail"><small>Scheme / industrial area</small><strong>{{ item.scheme }}</strong></div><div class="detail"><small>Property / plot</small><strong>{{ item.property_number }}</strong></div><div class="detail"><small>Area</small><strong>{{ item.area }}</strong></div><div class="detail"><small>Land use</small><strong>{{ item.usage }}</strong></div><div class="detail"><small>Reserve / assessed value</small><strong>{{ item.assessed_value }}</strong></div><div class="detail"><small>EMD</small><strong>{{ item.emd }}</strong></div><div class="detail"><small>Application / EMD dates</small><strong>{{ item.emd_start }} to {{ item.emd_end }}</strong></div><div class="detail"><small>Bid dates</small><strong>{{ item.bid_start }} to {{ item.bid_end }}</strong></div></div>
<div class="links">{% for link in item.detail_links + item.listing_links %}<a href="{{ link }}" target="_blank" rel="noopener">Official source ↗</a>{% endfor %}</div>
</article>{% else %}<div class="empty"><strong>No records available for this authority and filter.</strong><p class="muted">RIICO and other-authority records remain in staging until official identifiers and fields are reviewed.</p></div>{% endfor %}</section>
</main></body></html>
"""


@app.route("/authority/<authority_key>")
def authority_page(authority_key):
    authority_map = {
        "jda": ("Jaipur Development Authority", "JDA opportunities", "Canonical JDA auction records from the existing collected catalogue."),
        "riico": ("RIICO", "RIICO reviewed records", "Reviewed RIICO e-auction records are shown as closed or unavailable where the 14 October 2026 EMD deadline has passed. They are not new-application opportunities."),
        "other": ("Other verified authorities", "Historical authority records", "Historical RHB allotment outcomes and JDA institutional land allotments are shown for reference only. No current purchasable opportunity is asserted."),
    }
    if authority_key not in authority_map:
        return "Authority page not found", 404
    authority, heading, description = authority_map[authority_key]
    try:
        items, _ = load_catalogue()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"<h1>Could not load catalogue</h1><pre>{str(exc)}</pre>", 500
    if authority_key == "riico":
        filtered = load_authority_records("riico")
    elif authority_key == "other":
        filtered = load_authority_records("rhb") + load_authority_records("jda")
    else:
        filtered = [item for item in items if (
        (authority_key == "jda" and item["source_authority"].casefold() == authority.casefold())
        or (authority_key == "riico" and item["source_authority"].casefold() == "riico")
        or (authority_key == "other" and item["source_authority"].casefold() not in {"jaipur development authority", "riico"})
        )]
    q = request.args.get("q", "").strip().casefold()
    category = request.args.get("category", "all").strip().lower()
    deadline = request.args.get("deadline", "all").strip().lower()
    if category not in {"all", "residential", "commercial", "other"}:
        category = "all"
    if deadline not in {"all", "upcoming", "passed", "unknown"}:
        deadline = "all"
    total = len(filtered)
    now = datetime.now(IST)
    results = []
    for item in filtered:
        searchable = " ".join(str(item[k]) for k in ("auction_id", "title", "scheme", "property_number", "area", "usage")).casefold()
        if q and q not in searchable:
            continue
        if category != "all" and item["category"] != category:
            continue
        if "historical" in item.get("_source_status", "") or "closed" in item.get("_source_status", ""):
            item["deadline_status"], item["status_class"], group = item["_source_status"], "past", "passed"
        elif item["_bid_dt"] is None:
            item["deadline_status"], item["status_class"], group = "Needs verification", "warn", "unknown"
        elif item["_bid_dt"] < now:
            item["deadline_status"], item["status_class"], group = "Published bid deadline passed", "past", "passed"
        else:
            item["deadline_status"], item["status_class"], group = "Future published deadline — verify", "future", "upcoming"
        if deadline != "all" and deadline != group:
            continue
        results.append(item)
    upcoming = sum(1 for item in filtered if item["_bid_dt"] and item["_bid_dt"] >= now)
    return render_template_string(AUTHORITY_PAGE, title="Jaipur Land Intelligence", heading=heading,
        description=description, results=results, total=total, upcoming=upcoming, q=request.args.get("q", ""),
        category=category, deadline=deadline)


def load_scheme_location(scheme_name):
    """Read one exact, reviewed scheme-level location without changing the database."""
    normalized = re.sub(r"\s+", " ", str(scheme_name or "")).strip().casefold()
    if not normalized or not LOCATION_DB_FILE.exists():
        return None

    uri = f"file:{LOCATION_DB_FILE.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(
                """
                SELECT sl.label, l.latitude, l.longitude, l.precision,
                       l.status, l.source_url, l.evidence_notes,
                       l.coordinate_subject, l.verification_status, l.confidence
                FROM scheme_labels AS sl
                JOIN locations AS l ON l.scheme_label_id = sl.id
                WHERE sl.normalized_label = ?
                  AND l.status IN ('provisional', 'verified')
                ORDER BY CASE l.status WHEN 'verified' THEN 0 ELSE 1 END, l.id
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("Could not read the scheme-location database.") from exc

    if row is None:
        return None

    return {
        "scheme_label": row[0],
        "latitude": row[1],
        "longitude": row[2],
        "precision": row[3],
        "status": row[4],
        "source_url": row[5],
        "evidence_notes": row[6],
        "coordinate_subject": row[7],
        "verification_status": row[8],
        "confidence": row[9],
    }


DETAIL_PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ item.title }} · Jaipur Land Intelligence</title>
<style>
:root{--navy:#173354;--blue:#245b91;--ink:#263447;--muted:#64748b;--line:#dbe3ed;--bg:#f3f6fa;--white:#fff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,Helvetica,sans-serif}
header{background:var(--navy);color:white;padding:24px max(20px,calc((100% - 1000px)/2))}h1{font-size:23px;line-height:1.4;margin:0}.subtitle{font-size:13px;color:#dbe6f3;margin-top:6px}
nav{background:white;border-bottom:1px solid var(--line);padding:13px 20px}nav a{max-width:1000px;margin:auto;display:block;color:var(--blue);font-size:13px;font-weight:bold;text-decoration:none}
main{max-width:1000px;margin:25px auto;padding:0 18px 45px}.panel{background:white;border:1px solid var(--line);border-radius:9px;padding:22px;margin-bottom:16px}.muted{font-size:13px;color:var(--muted);line-height:1.6}.pill{display:inline-block;font-size:11px;font-weight:bold;border-radius:20px;padding:6px 10px;background:#edf2f7;color:#34445b;margin:2px 4px 2px 0}.warn{background:#fff1d0;color:#805600}.past{background:#fce8e8;color:#9b2424}.future{background:#e3f5e9;color:#17633a}
h2{font-size:17px;color:var(--navy);margin:0 0 14px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 20px}.detail{padding:12px 0;border-top:1px solid #edf0f4;min-width:0}.detail small{display:block;color:var(--muted);font-size:11px;margin-bottom:6px}.detail strong{font-size:14px;line-height:1.5;overflow-wrap:anywhere}.links{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}.links a{color:var(--blue);font-size:13px;font-weight:bold;overflow-wrap:anywhere}.notice{background:#fff9e9;border-left:4px solid #e6b951;padding:13px 15px;font-size:13px;line-height:1.6;margin-bottom:16px}.raw{font-size:13px;background:#f7f9fc;border-radius:5px;padding:12px;overflow-wrap:anywhere;line-height:1.6;white-space:pre-wrap}.map-frame{width:100%;height:360px;border:1px solid var(--line);border-radius:7px;overflow:hidden;margin:14px 0}.map-frame iframe{width:100%;height:100%;border:0}@media(max-width:650px){.grid{grid-template-columns:1fr}.map-frame{height:280px}header{padding:20px}}
</style></head><body>
<header><h1>{{ item.title }}</h1><div class="subtitle">Jaipur Land Intelligence · Property record</div></header>
<nav><a href="{{ back_url }}">← Back to property listings</a></nav>
<main>
<div class="notice"><strong>Important:</strong> This page organizes collected catalogue information. It does not certify that the property is available or that an auction is currently open. Confirm current status, eligibility, terms, notices and corrigenda with the official authority.</div>
<section class="panel"><span class="pill">{{ item.category_label }}</span><span class="pill warn">Official status not verified</span><span class="pill {{ item.status_class }}">{{ item.deadline_status }}</span><p class="muted">Auction ID: {{ item.auction_id }} · {{ item.provenance_count }} collected observation(s) for this ID</p>
<h2>Property and auction details</h2><div class="grid">
<div class="detail"><small>Property title</small><strong>{{ item.title }}</strong></div><div class="detail"><small>Auction ID</small><strong>{{ item.auction_id }}</strong></div>
<div class="detail"><small>Scheme</small><strong>{{ item.scheme }}</strong></div><div class="detail"><small>Property / plot number</small><strong>{{ item.property_number }}</strong></div>
<div class="detail"><small>Area</small><strong>{{ item.area }}</strong></div><div class="detail"><small>Usage</small><strong>{{ item.usage }}</strong></div>
<div class="detail"><small>EMD amount</small><strong>{{ item.emd }}</strong></div><div class="detail"><small>Assessed value (source-listed)</small><strong>{{ item.assessed_value }}</strong></div>
<div class="detail"><small>EMD start (listed)</small><strong>{{ item.emd_start }}</strong></div><div class="detail"><small>EMD deadline (listed)</small><strong>{{ item.emd_end }}</strong></div>
<div class="detail"><small>Bid start (listed)</small><strong>{{ item.bid_start }}</strong></div><div class="detail"><small>Bid deadline (listed)</small><strong>{{ item.bid_end }}</strong></div>
</div></section>
{% if item.map_location %}
<section class="panel"><h2>Navigate to the scheme area</h2>
<div class="notice"><strong>Location accuracy:</strong> This is a {{ item.map_location.precision }} pin for {{ item.map_location.coordinate_subject or "a reviewed location" }}. {% if item.map_location.precision == "locality_only" %}<strong>Locality navigation only:</strong> the actual scheme boundary and plot are unverified.{% else %}It is not the exact plot location, entrance, or legal boundary.{% endif %} Confirm the plot against the official location plan before visiting or making a decision.</div>
<p><strong>{{ item.map_location.scheme_label }}</strong></p>
<p class="muted">Verification status: {{ item.map_location.status }} / {{ item.map_location.verification_status or "not separately recorded" }} · Confidence: {{ item.map_location.confidence or "not recorded" }} · Coordinates: {{ "%.6f"|format(item.map_location.latitude) }}, {{ "%.6f"|format(item.map_location.longitude) }}</p>
<div class="map-frame"><iframe title="Scheme-area map" loading="lazy" referrerpolicy="no-referrer-when-downgrade" src="https://maps.google.com/maps?q={{ item.map_location.latitude }},{{ item.map_location.longitude }}&z=14&output=embed" allowfullscreen></iframe></div>
<div class="links"><a href="https://www.google.com/maps/dir/?api=1&destination={{ item.map_location.latitude }},{{ item.map_location.longitude }}" target="_blank" rel="noopener noreferrer">Get directions to scheme area ↗</a>{% if item.map_location.source_url %}<a href="{{ item.map_location.source_url }}" target="_blank" rel="noopener noreferrer">Open location source ↗</a>{% endif %}</div>
<p class="muted">{{ item.map_location.evidence_notes }}</p>
</section>
{% else %}
<section class="panel"><h2>Location</h2><p class="muted"><strong>Location not yet verified.</strong> No suitable reviewed scheme-area location record is available for this exact scheme label. We are not guessing coordinates from the scheme or plot name.</p></section>
{% endif %}
<section class="panel"><h2>Official source records</h2><p class="muted">These links are preserved from collected records; they are not reconstructed by this page.</p>
{% if item.detail_links or item.listing_links %}<div class="links">
{% for link in item.detail_links %}<a href="{{ link }}" target="_blank" rel="noopener noreferrer">{% if loop.first %}Official property details{% else %}Alternate property details{% endif %} ↗</a>{% endfor %}
{% for link in item.listing_links %}<a href="{{ link }}" target="_blank" rel="noopener noreferrer">{% if loop.first %}Official auction listing{% else %}Alternate official listing{% endif %} ↗</a>{% endfor %}
</div>{% else %}<p class="muted">No official source link was captured for this record.</p>{% endif %}</section>
<section class="panel"><h2>Collected source text</h2><p class="muted">Original row text retained for traceability. This is not a substitute for the latest official notice.</p><div class="raw">{{ item.raw_text or 'No original row text captured.' }}</div></section>
</main><footer>Jaipur Land Intelligence · Prototype · Verify current status through official sources.</footer></body></html>
"""


@app.route("/property/<auction_id>")
def property_detail(auction_id):
    try:
        items, _retrieved_at = load_catalogue()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"<h1>Could not load catalogue</h1><pre>{str(exc)}</pre>", 500
    item = next((entry for entry in items if entry["auction_id"] == str(auction_id)), None)
    if item is None:
        staged = load_authority_records("riico") + load_authority_records("rhb") + load_authority_records("jda")
        item = next((entry for entry in staged if entry["auction_id"] == str(auction_id)), None)
    if item is None:
        return "<h1>Property record not found</h1><p>This auction ID is not in the current catalogue.</p><p><a href='/'>Back to listings</a></p>", 404

    try:
        item["map_location"] = load_scheme_location(item["scheme"])
    except RuntimeError as exc:
        return f"<h1>Could not load scheme location</h1><pre>{str(exc)}</pre>", 500
    back_url = "/"
    if request.args:
        from urllib.parse import urlencode
        back_url += "?" + urlencode(request.args)
    return render_template_string(DETAIL_PAGE, item=item, back_url=back_url)



if __name__ == "__main__":
    print("Jaipur Land Intelligence is starting...")
    print("Open http://127.0.0.1:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=False)
