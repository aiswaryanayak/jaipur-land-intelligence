
from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, urlencode

import hmac
import os
import requests
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
POST_AUCTION_FILE = BASE_DIR / "data" / "jda_post_auction_latest.json"
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
<label for="min_price">Minimum price: <output id="min_price_label">₹{{ "{:,.0f}".format(min_price) }}</output></label>
<input id="min_price" name="min_price" type="range" min="{{ price_min }}" max="{{ price_max }}" step="1" value="{{ min_price }}" aria-describedby="price_filter_help">
<label for="max_price">Maximum price: <output id="max_price_label">₹{{ "{:,.0f}".format(max_price) }}</output></label>
<input id="max_price" name="max_price" type="range" min="{{ price_min }}" max="{{ price_max }}" step="1" value="{{ max_price }}" aria-describedby="price_filter_help">
<p id="price_filter_help" class="muted">Drag either slider to set the range, then select Search listings to apply it.</p>
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
<a href="https://www.google.com/maps/search/?api=1&amp;query={{ (item.scheme ~ ', Jaipur, Rajasthan')|urlencode }}" target="_blank" rel="noopener noreferrer">Find scheme area on Google Maps ↗</a>
{% for link in item.detail_links %}<a href="{{ link }}" target="_blank" rel="noopener">Official details{% if not loop.first %} (alternate){% endif %} ↗</a>{% endfor %}
{% for link in item.listing_links %}<a href="{{ link }}" target="_blank" rel="noopener">{% if loop.first %}Official auction listing{% else %}Additional official listing{% endif %} ↗</a>{% endfor %}
</div>
<details class="raw"><summary>View original source row</summary><p>{{ item.raw_text }}</p></details>
</article>
{% else %}
<div class="empty">No matching records. Try another search or select All property types.</div>
{% endfor %}
</section></main>
<footer>Jaipur Land Intelligence · Prototype · Verify current status through official sources.</footer>
<script>
(() => {
  const minSlider = document.getElementById("min_price");
  const maxSlider = document.getElementById("max_price");
  const minLabel = document.getElementById("min_price_label");
  const maxLabel = document.getElementById("max_price_label");
  if (!minSlider || !maxSlider || !minLabel || !maxLabel) return;

  const rupees = new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: 0,
    useGrouping: true
  });

  function updateLabels(changedSlider) {
    let minValue = Number(minSlider.value);
    let maxValue = Number(maxSlider.value);

    // Keep the range valid while allowing the handle being dragged to lead.
    if (minValue > maxValue) {
      if (changedSlider === minSlider) {
        maxValue = minValue;
        maxSlider.value = String(maxValue);
      } else {
        minValue = maxValue;
        minSlider.value = String(minValue);
      }
    }

    minLabel.textContent = "₹" + rupees.format(minValue);
    maxLabel.textContent = "₹" + rupees.format(maxValue);
    minSlider.setAttribute("aria-valuetext", "Minimum price ₹" + rupees.format(minValue));
    maxSlider.setAttribute("aria-valuetext", "Maximum price ₹" + rupees.format(maxValue));
  }

  minSlider.addEventListener("input", () => updateLabels(minSlider));
  maxSlider.addEventListener("input", () => updateLabels(maxSlider));
  updateLabels(null);
})();
</script>
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
    return float(match.group(0).replace(",", ""))


