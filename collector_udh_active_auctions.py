import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://udhonline.rajasthan.gov.in"
LIST_URL = (
    BASE_URL
    + "/Portal/PortalAuctionList"
    + "?UnitId=22&SchemeId=2750633"
    + "&UsageType=21&IsCorner=0&Flag=R"
)

OUTPUT_DIR = Path("data/udh_test")
OUTPUT_FILE = OUTPUT_DIR / "jda_residential_noncorner_test.json"


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def extract_fields(cell):
    fields = {}

    for li in cell.select("li"):
        label = li.find("b")
        if not label:
            continue

        key = clean(label.get_text(" ", strip=True)).strip().rstrip(":").strip().lower()
        value = clean(li.get_text(" ", strip=True))
        value = re.sub(r"^" + re.escape(label.get_text(" ", strip=True))
                       + r"\s*", "", value).strip()

        if key:
            fields[key] = value

    return fields


def main():
    response = requests.get(
        LIST_URL,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    tables = [
        table for table in soup.find_all("table")
        if "Auction Details" in table.get_text(" ", strip=True)
        and "Property Detail" in table.get_text(" ", strip=True)
    ]

    if len(tables) != 1:
        raise RuntimeError(
            f"Expected exactly one auction table; found {len(tables)}. "
            "Nothing was written."
        )

    table = tables[0]
    rows = table.select("tbody > tr")
    records = []

    for index, row in enumerate(rows):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue

        auction_fields = extract_fields(cells[1])
        property_fields = extract_fields(cells[2])
        emd_fields = extract_fields(cells[3])
        date_fields = extract_fields(cells[4])

        auction_id = auction_fields.get("id", "")
        if not auction_id:
            continue

        detail_link = row.select_one('a[href*="/Portal/ViewAuction"]')
        if not detail_link:
            raise RuntimeError(
                f"Auction {auction_id} has no visible detail link. "
                "Nothing was written."
            )

        assessed_value = None
        next_index = index + 1

        if next_index < len(rows):
            next_text = clean(rows[next_index].get_text(" ", strip=True))
            match = re.search(
                r"Assessed Property Value as per Bid Start Price "
                r"\(BSP\)\s*:\s*([\d,]+(?:\.\d+)?)",
                next_text,
                flags=re.IGNORECASE
            )
            if match:
                assessed_value = float(match.group(1).replace(",", ""))

        record = {
            "source_authority": "Jaipur Development Authority",
            "source_record_type": "active_auction_listing",
            "source_list_url": response.url,
            "official_detail_url": urljoin(
                BASE_URL, detail_link.get("href", "")
            ),
            "auction_id": auction_id,
            "title": auction_fields.get("title"),
            "scheme_name": property_fields.get("scheme name"),
            "property_number": property_fields.get("property number"),
            "area_raw": property_fields.get("property area"),
            "usage_type": property_fields.get("usage type"),
            "emd_amount_raw": emd_fields.get("emd amount (rs.)"),
            "emd_start": emd_fields.get("emd deposit start date"),
            "emd_end": emd_fields.get("emd deposit end date"),
            "bid_start": date_fields.get("bid start date"),
            "bid_end": date_fields.get("bid end date"),
            "assessed_value_inr": assessed_value,
            "retrieved_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "source_http_status": response.status_code,
            "status_note": (
                "Listed by source; current availability and amendments "
                "not independently verified."
            ),
        }

        records.append(record)

    if not records:
        raise RuntimeError(
            "No auction records were extracted. Nothing was written."
        )

    ids = [record["auction_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise RuntimeError(
            "Duplicate auction IDs found in this response. "
            "Nothing was written."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print("SUCCESS: public auction HTML parsed")
    print("HTTP status:", response.status_code)
    print("Records extracted:", len(records))
    print("Auction IDs:", ", ".join(ids))
    print("Output:", OUTPUT_FILE.resolve())

    for record in records:
        print(
            record["auction_id"],
            "|", record["property_number"],
            "|", record["area_raw"],
            "| EMD:", record["emd_amount_raw"],
            "| detail:", record["official_detail_url"]
        )


if __name__ == "__main__":
    main()

