
import csv
import json
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

BASE = "https://udhonline.rajasthan.gov.in"
INPUT = Path("deploy_data/auctions.json")
OUT_DIR = Path("data/location_research/downloads")
MANIFEST = OUT_DIR / "document_manifest.csv"
LIMIT = 10                 # Pilot only; expand after validation
REQUEST_DELAY = 1.2        # Polite delay between requests
MAX_BYTES = 15 * 1024 * 1024
KEYWORDS = ("site plan", "location plan")

class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.documents = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            absolute = urljoin(BASE, attrs["href"])
            parsed = urlparse(absolute)
            if (
                parsed.hostname == urlparse(BASE).hostname
                and parsed.path.lower().endswith("/showdocument")
            ):
                self.current = {"url": absolute, "label": ""}
                self.documents.append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current["label"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None

def get_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != urlparse(BASE).hostname:
        raise ValueError("Blocked non-UDH URL")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "JaipurLandIntelligenceResearch/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("Response exceeded 15 MB safety limit")
        return response.status, response.headers.get("Content-Type", ""), response.geturl(), content

def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")[:80] or "unknown"

data = json.loads(INPUT.read_text(encoding="utf-8"))
rows = data.get("records", data) if isinstance(data, dict) else data
if not isinstance(rows, list):
    raise ValueError("Expected auction records to be a list")

# Preserve first source record for each auction ID.
unique = {}
for row in rows:
    auction_id = str(row.get("auction_id", "")).strip()
    if auction_id:
        unique.setdefault(auction_id, row)

OUT_DIR.mkdir(parents=True, exist_ok=True)
existing = []
if MANIFEST.exists():
    with MANIFEST.open("r", newline="", encoding="utf-8-sig") as f:
        existing = list(csv.DictReader(f))

known = {
    (item.get("auction_id"), item.get("document_url"))
    for item in existing
}
manifest_rows = list(existing)
processed = 0
downloaded = 0
failures = 0

for auction_id, row in list(unique.items())[:LIMIT]:
    links = row.get("detail_links", [])
    if isinstance(links, str):
        try:
            links = json.loads(links)
        except json.JSONDecodeError:
            links = [links]
    if not isinstance(links, list):
        links = []

    detail_url = next(
        (u for u in links if isinstance(u, str) and "/Portal/ViewAuction" in u),
        None
    )
    if not detail_url:
        continue

    scheme = row.get("scheme_name", "")
    print(f"\n[{processed + 1}/{min(LIMIT, len(unique))}] Auction {auction_id} | {scheme}")

    try:
        time.sleep(REQUEST_DELAY)
        _, content_type, _, body = get_url(detail_url)
        if "html" not in content_type.lower():
            print("  Skipped: auction page was not HTML")
            continue

        parser = DocumentParser()
        parser.feed(body.decode("utf-8", errors="replace"))
        targets = [
            doc for doc in parser.documents
            if any(k in doc["label"].lower() for k in KEYWORDS)
        ]
        print("  Location/site plan links:", len(targets))
        processed += 1

        for doc in targets:
            key = (auction_id, doc["url"])
            if key in known:
                print("  Already indexed; skipped")
                continue

            entry = {
                "auction_id": auction_id,
                "scheme_name": scheme,
                "document_label": " ".join(doc["label"].split()),
                "detail_url": detail_url,
                "document_url": doc["url"],
                "http_status": "",
                "content_type": "",
                "file_path": "",
                "result": "",
                "error": "",
            }

            try:
                time.sleep(REQUEST_DELAY)
                status, ctype, final_url, content = get_url(doc["url"])
                entry["http_status"] = status
                entry["content_type"] = ctype

                if urlparse(final_url).hostname != urlparse(BASE).hostname:
                    raise ValueError("Unexpected redirect host")

                if not ctype.lower().startswith("image/"):
                    entry["result"] = "not_an_image"
                    print("  Skipped non-image:", ctype)
                elif not (
                    content.startswith(b"\xff\xd8\xff")
                    or content.startswith(b"\x89PNG\r\n\x1a\n")
                    or content.startswith((b"GIF87a", b"GIF89a"))
                    or content.startswith(b"RIFF") and content[8:12] == b"WEBP"
                ):
                    entry["result"] = "unrecognized_image_signature"
                    print("  Skipped unexpected image signature")
                else:
                    ext = (
                        ".jpg" if content.startswith(b"\xff\xd8\xff")
                        else ".png" if content.startswith(b"\x89PNG")
                        else ".gif" if content.startswith((b"GIF87a", b"GIF89a"))
                        else ".webp"
                    )
                    filename = (
                        f"{safe_name(auction_id)}_"
                        f"{safe_name(entry['document_label'])}{ext}"
                    )
                    path = OUT_DIR / filename
                    path.write_bytes(content)
                    entry["file_path"] = str(path)
                    entry["result"] = "downloaded"
                    downloaded += 1
                    print("  Saved:", filename, f"({len(content):,} bytes)")

            except Exception as error:
                entry["result"] = "failed"
                entry["error"] = f"{type(error).__name__}: {error}"
                failures += 1
                print("  Document error:", entry["error"])

            manifest_rows.append(entry)
            known.add(key)

    except Exception as error:
        failures += 1
        print("  Auction page error:", type(error).__name__, str(error))

fields = [
    "auction_id", "scheme_name", "document_label", "detail_url",
    "document_url", "http_status", "content_type", "file_path",
    "result", "error"
]
with MANIFEST.open("w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(manifest_rows)

print("\nPILOT COMPLETE")
print("Unique auction IDs in source:", len(unique))
print("Auction pages processed:", processed)
print("New images downloaded:", downloaded)
print("Failures:", failures)
print("Manifest:", MANIFEST)
print("Downloads folder:", OUT_DIR)
print("Original auctions.json was not modified.")
print("Database, app, Git, GitHub, and live deployment were not modified.")
