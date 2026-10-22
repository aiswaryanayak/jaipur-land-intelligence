import urllib.request
import urllib.error
from urllib.parse import urlparse
import csv
from pathlib import Path

manifest = Path("data/location_research/downloads_v2/document_manifest.csv")
with manifest.open("r", encoding="utf-8-sig", newline="") as f:
    row = next(r for r in csv.DictReader(f)
               if r.get("result") == "failed" and r.get("auction_id") == "44885")

url = row["document_url"]
if urlparse(url).scheme != "https" or urlparse(url).hostname != "udhonline.rajasthan.gov.in":
    raise SystemExit("Blocked: URL is not on the official UDH HTTPS host.")

req = urllib.request.Request(
    url,
    headers={
        "User-Agent": "JaipurLandIntelligenceResearch/1.0",
        "Range": "bytes=0-1023",
    },
    method="GET",
)

try:
    response = urllib.request.urlopen(req, timeout=15)
except urllib.error.HTTPError as exc:
    response = exc

try:
    print("HTTP status:", response.status)
    print("Content-Type:", response.headers.get("Content-Type", ""))
    print("Content-Length:", response.headers.get("Content-Length", "not provided"))
    print("Content-Range:", response.headers.get("Content-Range", "not provided"))
    print("Accept-Ranges:", response.headers.get("Accept-Ranges", "not provided"))
    sample = response.read(1024)
    print("Bytes inspected:", len(sample))
    print("First bytes (hex):", sample[:24].hex())
finally:
    response.close()
