
import csv
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

BASE_HOST = "udhonline.rajasthan.gov.in"
ROOT = Path("data/location_research/downloads_v2")
MANIFEST = ROOT / "recovery_manifest.csv"

TARGET_IDS = {
    "44955", "44734", "44883",
    "44732", "44735", "44733",
    "44736", "44816", "44834",
}

def main():
    with MANIFEST.open(
        "r", encoding="utf-8-sig", newline=""
    ) as f:
        rows = list(csv.DictReader(f))

    targets = [
        row for row in rows
        if row.get("auction_id") in TARGET_IDS
        and row.get("result") == "failed"
    ]

    seen = set()

    for row in targets:
        url = row.get("document_url", "")

        if not url or url in seen:
            continue
        seen.add(url)

        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != BASE_HOST:
            print(row["auction_id"], "SKIPPED: unexpected URL")
            continue

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "JaipurLandIntelligenceResearch/1.0"
            },
        )

        try:
            with urllib.request.urlopen(
                request, timeout=30
            ) as response:
                final = urlparse(response.geturl())

                if (
                    final.scheme != "https"
                    or final.hostname != BASE_HOST
                ):
                    print(row["auction_id"], "SKIPPED: redirect")
                    continue

                length = response.headers.get(
                    "Content-Length", ""
                )
                content_type = response.headers.get(
                    "Content-Type", ""
                )

                if length.isdigit():
                    size = int(length) / (1024 * 1024)
                    print(
                        f"{row['auction_id']} | "
                        f"{size:.2f} MiB | {content_type}"
                    )
                else:
                    print(
                        f"{row['auction_id']} | "
                        f"size unavailable | {content_type}"
                    )

                # Close response without reading its body.

        except Exception as exc:
            print(
                f"{row['auction_id']} | "
                f"{type(exc).__name__}: {exc}"
            )

        time.sleep(1.2)

    print("\nDiagnostic finished. No image bodies were downloaded.")

if __name__ == "__main__":
    main()
