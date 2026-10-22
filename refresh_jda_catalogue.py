
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
TEST_DIR = BASE_DIR / "data" / "udh_test"
DISCOVERY_FILE = TEST_DIR / "jda_scheme_discovery.json"
OUTPUT_DIR = TEST_DIR / "refresh_snapshots"

ALLOWED_HOST = "udhonline.rajasthan.gov.in"
TIMEOUT = 30
MAX_SCHEMES_PER_RUN = 246
PAUSE_SECONDS = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JaipurLandIntelligencePrototype/0.1)"
}

DATE_PATTERN = (
    r"\d{1,2}-[A-Za-z]{3}-\d{4}\s+"
    r"\d{1,2}:\d{2}\s+[APap][Mm]"
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def labelled_value(row_text, label, next_labels):
    """Extract a labelled value up to the next known column label."""
    following = "|".join(re.escape(x) for x in next_labels)
    pattern = (
        r"(?is)"
        + re.escape(label)
        + r"\s*:\s*(.*?)\s+(?="
        + following
        + r")"
    )
    match = re.search(pattern, row_text)
    return clean(match.group(1)) if match else ""


def extract_date(row_text, label):
    pattern = (
        re.escape(label)
        + r"\s*:\s*("
        + DATE_PATTERN
        + r")"
    )
    match = re.search(pattern, row_text, re.IGNORECASE)
    return clean(match.group(1)) if match else ""


def extract_assessed_value(next_row_text):
    pattern = (
        r"Assessed Property Value as per Bid Start Price\s*"
        r"\(BSP\)\s*:\s*(.*?)"
        r"(?=\s*\(Actual value|$)"
    )
    match = re.search(pattern, next_row_text, re.IGNORECASE)
    return clean(match.group(1)) if match else ""


def official_url(href, page_url):
    """Keep exact links only when they belong to the official UDH host."""
    if not href:
        return ""

    absolute = urljoin(page_url, href)
    parsed = urlparse(absolute)

    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != ALLOWED_HOST
    ):
        return ""

    return absolute


def parse_page(html, page_url, scheme_entry, retrieved_at):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")

    if table is None:
        raise ValueError("No auction table found in response")

    rows = table.find_all("tr")
    records = []

    for row_index, row in enumerate(rows):
        cells = row.find_all("td", recursive=False)

        # Auction records contain multiple data cells; headings do not.
        if len(cells) < 5:
            continue

        row_text = clean(row.get_text(" ", strip=True))
        auction_match = re.search(
            r"\bId\s*:\s*(\d+)",
            row_text,
            re.IGNORECASE,
        )

        if not auction_match:
            continue

        auction_id = auction_match.group(1)

        title_match = re.search(
            r"\bTitle\s*:\s*(.*?)(?=\s+Scheme\s*Name\s*:)",
            row_text,
            re.IGNORECASE,
        )
        title = clean(title_match.group(1)) if title_match else ""

        scheme = labelled_value(
            row_text,
            "Scheme Name",
            ["Property Number", "PropertyNumber", "Property Area"],
        )

        # Accept both "Property Number" and "PropertyNumber".
        property_match = re.search(
            r"Property\s*Number\s*:\s*(.*?)"
            r"\s+Property\s*Area\s*:",
            row_text,
            re.IGNORECASE,
        )
        property_number = (
            clean(property_match.group(1)) if property_match else ""
        )

        area = labelled_value(
            row_text,
            "Property Area",
            ["Usage Type"],
        )

        usage = labelled_value(
            row_text,
            "Usage Type",
            ["EMD Deposit Start Date"],
        )

        emd_start = extract_date(
            row_text, "EMD Deposit Start Date"
        )
        emd_end = extract_date(
            row_text, "EMD Deposit End Date"
        )

        emd_amount = labelled_value(
            row_text,
            "EMD Amount (Rs.)",
            ["Bid Start Date"],
        )

        bid_start = extract_date(row_text, "Bid Start Date")
        bid_end = extract_date(row_text, "Bid End Date")

        # The assessed value is published in the next table row.
        next_row_text = ""
        if row_index + 1 < len(rows):
            next_row_text = clean(
                rows[row_index + 1].get_text(" ", strip=True)
            )

        assessed_value = extract_assessed_value(next_row_text)

        detail_links = []
        for anchor in row.find_all("a", href=True):
            label = clean(
                anchor.get_text(" ", strip=True)
            ).lower()

            url = official_url(anchor["href"], page_url)

            if url and (
                "view details" in label
                or label == "view"
            ):
                detail_links.append(url)

        records.append({
            "source_authority": "Jaipur Development Authority",
            "source_url": page_url,
            "report_url": scheme_entry.get("report_url", ""),
            "catalog_scheme_name": scheme_entry.get("scheme_name", ""),
            "catalogue_reported_count": scheme_entry.get(
                "reported_auction_count"
            ),
            "retrieved_at_utc": retrieved_at,
            "http_status": 200,
            "auction_id": auction_id,
            "title": title,
            "scheme_name": scheme,
            "property_number": property_number,
            "property_area": area,
            "usage_type": usage,
            "emd_start": emd_start,
            "emd_end": emd_end,
            "emd_amount": emd_amount,
            "bid_start": bid_start,
            "bid_end": bid_end,
            "assessed_value": assessed_value,
            "detail_links": list(dict.fromkeys(detail_links)),
            "raw_row_text": row_text,
            "raw_assessed_value_row_text": next_row_text,
            "raw_row_sha256": hashlib.sha256(
                row_text.encode("utf-8")
            ).hexdigest(),
        })

    page_title = (
        clean(soup.title.get_text())
        if soup.title
        else ""
    )

    return records, page_title


