import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://udhonline.rajasthan.gov.in"
SUMMARY_URL = BASE + "/Portal/AuctionListNew"
OUT_DIR = Path("data/udh_test")
OUT_FILE = OUT_DIR / "jda_scheme_discovery.json"

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()

def get_page(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "udhonline.rajasthan.gov.in":
        raise ValueError(f"Unexpected external URL: {url}")

    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response

summary_response = get_page(SUMMARY_URL)
summary = BeautifulSoup(summary_response.text, "html.parser")

jda_row = next(
    (
        row for row in summary.find_all("tr")
        if "Jaipur Development Authority" in clean(row.get_text(" ", strip=True))
    ),
    None
)

if jda_row is None:
    raise RuntimeError("JDA summary row not found. No output written.")

report_links = []
for cell in jda_row.find_all("td"):
    for link in cell.find_all("a", href=True):
        url = urljoin(summary_response.url, link["href"])
        if "/Portal/LiveAuctionDetailReport" in url:
            report_links.append({
                "summary_count_label": clean(link.get_text(" ", strip=True)),
                "report_url": url,
            })

if len(report_links) != 7:
    raise RuntimeError(
        f"Expected 7 JDA category reports, found {len(report_links)}. "
        "No output written."
    )

reports = []
all_scheme_entries = []
errors = []

for report_index, report_link in enumerate(report_links, start=1):
    print(f"Reading report {report_index}/7...")

    try:
        response = get_page(report_link["report_url"])
        page = BeautifulSoup(response.text, "html.parser")
        tables = page.find_all("table")

        if len(tables) != 1:
            raise RuntimeError(f"Expected 1 table; found {len(tables)}")

        rows = tables[0].find_all("tr")
        scheme_entries = []

        for row in rows:
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 3:
                continue

            values = [clean(cell.get_text(" ", strip=True)) for cell in cells]
            if values[0].lower() in {"s.no.", "s.no", "s. no."}:
                continue

            # Scheme report rows have: serial number, scheme name, auction count.
            if not values[2].isdigit():
                continue

            listing_links = [
                urljoin(response.url, a["href"])
                for a in row.find_all("a", href=True)
                if "/Portal/PortalAuctionList" in a["href"]
            ]

            if len(listing_links) != 1:
                errors.append(
                    f"Report {report_index}, scheme {values[1]!r}: "
                    f"expected one listing link, found {len(listing_links)}"
                )
                continue

            entry = {
                "report_url": report_link["report_url"],
                "scheme_name": values[1],
                "reported_auction_count": int(values[2]),
                "official_listing_url": listing_links[0],
            }
            scheme_entries.append(entry)

        expected = int(report_link["summary_count_label"])
        calculated = sum(
            entry["reported_auction_count"] for entry in scheme_entries
        )

        report_record = {
            **report_link,
            "http_status": response.status_code,
            "scheme_rows_extracted": len(scheme_entries),
            "summary_count": expected,
            "sum_of_scheme_counts": calculated,
            "counts_reconcile": expected == calculated,
        }

        reports.append(report_record)
        all_scheme_entries.extend(scheme_entries)

        if expected != calculated:
            errors.append(
                f"Report {report_index}: summary={expected}, "
                f"scheme-count sum={calculated}"
            )

        time.sleep(0.25)

    except Exception as exc:
        errors.append(f"Report {report_index}: {type(exc).__name__}: {exc}")

if errors:
    print("\nDISCOVERY CHECKS NEED REVIEW:")
    for error in errors:
        print("-", error)

    # Do not replace a previous successful catalog with incomplete data.
    raise SystemExit(
        "Discovery incomplete; no catalog written. Review errors above."
    )

result = {
    "source_authority": "Jaipur Development Authority",
    "source_summary_url": SUMMARY_URL,
    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
    "report_count": len(reports),
    "scheme_entry_count": len(all_scheme_entries),
    "reports": reports,
    "scheme_entries": all_scheme_entries,
    "note": (
        "Counts are as displayed by the official portal. "
        "They are not independently verified actionable-auction counts."
    ),
}

OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE.write_text(
    json.dumps(result, indent=2, ensure_ascii=False),
    encoding="utf-8"
)

print("\nDISCOVERY COMPLETE")
print("Reports:", len(reports))
print("Scheme entries:", len(all_scheme_entries))
print("Sum of category totals:", sum(r["summary_count"] for r in reports))
print("Catalog:", OUT_FILE.resolve())

for report in reports:
    print(
        "Category count:", report["summary_count"],
        "| Schemes:", report["scheme_rows_extracted"],
        "| Sum of scheme counts:", report["sum_of_scheme_counts"],
        "| Reconciles:", report["counts_reconcile"],
    )
