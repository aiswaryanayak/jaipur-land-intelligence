import csv
import time
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

BASE_HOST = "udhonline.rajasthan.gov.in"
MANIFEST = Path("data/location_research/downloads_v2/document_manifest.csv")
OUT = Path("data/location_research/downloads_v2/failed_url_headers.csv")

with MANIFEST.open("r", encoding="utf-8-sig", newline="") as f:
    rows = [r for r in csv.DictReader(f) if r.get("result") == "failed"]

results = []
for i, row in enumerate(rows, 1):
    url = row.get("document_url", "")
    result = {
        "auction_id": row.get("auction_id", ""),
        "scheme_name": row.get("scheme_name", ""),
        "document_url": url,
        "status": "",
        "content_type": "",
        "content_length": "",
        "note": "",
    }

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != BASE_HOST:
        result["note"] = "BLOCKED: non-official host"
        results.append(result)
        continue

    try:
        req = urllib.request.Request(
            url, method="HEAD",
            headers={"User-Agent": "JaipurLandIntelligenceResearch/1.0"}
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            result["status"] = response.status
            result["content_type"] = response.headers.get("Content-Type", "")
            result["content_length"] = response.headers.get("Content-Length", "")
            result["note"] = "HEAD response"
    except Exception as exc:
        result["note"] = f"HEAD unavailable: {type(exc).__name__}: {exc}"

    results.append(result)
    print(f"{i}/{len(rows)} | {result['auction_id']} | {result['status']} | "
          f"{result['content_type']} | {result['content_length']} | {result['note']}")
    time.sleep(1.2)

with OUT.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(results[0].keys()) if results else [])
    if results:
        writer.writeheader()
        writer.writerows(results)

print(f"\nHeader diagnostic saved to: {OUT}")
print("No document bodies were intentionally downloaded or saved.")