def main():
    if not DISCOVERY_FILE.exists():
        print(f"Discovery file not found: {DISCOVERY_FILE}")
        return 1

    try:
        discovery = json.loads(
            DISCOVERY_FILE.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read discovery file: {exc}")
        return 1

    entries = discovery.get("scheme_entries", [])

    if not entries:
        print("No scheme entries found.")
        return 1

    # Intentionally limited to one scheme until the parser is validated.
    entries = entries[:MAX_SCHEMES_PER_RUN]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    retrieved_at = utc_now()
    all_records = []
    errors = []

    session = requests.Session()
    session.headers.update(HEADERS)

    for number, entry in enumerate(entries, start=1):
        url = entry.get("official_listing_url", "")
        parsed = urlparse(url)

        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != ALLOWED_HOST
        ):
            errors.append({
                "scheme_name": entry.get("scheme_name"),
                "url": url,
                "error": "Rejected URL: not the approved official host",
            })
            continue

        print(
            f"Testing scheme {number}/{len(entries)}: "
            f"{entry.get('scheme_name')}"
        )
        print("Requesting official listing page...")

        try:
            response = session.get(url, timeout=TIMEOUT)

            if response.status_code != 200:
                errors.append({
                    "scheme_name": entry.get("scheme_name"),
                    "url": url,
                    "http_status": response.status_code,
                    "error": f"HTTP {response.status_code}",
                })
                print(
                    f"HTTP {response.status_code}; "
                    "not retrying automatically."
                )
                continue

            records, page_title = parse_page(
                response.text,
                url,
                entry,
                retrieved_at,
            )

            if not records:
                errors.append({
                    "scheme_name": entry.get("scheme_name"),
                    "url": url,
                    "error": (
                        "HTTP 200 received, but no auction rows "
                        "were parsed"
                    ),
                    "page_title": page_title,
                })
                print("No auction rows parsed.")
                continue

            for record in records:
                record["page_title"] = page_title

            all_records.extend(records)
            print(f"Parsed auction records: {len(records)}")
            print(f"Page title: {page_title}")

        except (requests.RequestException, ValueError) as exc:
            errors.append({
                "scheme_name": entry.get("scheme_name"),
                "url": url,
                "error": str(exc),
            })
            print(f"Request or parsing error: {exc}")

        time.sleep(PAUSE_SECONDS)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = OUTPUT_DIR / f"jda_refresh_{stamp}.json"
    errors_path = OUTPUT_DIR / f"jda_refresh_errors_{stamp}.json"

    snapshot = {
        "source_authority": (
            "Jaipur Development Authority via Rajasthan UDH portal"
        ),
        "retrieved_at_utc": retrieved_at,
        "test_scope": (
            "First catalogue scheme only; not a complete refresh"
        ),
        "catalogue_entries_attempted": len(entries),
        "parsed_record_count": len(all_records),
        "records": all_records,
    }

    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    errors_path.write_text(
        json.dumps(errors, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nTEST COMPLETE")
    print(f"Parsed records: {len(all_records)}")
    print(f"Errors: {len(errors)}")
    print(f"Snapshot: {snapshot_path}")
    print(f"Errors file: {errors_path}")
    print("SQLite and the existing catalogue were not modified.")

    if all_records:
        print("\nFirst parsed record:")
        print(
            json.dumps(
                all_records[0],
                ensure_ascii=False,
                indent=2,
            )
        )

    return 0 if all_records else 1


if __name__ == "__main__":
    sys.exit(main())