def _normalized_identity(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _area_number(value):
    match = re.search(r"\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group(0)) if match else None


def _analysis_date(value):
    text_value = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%b-%Y %I:%M %p"):
        try:
            return datetime.strptime(text_value, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _https_url(value):
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    return candidate if parsed.scheme == "https" and parsed.netloc else ""


def _analysis_observation(
    date_value, amount, metric_type, unit, authority, source_url, identity_note,
    source_record_id
):
    parsed_date = _analysis_date(date_value)
    try:
        numeric_amount = float(amount)
    except (TypeError, ValueError):
        numeric_amount = None
    url = _https_url(source_url)
    if parsed_date is None or numeric_amount is None or numeric_amount < 0 or not url:
        return None
    return {
        "date": parsed_date.isoformat(),
        "date_label": parsed_date.astimezone(IST).strftime("%d %b %Y"),
        "amount": numeric_amount,
        "amount_label": f"{numeric_amount:,.2f}",
        "metric_type": metric_type,
        "unit": unit,
        "authority": authority,
        "source_url": url,
        "identity_note": identity_note,
        "source_record_id": source_record_id,
    }


def load_property_history(auction_id, item):
    """Return only dated, linked observations with conservative property identity."""
    observations = []
    current_scheme = _normalized_identity(item.get("scheme"))
    current_plot = _normalized_identity(item.get("property_number"))
    current_area = _area_number(item.get("area"))

    if POST_AUCTION_FILE.exists() and item.get("source_authority") == "Jaipur Development Authority":
        try:
            payload = json.loads(POST_AUCTION_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            payload = []
        if isinstance(payload, list):
            for record in payload:
                if not isinstance(record, dict):
                    continue
                if (
                    _normalized_identity(record.get("scheme_name")) != current_scheme
                    or _normalized_identity(record.get("plot_number")) != current_plot
                    or current_area is None
                    or record.get("area_sqm") is None
                ):
                    continue
                try:
                    historical_area = float(record["area_sqm"])
                except (TypeError, ValueError):
                    continue
                if abs(historical_area - current_area) > 0.01:
                    continue
                observation = _analysis_observation(
                    record.get("auction_date"),
                    record.get("auctioned_rate_inr_per_sqm"),
                    "auctioned rate",
                    "INR per square metre",
                    record.get("source_authority"),
                    record.get("source_url"),
                    "Matched by authority, scheme, plot number, and area.",
                    record.get("record_fingerprint"),
                )
                if observation:
                    observations.append(observation)

    source_paths = []
    snapshot = latest_snapshot_file()
    if snapshot:
        source_paths.append(snapshot)
    elif DATA_FILE.exists():
        source_paths.append(DATA_FILE)
    for source_path in source_paths:
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        records = payload.get("records", []) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or str(record.get("auction_id")) != str(auction_id):
                continue
            amount = numeric_price(record.get("assessed_value"))
            detail_links = record.get("detail_links")
            source_url = (
                detail_links[0]
                if isinstance(detail_links, list) and detail_links
                else record.get("source_url")
            )
            observation = _analysis_observation(
                record.get("retrieved_at_utc"),
                amount,
                "BSP / assessed value",
                "INR total",
                record.get("source_authority") or "Jaipur Development Authority",
                source_url or record.get("source_url"),
                "Matched by exact catalogue auction_id.",
                record.get("raw_row_sha256")
                or f"{auction_id}:{record.get('retrieved_at_utc')}",
            )
            if observation:
                observations.append(observation)

    unique = {
        (
            entry["source_record_id"],
            entry["date"],
            entry["metric_type"],
            entry["unit"],
        ): entry
        for entry in observations
    }
    return sorted(unique.values(), key=lambda entry: entry["date"])


COMPARABLE_LOCALITY_ALIASES = {
    # UDH calls the current locality "TRANSPORT NAGAR YOJANA"; JDA's
    # post-auction rows identify the same locality as Transport Nagar.
    "transportnagaryojana": {
        "transportnagar",
        "transportnagarsikarroad",
    },
}


def _comparable_zone(item):
    if _normalized_identity(item.get("scheme")) == "transportnagaryojana":
        return "zone13"
    match = re.search(r"\bzone\s*[- ]?\s*(\d+)\b", str(item.get("zone_name", "")).casefold())
    return f"zone{match.group(1)}" if match else ""


def _record_zone(record):
    match = re.search(
        r"\bzone\s*[- ]?\s*(\d+)\b",
        str(record.get("zone_name", "")).casefold(),
    )
    return f"zone{match.group(1)}" if match else ""


def audit_comparable_auctions(item):
    """Classify source rows before selecting same-locality comparables."""
    if not POST_AUCTION_FILE.exists():
        return {"included": [], "same_zone_unverified": 0, "excluded": 0}
    try:
        records = json.loads(POST_AUCTION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"included": [], "same_zone_unverified": 0, "excluded": 0}
    if not isinstance(records, list):
        return {"included": [], "same_zone_unverified": 0, "excluded": 0}

    target_category = _normalized_identity(item.get("usage"))
    target_area = _area_number(item.get("area"))
    target_zone = _comparable_zone(item)
    comparables = []
    same_zone_unverified = 0
    excluded = 0
    for record in records:
        if not isinstance(record, dict):
            excluded += 1
            continue
        raw = record.get("raw_source_row")
        raw_scheme = raw.get("scheme") if isinstance(raw, dict) else None
        raw_zone = raw.get("zone_name") if isinstance(raw, dict) else None
        source_zone = _record_zone(record)
        if not source_zone:
            excluded += 1
            continue
        if target_zone and source_zone != target_zone:
            excluded += 1
            continue
        target_locality = _normalized_identity(item.get("scheme"))
        accepted_localities = COMPARABLE_LOCALITY_ALIASES.get(
            target_locality, {target_locality}
        )
        source_locality = _normalized_identity(record.get("scheme_name"))
        if not source_locality:
            same_zone_unverified += 1
            continue
        if source_locality not in accepted_localities:
            excluded += 1
            continue
        if not _normalized_identity(raw_scheme) or not _record_zone({"zone_name": raw_zone}):
            same_zone_unverified += 1
            continue
        if (
            _normalized_identity(raw_scheme) not in accepted_localities
            or _record_zone({"zone_name": raw_zone}) != source_zone
        ):
            excluded += 1
            continue
        source_url = _https_url(record.get("source_url"))
        date = _analysis_date(record.get("auction_date"))
        try:
            amount = float(record.get("auctioned_rate_inr_per_sqm"))
            area = float(record.get("area_sqm"))
        except (TypeError, ValueError):
            continue
        if date is None or not source_url or amount <= 0 or area <= 0 or not raw_zone:
            excluded += 1
            continue
        source_id = str(record.get("record_fingerprint") or "").strip()
        if not source_id:
            excluded += 1
            continue
        category = str(record.get("property_category") or "").strip()
        comparables.append({
            "date": date.isoformat(),
            "date_label": date.astimezone(IST).strftime("%d %b %Y"),
            "amount": amount,
            "amount_label": f"{amount:,.2f}",
            "plot_number": str(record.get("plot_number") or "").strip(),
            "area_sqm": area,
            "area_label": f"{area:,.2f} sqm",
            "category": category or "Not stated",
            "authority": str(record.get("source_authority") or "Jaipur Development Authority"),
            "source_url": source_url,
            "source_record_id": source_id,
            "same_category": bool(target_category and
                                  _normalized_identity(category) == target_category),
            "area_difference": abs(area - target_area) if target_area is not None else None,
        })

    unique = {
        (entry["source_record_id"], entry["date"]): entry
        for entry in comparables
    }
    return {
        "included": sorted(unique.values(), key=lambda entry: (entry["date"], entry["plot_number"])),
        "same_zone_unverified": same_zone_unverified,
        "excluded": excluded,
    }


def load_comparable_auctions(item):
    return audit_comparable_auctions(item)["included"]


def comparable_statistics(comparables):
    amounts = sorted(entry["amount"] for entry in comparables)
    if len(amounts) < 2:
        return None
    midpoint = len(amounts) // 2
    median = amounts[midpoint] if len(amounts) % 2 else (
        amounts[midpoint - 1] + amounts[midpoint]
    ) / 2
    return {
        "count": len(amounts),
        "minimum": min(amounts),
        "median": median,
        "maximum": max(amounts),
    }


def calculate_roi(acquisition_price, additional_costs, resale_value, holding_years=None):
    values = (acquisition_price, additional_costs, resale_value)
    if any(value is None or value < 0 for value in values):
        return None
    total_cost = acquisition_price + additional_costs
    if total_cost <= 0:
        return None
    profit = resale_value - total_cost
    result = {
        "total_cost": total_cost,
        "net_profit": profit,
        "roi_percent": (profit / total_cost) * 100,
        "annualized_roi_percent": None,
    }
    if holding_years is not None and holding_years > 0 and resale_value > 0:
        result["annualized_roi_percent"] = (
            ((resale_value / total_cost) ** (1 / holding_years) - 1) * 100
        )
    return result


def _roi_input(form, name):
    try:
        value = float(form.get(name, "").replace(",", "").strip())
        return value if value >= 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


def build_roi_scenarios(form):
    acquisition = _roi_input(form, "acquisition_price")
    additional = _roi_input(form, "additional_costs")
    resale = _roi_input(form, "expected_resale_value")
    years = _roi_input(form, "holding_years")
    if acquisition is None or additional is None or resale is None:
        return None, {"acquisition_price": acquisition, "additional_costs": additional,
                      "expected_resale_value": resale, "holding_years": years}
    scenarios = []
    for label, multiplier, assumption in (
        ("Pessimistic", 0.90, "90% of the user-entered resale assumption"),
        ("Base", 1.00, "The user-entered resale assumption"),
        ("Optimistic", 1.10, "110% of the user-entered resale assumption"),
    ):
        value = resale * multiplier
        result = calculate_roi(acquisition, additional, value, years)
        result["label"] = label
        result["assumption"] = assumption
        scenarios.append(result)
    return scenarios, {"acquisition_price": acquisition, "additional_costs": additional,
                       "expected_resale_value": resale, "holding_years": years}
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
<div class="links">
<a href="https://www.google.com/maps/search/?api=1&amp;query={{ (item.scheme ~ ', Jaipur, Rajasthan')|urlencode }}" target="_blank" rel="noopener noreferrer">Find scheme area on Google Maps ↗</a>
{% for link in item.detail_links %}<a href="{{ link }}" target="_blank" rel="noopener">{% if loop.first %}Official property details{% else %}Alternate property details{% endif %} ↗</a>{% endfor %}
{% for link in item.listing_links %}<a href="{{ link }}" target="_blank" rel="noopener">{% if loop.first %}Official auction listing{% else %}Additional official listing{% endif %} ↗</a>{% endfor %}
</div>
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
                  AND l.status = 'verified'
                  AND l.precision IN ('approximate scheme-area', 'scheme_area', 'scheme-level')
                  AND l.source_url IS NOT NULL
                  AND trim(l.source_url) <> ''
                  AND l.evidence_notes IS NOT NULL
                  AND lower(trim(l.evidence_notes)) LIKE '%verified%'
                  AND (
                      lower(l.evidence_notes) LIKE '%map%'
                      OR lower(l.evidence_notes) LIKE '%place reference%'
                  )
                  AND l.coordinate_subject IS NOT NULL
                  AND (
                      lower(l.coordinate_subject) LIKE '%scheme%'
                      OR lower(l.coordinate_subject) LIKE '%sector%'
                      OR lower(l.coordinate_subject) LIKE '%area%'
                  )
                  AND l.verification_status IS NOT NULL
                  AND lower(trim(l.verification_status)) LIKE '%verified%'
                  AND lower(l.verification_status) NOT LIKE '%candidate%'
                  AND lower(l.verification_status) NOT LIKE '%provisional%'
                  AND l.confidence IS NOT NULL
                  AND lower(trim(l.confidence)) LIKE 'high%'
                ORDER BY l.id
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
<div class="notice"><strong>Approximate sector/scheme location only — this pin does not identify the exact property, plot boundary, entrance, or legal location.</strong> Confirm the property against the official location plan before visiting or making a decision.</div>
<p><strong>{{ item.map_location.scheme_label }}</strong></p>
<p class="muted">Verification status: {{ item.map_location.status }} / {{ item.map_location.verification_status or "not separately recorded" }} · Confidence: {{ item.map_location.confidence or "not recorded" }} · Coordinates: {{ "%.6f"|format(item.map_location.latitude) }}, {{ "%.6f"|format(item.map_location.longitude) }}</p>
<div class="map-frame"><iframe title="Scheme-area map" loading="lazy" referrerpolicy="no-referrer-when-downgrade" src="https://maps.google.com/maps?q={{ item.map_location.latitude }},{{ item.map_location.longitude }}&z=14&output=embed" allowfullscreen></iframe></div>
<div class="links"><a href="https://www.google.com/maps/search/?api=1&query={{ item.map_location.latitude }},{{ item.map_location.longitude }}" target="_blank" rel="noopener noreferrer">Open approximate area in Google Maps ↗</a><a href="https://www.google.com/maps/dir/?api=1&destination={{ item.map_location.latitude }},{{ item.map_location.longitude }}" target="_blank" rel="noopener noreferrer">Get directions to approximate area ↗</a>{% if item.map_location.source_url %}<a href="{{ item.map_location.source_url }}" target="_blank" rel="noopener noreferrer">Open location source ↗</a>{% endif %}</div>
<p class="muted">{{ item.map_location.evidence_notes }}</p>
</section>
{% else %}
<section class="panel"><h2>Explore the scheme area on Google Maps</h2><p class="muted"><strong>Scheme / area from source:</strong> {{ item.scheme }}</p><p class="muted">This map searches for the recorded scheme name in Jaipur, Rajasthan. Google Maps may show a general area or several possible matches; this is not a verified pin for the exact plot.</p><div class="map-frame"><iframe title="Google Maps search for the recorded scheme area" loading="lazy" referrerpolicy="no-referrer-when-downgrade" src="https://maps.google.com/maps?q={{ (item.scheme ~ ', Jaipur, Rajasthan')|urlencode }}&z=12&output=embed" allowfullscreen></iframe></div><div class="links"><a href="https://www.google.com/maps/search/?api=1&amp;query={{ (item.scheme ~ ', Jaipur, Rajasthan')|urlencode }}" target="_blank" rel="noopener noreferrer">Open this area search in Google Maps ↗</a></div><p class="muted">An exact property pin will only be shown when a verified coordinate is available. Confirm the plot against the official location plan before visiting or making a decision.</p></section>
{% endif %}
<section class="panel"><h2>Official source records</h2><p class="muted">These links are preserved from collected records; they are not reconstructed by this page.</p>
<p><a href="/property/{{ item.auction_id }}/analysis">Open property-specific investment analysis ↗</a></p>
{% if item.detail_links or item.listing_links %}<div class="links">
{% for link in item.detail_links %}<a href="{{ link }}" target="_blank" rel="noopener noreferrer">{% if loop.first %}Official property details{% else %}Alternate property details{% endif %} ↗</a>{% endfor %}
{% for link in item.listing_links %}<a href="{{ link }}" target="_blank" rel="noopener noreferrer">{% if loop.first %}Official auction listing{% else %}Alternate official listing{% endif %} ↗</a>{% endfor %}
</div>{% else %}<p class="muted">No official source link was captured for this record.</p>{% endif %}</section>
<section class="panel"><h2>Collected source text</h2><p class="muted">Original row text retained for traceability. This is not a substitute for the latest official notice.</p><div class="raw">{{ item.raw_text or 'No original row text captured.' }}</div></section>
</main><footer>Jaipur Land Intelligence · Prototype · Verify current status through official sources.</footer></body></html>
"""


ANALYSIS_PAGE = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Investment analysis · {{ item.title }}</title>
<style>
:root{--navy:#173354;--blue:#245b91;--ink:#263447;--muted:#64748b;--line:#dbe3ed;--bg:#f3f6fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,Helvetica,sans-serif}
header{background:var(--navy);color:white;padding:24px max(20px,calc((100% - 1000px)/2))}
main{max-width:1000px;margin:25px auto;padding:0 18px 45px}.panel{background:white;border:1px solid var(--line);border-radius:9px;padding:22px;margin-bottom:16px}
h1{font-size:23px;margin:0}.subtitle,.muted{font-size:13px;color:var(--muted);line-height:1.6}
header .subtitle{color:#dbe6f3;margin-top:6px}h2{font-size:17px;color:var(--navy);margin:0 0 14px}
.notice{background:#fff9e9;border-left:4px solid #e6b951;padding:13px 15px;font-size:13px;line-height:1.6;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 20px}.detail{border-top:1px solid #edf0f4;padding-top:9px}
.detail small{display:block;color:var(--muted);font-size:11px;margin-bottom:5px}.detail strong{display:block;font-size:13px;overflow-wrap:anywhere}
.inputs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}label{font-size:12px;color:var(--muted)}
input,textarea,button{width:100%;font:inherit;padding:10px;border:1px solid #cbd5e1;border-radius:5px;background:white}
button{background:var(--navy);border-color:var(--navy);color:white;font-weight:bold;cursor:pointer}.scenarios{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.scenario{border:1px solid var(--line);border-radius:7px;padding:13px}.scenario h3{color:var(--navy);margin:0 0 8px;font-size:15px}
.links{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}.links a{color:var(--blue);font-size:13px;font-weight:bold;overflow-wrap:anywhere}
.history-table{width:100%;border-collapse:collapse;font-size:12px}.history-table th,.history-table td{text-align:left;border-bottom:1px solid #edf0f4;padding:8px}
.chart{border:1px solid var(--line);border-radius:7px;padding:8px;margin:12px 0;overflow:hidden}.chart svg{display:block;width:100%;height:auto;min-height:240px}.chart circle{cursor:pointer;fill:#245b91;stroke:#fff;stroke-width:1.5}.chart .comparable-point{fill:#8a5a00}.chart .median-line{fill:none;stroke:#173354;stroke-width:2;stroke-dasharray:6 4}.axis-label{fill:var(--muted);font-size:10px}.chart-legend{font-size:12px;color:var(--muted);margin:4px 0 8px}
.assistant-shell{border:1px solid var(--line);border-radius:12px;background:#fbfcfe;overflow:hidden}
.assistant-header{display:flex;align-items:center;gap:12px;padding:16px 18px;background:white;border-bottom:1px solid var(--line)}
.assistant-icon{display:grid;place-items:center;width:36px;height:36px;flex:0 0 36px;border-radius:10px;background:#e8f0f8;color:var(--navy);font-size:20px}
.assistant-title{margin:0;color:var(--navy);font-size:15px}.assistant-subtitle{margin:3px 0 0;color:var(--muted);font-size:12px}
.chat-thread{display:grid;gap:12px;padding:18px;min-height:74px;max-height:520px;overflow-y:auto;scroll-behavior:smooth}
.chat-bubble{max-width:88%;padding:11px 14px;border-radius:14px;font-size:13px;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere}
.chat-bubble.user{justify-self:end;background:var(--navy);color:white;border-bottom-right-radius:4px}
.chat-bubble.assistant{justify-self:start;background:#eef3f8;color:var(--ink);border-bottom-left-radius:4px}
.chat-bubble.error{justify-self:start;background:#fff6e5;border:1px solid #f0d28c;color:#684d13}
.chat-label{display:block;margin-bottom:4px;font-size:10px;font-weight:bold;letter-spacing:.04em;text-transform:uppercase;opacity:.72}
.chat-composer{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:end;padding:14px 18px;background:white;border-top:1px solid var(--line)}
.chat-composer label{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.chat-composer textarea{min-height:46px;max-height:150px;resize:vertical;padding:12px 13px;border-radius:9px;line-height:1.45}
.chat-send{width:auto;min-width:78px;padding:11px 16px;border-radius:9px}
.chat-composer textarea:focus-visible,.chat-send:focus-visible{outline:3px solid #8db7df;outline-offset:2px}
.chat-send:disabled{cursor:wait;opacity:.7}.chat-status{padding:0 18px 13px;color:var(--muted);font-size:12px}
.chat-status[hidden]{display:none}
@media(max-width:650px){.grid,.inputs,.scenarios{grid-template-columns:1fr}.chat-composer{grid-template-columns:1fr}.chat-send{width:100%}.chat-bubble{max-width:94%}}
</style></head><body>
<header><h1>{{ item.title }}</h1><div class="subtitle">Property-specific investment analysis · estimates only</div></header>
<main>
<p><a href="/property/{{ item.auction_id }}">← Return to property details</a></p>
<div class="notice"><strong>Important:</strong> This page reports sourced observations and user-entered scenarios. It is not financial, legal, valuation, or availability advice. No resale prediction is asserted.</div>
<section class="panel"><h2>Property identity</h2><div class="grid">
<div class="detail"><small>Auction ID</small><strong>{{ item.auction_id }}</strong></div>
<div class="detail"><small>Authority</small><strong>{{ item.source_authority }}</strong></div>
<div class="detail"><small>Scheme</small><strong>{{ item.scheme }}</strong></div>
<div class="detail"><small>Property / plot</small><strong>{{ item.property_number }}</strong></div>
</div><div class="links">{% for link in item.detail_links + item.listing_links %}<a href="{{ link }}" target="_blank" rel="noopener noreferrer">Official source ↗</a>{% endfor %}</div></section>
<section class="panel"><h2>Verified dated price observations</h2>
{% if not history %}<p class="muted">No verified property-specific historical observation is available. Records matched by scheme name alone are intentionally excluded.</p>
{% else %}
<p class="muted">Only observations with a dated source URL and conservative identity match are shown. Metrics and units are not interchangeable.</p>
{% for series in chart_series %}<div class="chart"><strong>{{ series.unit }}</strong><svg viewBox="0 0 640 150" role="img" aria-label="Price observations in {{ series.unit }}">
<polyline points="{{ series.polyline }}" fill="none" stroke="#245b91" stroke-width="2"/>
{% for point in series.points %}<circle cx="{{ point.x }}" cy="{{ point.y }}" r="6" data-history-index="{{ point.index }}"><title>{{ point.date_label }} · {{ point.amount_label }} {{ point.unit }}</title></circle>{% endfor %}
</svg></div>{% endfor %}
<div id="history-detail" class="notice">Select a graph point to view its date, amount, metric, unit, authority, and source.</div>
<table class="history-table"><thead><tr><th>Date</th><th>Metric</th><th>Amount</th><th>Source</th></tr></thead><tbody>
{% for point in history %}<tr><td>{{ point.date_label }}</td><td>{{ point.metric_type }}</td><td>{{ point.amount_label }} {{ point.unit }}</td><td><a href="{{ point.source_url }}" target="_blank" rel="noopener noreferrer">Official source ↗</a></td></tr>{% endfor %}
</tbody></table>{% endif %}</section>
<section class="panel"><h2>Comparable Auction Rates</h2>
<p class="muted">These are completed auction rates for other plots whose original JDA source row explicitly identifies the same locality. They are not historical prices for this property and do not establish physical distance or plot proximity. Rates are INR per square metre, sorted by auction date; category and area are shown for comparison.</p>
<div class="notice"><strong>Source audit:</strong> {{ comparable_audit.same_zone_unverified }} same-zone record(s) were not included because their locality could not be verified from the original source row; {{ comparable_audit.excluded }} record(s) were excluded as unrelated or invalid. Only verified same-locality records are graphed and included in statistics.</div>
{% if not comparables %}<p class="muted">No valid, dated, sourced comparable auction results are available for this scheme/area.</p>
{% else %}
{% for series in comparable_series %}<div class="chart"><strong>INR per square metre · {{ series.tier }}</strong><div class="chart-legend">● Individual auction · <span style="color:#173354">━ ━</span> Median rate by auction date{% if series.log_scale %} · Log-scaled visual axis; labels show actual rates{% endif %}</div><svg viewBox="0 0 700 250" role="img" aria-label="Comparable auction rates scatter plot">
{% for tick in series.y_ticks %}<line x1="56" x2="680" y1="{{ tick.y }}" y2="{{ tick.y }}" stroke="#edf0f4"/><text x="2" y="{{ tick.y + 4 }}" class="axis-label">{{ tick.label }}</text>{% endfor %}
<line x1="56" x2="680" y1="205" y2="205" stroke="#94a3b8"/><line x1="56" x2="56" y1="18" y2="205" stroke="#94a3b8"/>
{% if series.median_line %}<polyline class="median-line" points="{% for point in series.median_line %}{{ point.x }},{{ point.y }} {% endfor %}"><title>Median auctioned rate grouped by auction date; not an individual auction price</title></polyline>{% endif %}
{% for point in series.points %}<circle class="comparable-point" cx="{{ point.x }}" cy="{{ point.y }}" r="6" data-comparable-index="{{ point.index }}"><title>{{ point.date_label }} · {{ point.amount_label }} INR per square metre · Plot {{ point.plot_number }}</title></circle>{% endfor %}
{% for tick in series.x_ticks %}<text x="{{ tick.x }}" y="225" text-anchor="middle" class="axis-label">{{ tick.label }}</text>{% endfor %}
</svg></div>{% endfor %}
<div id="comparable-detail" class="notice">Select a graph point to view the comparable plot, area, category, date, rate, authority, and source.</div>
{% if comparable_stats %}<div class="grid"><div class="detail"><small>Sample count</small><strong>{{ comparable_stats.count }}</strong></div><div class="detail"><small>Minimum</small><strong>₹{{ "{:,.2f}".format(comparable_stats.minimum) }}/sqm</strong></div><div class="detail"><small>Median</small><strong>₹{{ "{:,.2f}".format(comparable_stats.median) }}/sqm</strong></div><div class="detail"><small>Maximum</small><strong>₹{{ "{:,.2f}".format(comparable_stats.maximum) }}/sqm</strong></div></div>{% else %}<p class="muted">Statistics require at least two valid comparable observations.</p>{% endif %}
<table class="history-table"><thead><tr><th>Auction date</th><th>Plot</th><th>Area</th><th>Category</th><th>Auctioned rate</th><th>Source</th></tr></thead><tbody>
{% for point in comparables %}<tr><td>{{ point.date_label }}</td><td>{{ point.plot_number }}</td><td>{{ point.area_label }}</td><td>{{ point.category }}</td><td>{{ point.amount_label }} INR/sqm</td><td><a href="{{ point.source_url }}" target="_blank" rel="noopener noreferrer">Official source ↗</a></td></tr>{% endfor %}
</tbody></table>{% endif %}</section>
<section class="panel"><h2>ROI scenario calculator</h2><p class="muted">Enter assumptions below. Total invested cost = acquisition price + additional costs. Net profit = expected resale value − total invested cost. ROI = net profit ÷ total invested cost. Annualized ROI uses compound annualization when holding period is positive.</p>
<form method="post" class="inputs"><label>Acquisition price (INR)<input name="acquisition_price" value="{{ inputs.acquisition_price or '' }}" inputmode="decimal" required></label>
<label>Additional costs (INR)<input name="additional_costs" value="{{ inputs.additional_costs or '' }}" inputmode="decimal" required></label>
<label>Expected resale value (INR)<input name="expected_resale_value" value="{{ inputs.expected_resale_value or '' }}" inputmode="decimal" required></label>
<label>Holding period (years, optional)<input name="holding_years" value="{{ inputs.holding_years or '' }}" inputmode="decimal"></label>
<button type="submit">Calculate estimate scenarios</button></form>
{% if scenarios %}<div class="scenarios">{% for scenario in scenarios %}<div class="scenario"><h3>{{ scenario.label }}</h3><p class="muted">{{ scenario.assumption }}</p><div class="detail"><small>Total invested cost</small><strong>₹{{ "{:,.2f}".format(scenario.total_cost) }}</strong></div><div class="detail"><small>Net profit</small><strong>₹{{ "{:,.2f}".format(scenario.net_profit) }}</strong></div><div class="detail"><small>ROI</small><strong>{{ "{:,.2f}".format(scenario.roi_percent) }}%</strong></div><div class="detail"><small>Annualized ROI</small><strong>{% if scenario.annualized_roi_percent is not none %}{{ "{:,.2f}".format(scenario.annualized_roi_percent) }}%{% else %}Not calculated{% endif %}</strong></div></div>{% endfor %}</div>{% endif %}</section>
<section class="panel"><div class="assistant-shell">
<div class="assistant-header"><span class="assistant-icon" aria-hidden="true">✦</span><div><h2 class="assistant-title">Gemini Research Assistant</h2><p class="assistant-subtitle">Property insights powered by verified data.</p></div></div>
<p class="muted" style="padding:0 18px;margin:16px 0 0">Questions use this property's verified context plus matching local catalogue records. Optional official web research is disclosed when available. Gemini must distinguish sourced facts, calculations, assumptions, and unknowns.</p>
<div class="chat-thread" aria-live="polite">
{% if chat_response %}<div class="chat-bubble assistant"><span class="chat-label">Gemini</span><div class="assistant-content">{{ chat_response }}</div></div>{% endif %}
{% if chat_error %}<div class="chat-bubble error"><span class="chat-label">Assistant notice</span>{{ chat_error }}</div>{% endif %}
{% if not chat_response and not chat_error %}<p class="muted" style="margin:0">Ask a question about this property, its verified comparable records, or the information still requiring due diligence.</p>{% endif %}
</div>
<div class="chat-status" id="chat-status" role="status" aria-live="polite" hidden>Gemini is thinking…</div>
<form method="post" action="/property/{{ item.auction_id }}/analysis/chat" class="chat-composer" id="chat-composer">
<label for="chat-question">Your research question</label><textarea id="chat-question" name="question" rows="2" maxlength="2000" required placeholder="Ask a research question…" aria-describedby="chat-help"></textarea><button class="chat-send" type="submit">Send</button>
</form><div id="chat-help" class="chat-status">Press Enter to send, or use Shift+Enter for a new line.</div>
</div></section>
</main><script>
const history = {{ history|tojson }};
const comparables = {{ comparables|tojson }};
const chatComposer = document.getElementById("chat-composer");
const chatQuestion = document.getElementById("chat-question");
const chatStatus = document.getElementById("chat-status");
if (chatComposer) {
  const thread = document.querySelector(".chat-thread");
  const sendButton = chatComposer.querySelector("button[type='submit']");
  const defaultError = "Gemini could not answer this request right now. Sourced property data remains available above.";

  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[character]));
  const safeUrl = (value) => {
    try {
      const url = new URL(value, window.location.href);
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch (error) {
      return null;
    }
  };
  const inlineMarkdown = (value) => {
    let html = escapeHtml(value);
    const links = [];
    const keepLink = (label, url) => {
      const safe = safeUrl(url);
      if (!safe) return label;
      const token = "\u0000LINK" + links.length + "\u0000";
      links.push('<a href="' + escapeHtml(safe) + '" target="_blank" rel="noopener noreferrer">' +
        label + " ↗</a>");
      return token;
    };
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (match, label, url) => {
      return keepLink(label, url);
    });
    html = html.replace(/(https?:\/\/[^\s<]+)/g, (match) => {
      return keepLink(match, match);
    });
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    return html.replace(/\u0000LINK(\d+)\u0000/g, (match, index) => links[Number(index)]);
  };
  const jsonText = (value) => {
    const parsed = JSON.parse(value);
    if (typeof parsed === "string") return parsed;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const preferred = ["answer", "response", "summary", "message", "text"];
      const preferredValue = preferred.find((key) => typeof parsed[key] === "string");
      if (preferredValue) return parsed[preferredValue];
    }
    const lines = [];
    const visit = (entry, prefix) => {
      if (entry === null || typeof entry !== "object") {
        lines.push((prefix ? prefix + ": " : "") + String(entry));
        return;
      }
      Object.entries(entry).forEach(([key, child]) => {
        const label = prefix ? prefix + " · " + key : key;
        if (child && typeof child === "object") visit(child, label);
        else lines.push(label + ": " + String(child));
      });
    };
    visit(parsed, "");
    return lines.join("\n");
  };
  const readableText = (value) => {
    const trimmed = String(value || "").trim();
    if (!trimmed) return "";
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) ||
        (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        return jsonText(trimmed);
      } catch (error) {
        return trimmed;
      }
    }
    return trimmed;
  };
  const markdownHtml = (value) => {
    const lines = readableText(value).split(/\r?\n/);
    const output = [];
    let list = null;
    const closeList = () => {
      if (list) {
        output.push("</" + list + ">");
        list = null;
      }
    };
    lines.forEach((line) => {
      const heading = line.match(/^\s{0,3}(#{1,3})\s+(.+)$/);
      const unordered = line.match(/^\s*[-*]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (heading) {
        closeList();
        output.push("<h" + heading[1].length + ">" + inlineMarkdown(heading[2]) + "</h" + heading[1].length + ">");
      } else if (unordered || ordered) {
        const type = unordered ? "ul" : "ol";
        if (list !== type) {
          closeList();
          list = type;
          output.push("<" + list + ">");
        }
        output.push("<li>" + inlineMarkdown((unordered || ordered)[1]) + "</li>");
      } else if (line.trim()) {
        closeList();
        output.push("<p>" + inlineMarkdown(line) + "</p>");
      } else {
        closeList();
      }
    });
    closeList();
    return output.join("");
  };
  const renderAssistant = (bubble) => {
    const content = bubble.querySelector(".assistant-content");
    if (content) content.innerHTML = markdownHtml(content.textContent);
  };
  document.querySelectorAll(".chat-bubble.assistant").forEach(renderAssistant);

  chatComposer.addEventListener("submit", async (event) => {
    const thread = document.querySelector(".chat-thread");
    const question = chatQuestion ? chatQuestion.value.trim() : "";
    if (!question || chatComposer.dataset.busy === "true") {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    chatComposer.dataset.busy = "true";
    if (thread) {
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble user";
      const label = document.createElement("span");
      label.className = "chat-label";
      label.textContent = "You";
      bubble.appendChild(label);
      bubble.appendChild(document.createTextNode(question));
      thread.appendChild(bubble);
      thread.scrollTop = thread.scrollHeight;
    }
    if (sendButton) {
      sendButton.disabled = true;
      sendButton.textContent = "Sending…";
    }
    if (chatStatus) {
      chatStatus.hidden = false;
      chatStatus.textContent = "Gemini is thinking…";
    }
    try {
      const response = await fetch(chatComposer.action, {
        method: "POST",
        body: new FormData(chatComposer),
        credentials: "same-origin",
        headers: {"X-Requested-With": "XMLHttpRequest"}
      });
      if (!response.ok) throw new Error("Chat request failed");
      const html = await response.text();
      const parsedPage = new DOMParser().parseFromString(html, "text/html");
      const result = parsedPage.querySelector(".chat-bubble.assistant, .chat-bubble.error");
      if (!result) throw new Error("Chat response was empty");
      const rendered = result.cloneNode(true);
      if (rendered.classList.contains("assistant")) renderAssistant(rendered);
      if (thread) {
        const placeholder = thread.querySelector(".muted");
        if (placeholder) placeholder.remove();
        thread.appendChild(rendered);
        thread.scrollTop = thread.scrollHeight;
      }
      if (chatQuestion) chatQuestion.value = "";
      if (chatStatus) chatStatus.hidden = true;
    } catch (error) {
      chatComposer.dataset.busy = "false";
      if (chatStatus) chatStatus.textContent = defaultError;
      chatComposer.submit();
      return;
    }
    chatComposer.dataset.busy = "false";
    if (sendButton) {
      sendButton.disabled = false;
      sendButton.textContent = "Send";
    }
  });
}
if (chatQuestion) {
  chatQuestion.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (chatComposer && chatComposer.requestSubmit) chatComposer.requestSubmit();
    }
  });
}
document.querySelectorAll("[data-history-index]").forEach((point) => point.addEventListener("click", () => {
  const entry = history[Number(point.dataset.historyIndex)];
  document.getElementById("history-detail").innerHTML =
    "<strong>" + entry.date_label + "</strong> · " + entry.amount_label + " " + entry.unit +
    " · " + entry.metric_type + " · " + entry.authority +
    ' · <a target="_blank" rel="noopener noreferrer" href="' + entry.source_url + '">Original source ↗</a>';
}));
document.querySelectorAll("[data-comparable-index]").forEach((point) => point.addEventListener("click", () => {
  const entry = comparables[Number(point.dataset.comparableIndex)];
  document.getElementById("comparable-detail").innerHTML =
    "<strong>" + entry.date_label + "</strong> · Plot " + entry.plot_number +
    " · " + entry.area_label + " · " + entry.category + " · " +
    entry.amount_label + " INR per square metre · " + entry.authority +
    ' · <a target="_blank" rel="noopener noreferrer" href="' + entry.source_url + '">Original source ↗</a>';
}));
</script></body></html>
"""


def resolve_property(auction_id):
    items, _retrieved_at = load_catalogue()
    item = next((entry for entry in items if entry["auction_id"] == str(auction_id)), None)
    if item is None:
        staged = load_authority_records("riico") + load_authority_records("rhb") + load_authority_records("jda")
        item = next((entry for entry in staged if entry["auction_id"] == str(auction_id)), None)
    return item


def _chart_series(history):
    grouped = defaultdict(list)
    for index, entry in enumerate(history):
        grouped[entry["unit"]].append((index, entry))
    result = []
    for unit, entries in grouped.items():
        amounts = [entry["amount"] for _, entry in entries]
        low, high = min(amounts), max(amounts)
        points = []
        for position, (index, entry) in enumerate(entries):
            x = 30 + (560 * position / max(1, len(entries) - 1))
            ratio = 0.5 if high == low else (entry["amount"] - low) / (high - low)
            y = 125 - (90 * ratio)
            points.append({**entry, "index": index, "x": round(x, 2), "y": round(y, 2)})
        result.append({"unit": unit, "points": points, "polyline": " ".join(
            f"{point['x']},{point['y']}" for point in points
        )})
    return result


def _comparable_chart_series(comparables):
    if not comparables:
        return []
    chronological = sorted(comparables, key=lambda value: value["date"])
    amounts = [entry["amount"] for entry in chronological]
    low, high = min(amounts), max(amounts)
    log_scale = low > 0 and high / low >= 4
    visual_low = math.log(low) if log_scale else low
    visual_high = math.log(high) if log_scale else high
    dates = [datetime.fromisoformat(entry["date"]) for entry in chronological]
    first_date, last_date = min(dates), max(dates)
    date_span = max(1, (last_date - first_date).total_seconds())
    points = []
    occupied = defaultdict(int)
    for index, entry in enumerate(chronological):
        date = dates[index]
        x = 56 + (624 * (date - first_date).total_seconds() / date_span)
        visual_amount = math.log(entry["amount"]) if log_scale else entry["amount"]
        ratio = 0.5 if visual_high == visual_low else (
            (visual_amount - visual_low) / (visual_high - visual_low)
        )
        key = (round(x, 1), round(ratio, 3))
        offset = occupied[key]
        occupied[key] += 1
        x += (offset - 0.5) * 10 if offset else 0
        points.append({**entry, "index": index, "x": round(x, 2), "y": round(205 - (170 * ratio), 2)})
    y_ticks = []
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        visual_value = visual_low + ((visual_high - visual_low) * fraction)
        value = math.exp(visual_value) if log_scale else visual_value
        y_ticks.append({"y": round(205 - (170 * fraction), 2), "label": f"{value:,.0f}"})
    tick_indexes = sorted(set([0, len(chronological) // 2, len(chronological) - 1]))
    x_ticks = [
        {"x": points[index]["x"], "label": dates[index].astimezone(IST).strftime("%d %b %y")}
        for index in tick_indexes
    ]
    median_line = []
    grouped = defaultdict(list)
    for entry, date in zip(chronological, dates):
        grouped[date.date()].append(entry["amount"])
    if len(chronological) >= 3 and len(grouped) >= 2:
        for group_date, group_amounts in sorted(grouped.items()):
            group_datetime = datetime.combine(
                group_date, datetime.min.time(), tzinfo=timezone.utc
            )
            x = 56 + (624 * (group_datetime - first_date).total_seconds() / date_span)
            median_amount = statistics.median(group_amounts)
            visual_amount = math.log(median_amount) if log_scale else median_amount
            ratio = (
                (visual_amount - visual_low) / (visual_high - visual_low)
                if visual_high != visual_low else 0.5
            )
            median_line.append({
                "x": round(x, 2),
                "y": round(205 - (170 * ratio), 2),
            })
    return [{"points": points, "x_ticks": x_ticks, "y_ticks": y_ticks,
             "median_line": median_line, "log_scale": log_scale,
             "tier": "Verified same locality"}]


def _catalogue_matches(question, limit=12):
    try:
        items, retrieved_at = load_catalogue()
    except (OSError, ValueError, json.JSONDecodeError):
        return [], "Catalogue unavailable"
    words = {
        word for word in re.findall(r"[a-z0-9]+", question.casefold())
        if len(word) > 2
    }
    scored = []
    for item in items:
        haystack = " ".join(
            str(item.get(key, "")) for key in
            ("auction_id", "title", "scheme", "property_number", "area", "usage")
        ).casefold()
        score = sum(word in haystack for word in words)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda value: (-value[0], value[1]["auction_id"]))
    return scored[:limit], retrieved_at


def _official_web_research(question):
    if os.environ.get("GEMINI_WEB_RESEARCH", "").strip().lower() not in {"1", "true", "yes"}:
        return [], "Live web research is not enabled; using local verified data only."
    sources = [
        "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction",
        "https://udhonline.rajasthan.gov.in/Portal/AuctionListNew",
    ]
    evidence = []
    for url in sources:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            text_content = re.sub(r"\s+", " ", response.text)
            evidence.append({
                "url": url,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "content": text_content[:12000],
            })
        except requests.RequestException:
            continue
    if not evidence:
        return [], "Live official web research was unavailable; no web search is claimed."
    return evidence, "Official web pages retrieved server-side for this request."


def _gemini_answer(item, history, comparables, scenarios, inputs, question):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None, "Gemini assistant is not configured. Set GEMINI_API_KEY on the server."
    if not question or len(question) > 2000:
        return None, "Please provide a question of at most 2,000 characters."
    matches, catalogue_retrieved_at = _catalogue_matches(question)
    web_evidence, web_status = _official_web_research(question)
    context = {
        "property": {
            key: item.get(key)
            for key in ("auction_id", "title", "scheme", "property_number", "area",
                        "usage", "assessed_value", "source_authority")
        },
        "official_links": list(dict.fromkeys(item.get("detail_links", []) + item.get("listing_links", []))),
        "verified_observations": history,
        "verified_comparable_auctions_other_properties": comparables,
        "roi_inputs": inputs,
        "roi_scenarios": scenarios or [],
        "catalogue_matches": [
            {
                key: match[1].get(key)
                for key in ("auction_id", "title", "scheme", "property_number",
                            "area", "usage", "assessed_value", "detail_links",
                            "source_authority")
            }
            for match in matches
        ],
        "catalogue_retrieved_at": catalogue_retrieved_at,
        "web_research_status": web_status,
        "official_web_evidence": web_evidence,
    }
    prompt = (
        "Answer about the user's question using only the supplied context. "
        "Distinguish sourced facts, calculated results, user assumptions, and unknowns. "
        "Comparable auctions are other properties, not this property's history. "
        "Never invent historical prices, citations, availability, distances, or legal/financial advice. "
        "Do not claim web research unless official_web_evidence is non-empty. "
        "Mention official links when relevant and explain missing evidence. Context:\n"
        + json.dumps(context, ensure_ascii=False)
        + "\nQuestion:\n" + question
    )
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    if not model or "/" in model or ":" in model:
        return None, "Gemini assistant configuration is invalid. Sourced property data remains available above."
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    try:
        response = requests.post(
            endpoint, params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Gemini response has no candidates")
        candidate = candidates[0]
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list) or not parts:
            raise ValueError("Gemini response has no content parts")
        answer = parts[0].get("text") if isinstance(parts[0], dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Gemini response has no answer text")
        return answer.strip()[:8000], None
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None, "Gemini could not answer this request right now. Sourced property data remains available above."


@app.route("/property/<auction_id>")
def property_detail(auction_id):
    try:
        item = resolve_property(auction_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"<h1>Could not load catalogue</h1><pre>{str(exc)}</pre>", 500
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


@app.route("/property/<auction_id>/analysis", methods=["GET", "POST"])
def property_analysis(auction_id):
    try:
        item = resolve_property(auction_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"<h1>Could not load property</h1><pre>{str(exc)}</pre>", 500
    if item is None:
        return "<h1>Property record not found</h1><p><a href='/'>Back to property listings</a></p>", 404
    history = load_property_history(auction_id, item)
    comparable_audit = audit_comparable_auctions(item)
    comparables = comparable_audit["included"]
    scenarios, inputs = build_roi_scenarios(request.form if request.method == "POST" else {})
    return render_template_string(
        ANALYSIS_PAGE, item=item, history=history, chart_series=_chart_series(history),
        comparables=comparables, comparable_series=_comparable_chart_series(comparables),
        comparable_stats=comparable_statistics(comparables), comparable_audit=comparable_audit,
        scenarios=scenarios, inputs=inputs, chat_response=None, chat_error=None,
    )


@app.route("/property/<auction_id>/analysis/chat", methods=["POST"])
def property_analysis_chat(auction_id):
    try:
        item = resolve_property(auction_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"<h1>Could not load property</h1><pre>{str(exc)}</pre>", 500
    if item is None:
        return "<h1>Property record not found</h1><p><a href='/'>Back to property listings</a></p>", 404
    history = load_property_history(auction_id, item)
    comparable_audit = audit_comparable_auctions(item)
    comparables = comparable_audit["included"]
    scenarios, inputs = build_roi_scenarios(request.form)
    answer, error = _gemini_answer(
        item, history, comparables, scenarios, inputs, request.form.get("question", "").strip()
    )
    return render_template_string(
        ANALYSIS_PAGE, item=item, history=history, chart_series=_chart_series(history),
        comparables=comparables, comparable_series=_comparable_chart_series(comparables),
        comparable_stats=comparable_statistics(comparables), comparable_audit=comparable_audit,
        scenarios=scenarios, inputs=inputs, chat_response=answer, chat_error=error,
    )



if __name__ == "__main__":
    print("Jaipur Land Intelligence is starting...")
    print("Open http://127.0.0.1:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=False)
