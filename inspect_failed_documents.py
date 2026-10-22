import csv
from pathlib import Path
from collections import Counter

manifest = Path("data/location_research/downloads_v2/document_manifest.csv")

with manifest.open("r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

failed = [r for r in rows if r.get("result") == "failed"]

print("Failed document records:", len(failed))
print("Unique failed URLs:", len({r.get("document_url", "") for r in failed}))
print("\nFailed documents by scheme:")
for scheme, count in Counter(r.get("scheme_name", "") for r in failed).most_common():
    print(f"  {scheme}: {count}")

print("\nUnique URLs to investigate:")
seen = set()
for r in failed:
    url = r.get("document_url", "")
    if url and url not in seen:
        seen.add(url)
        print(f"{r.get('auction_id')} | {r.get('document_label')} | {url}")
