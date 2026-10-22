import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

DATA_FILE = Path("data/udh_test/jda_residential_noncorner_test.json")
IST = timezone(timedelta(hours=5, minutes=30))
now = datetime.now(IST)

REQUIRED = [
    "auction_id", "title", "scheme_name", "property_number",
    "area_raw", "usage_type", "emd_amount_raw", "emd_start",
    "emd_end", "bid_start", "bid_end", "official_detail_url",
]

def parse_dt(value):
    return datetime.strptime(
        value, "%d-%b-%Y %I:%M %p"
    ).replace(tzinfo=IST)

def parse_amount(value):
    cleaned = str(value).replace(",", "").strip()
    amount = float(cleaned)
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return amount

def validate_detail_url(value):
    parsed = urlparse(value or "")
    return (
        parsed.scheme == "https"
        and parsed.hostname == "udhonline.rajasthan.gov.in"
        and parsed.path == "/Portal/ViewAuction"
        and bool(parsed.query)
    )

def deadline_status(deadline, label):
    if now > deadline:
        return f"{label}: passed"
    return f"{label}: not yet passed"

if not DATA_FILE.exists():
    raise SystemExit(f"ERROR: Input file not found: {DATA_FILE}")

records = json.loads(DATA_FILE.read_text(encoding="utf-8"))
if not isinstance(records, list) or not records:
    raise SystemExit("ERROR: Expected a non-empty JSON list of records.")

errors = []
seen_ids = set()

for i, record in enumerate(records, start=1):
    if not isinstance(record, dict):
        errors.append(f"Record {i}: expected a JSON object")
        continue

    missing = [
        key for key in REQUIRED
        if not record.get(key)
    ]
    if missing:
        errors.append(f"Record {i}: missing fields: {missing}")

    auction_id = str(record.get("auction_id", ""))
    if not re.fullmatch(r"\d+", auction_id):
        errors.append(f"Record {i}: invalid auction_id")
    elif auction_id in seen_ids:
        errors.append(f"Record {i}: duplicate auction_id {auction_id}")
    else:
        seen_ids.add(auction_id)

    if not validate_detail_url(record.get("official_detail_url")):
        errors.append(f"Record {i}: unexpected official detail URL")

    try:
        emd = parse_amount(record["emd_amount_raw"])
    except (ValueError, KeyError, TypeError):
        emd = None
        errors.append(f"Record {i}: invalid EMD amount")

    try:
        emd_start = parse_dt(record["emd_start"])
        emd_end = parse_dt(record["emd_end"])
        bid_start = parse_dt(record["bid_start"])
        bid_end = parse_dt(record["bid_end"])

        if emd_end < emd_start:
            errors.append(f"Record {i}: EMD end precedes EMD start")
        if bid_end < bid_start:
            errors.append(f"Record {i}: bid end precedes bid start")

        emd_status = deadline_status(emd_end, "EMD deadline")
        bid_status = deadline_status(bid_end, "Bid deadline")

        if now < bid_start:
            bid_window = "Bidding has not started according to listed dates"
        elif now <= bid_end:
            bid_window = "Within listed bid-date window; availability unverified"
        else:
            bid_window = "Listed bid deadline has passed"

        if now < emd_start:
            emd_window = "EMD period has not started according to listed dates"
        elif now <= emd_end:
            emd_window = "Within listed EMD-date window; payment availability unverified"
        else:
            emd_window = "Listed EMD deadline has passed"

    except (ValueError, KeyError, TypeError):
        errors.append(f"Record {i}: invalid or missing date")
        emd_status = "Unknown"
        bid_status = "Unknown"
        bid_window = "Unknown"
        emd_window = "Unknown"

    print(f"\nAuction ID: {auction_id}")
    print(f"Property: {record.get('title')}")
    print(f"EMD: Rs. {record.get('emd_amount_raw')}")
    print(f"Assessed value: Rs. {record.get('assessed_value_inr')}")
    print(f"{emd_status} | {emd_window}")
    print(f"{bid_status} | {bid_window}")
    print(f"Official detail URL: {record.get('official_detail_url')}")
    print("Source note:", record.get("status_note", "Not provided"))

print("\nVALIDATION RESULT")
print("Records checked:", len(records))
print("Errors:", len(errors))

for error in errors:
    print("-", error)

if errors:
    raise SystemExit(1)

print("PASS: structural, URL, amount and basic date checks succeeded.")
print("IMPORTANT: This does not confirm current availability or eligibility.")
