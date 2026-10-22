
import csv
import hashlib
import json
import re
import time
import urllib.request
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

BASE = "https://udhonline.rajasthan.gov.in"
INPUT = Path("deploy_data/auctions.json")
OUT_DIR = Path("data/location_research/downloads_v2")
MANIFEST = OUT_DIR / "document_manifest.csv"
LIMIT = 238
DELAY = 1.2
MAX_BYTES = 15 * 1024 * 1024

KEYWORDS = ("site plan", "location plan")


class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.documents = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href", "")
        if tag == "a" and href:
            url = urljoin(BASE, href)
            parsed = urlparse(url)
            if (
                parsed.scheme == "https"
                and parsed.hostname == urlparse(BASE).hostname
                and parsed.path.lower().endswith("/showdocument")
            ):
                self.current = {"url": url, "label": ""}
                self.documents.append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current["label"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None


def fetch(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != urlparse(BASE).hostname:
        raise ValueError("Blocked URL outside official UDH host")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "JaipurLandIntelligenceResearch/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Response exceeded 15 MB limit")
        final = urlparse(response.geturl())
        if final.hostname != urlparse(BASE).hostname:
            raise ValueError("Unexpected redirect host")
        return response.status, response.headers.get("Content-Type", ""), body


def safe(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")[:70] or "unknown"


def image_extension(body):
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return ".webp"
    return None


data = json.loads(INPUT.read_text(encoding="utf-8"))
rows = data.get("records", data) if isinstance(data, dict) else data
if not isinstance(rows, list):
    raise ValueError("Expected auction records list")

unique = {}
for row in rows:
    aid = str(row.get("auction_id", "")).strip()
    if aid:
        unique.setdefault(aid, row)

OUT_DIR.mkdir(parents=True, exist_ok=True)

fields = [
    "auction_id", "scheme_name", "document_label", "detail_url",
    "document_url", "sha256", "http_status", "content_type",
    "file_path", "result", "error"
]

existing_rows = []
if MANIFEST.exists():
    with MANIFEST.open("r", encoding="utf-8-sig", newline="") as f:
        existing_rows = list(csv.DictReader(f))

# Reuse prior successful document URL mappings.
url_cache = {}
hash_cache = {}
for row in existing_rows:
    if row.get("result") == "downloaded" and row.get("file_path"):
        path = Path(row["file_path"])
        if path.exists():
            url_cache[row.get("document_url", "")] = (
                row.get("sha256", ""), str(path)
            )
            if row.get("sha256"):
                hash_cache[row["sha256"]] = str(path)

known_pairs = {
    (row.get("auction_id"), row.get("document_url"))
    for row in existing_rows
}
all_rows = list(existing_rows)
pages_ok = new_files = reused = failures = 0
seen_docs_this_run = set()

for index, (aid, record) in enumerate(list(unique.items())[:LIMIT], 1):
    links = record.get("detail_links", [])
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

    print(f"\n[{index}/{min(LIMIT, len(unique))}] {aid} | {record.get('scheme_name', '')}")

    try:
        time.sleep(DELAY)
        _, content_type, page = fetch(detail_url)
        if "html" not in content_type.lower():
            print("  Skipped: response is not HTML")
            continue

        parser = DocumentParser()
        parser.feed(page.decode("utf-8", errors="replace"))
        targets = [
            d for d in parser.documents
            if any(word in d["label"].lower() for word in KEYWORDS)
        ]
        pages_ok += 1
        print("  Site/location links:", len(targets))

        for doc in targets:
            url = doc["url"]
            pair = (aid, url)
            if pair in known_pairs or pair in seen_docs_this_run:
                print("  Already indexed:", doc["label"].strip())
                continue
            seen_docs_this_run.add(pair)

            entry = {
                "auction_id": aid,
                "scheme_name": record.get("scheme_name", ""),
                "document_label": " ".join(doc["label"].split()),
                "detail_url": detail_url,
                "document_url": url,
                "sha256": "",
                "http_status": "",
                "content_type": "",
                "file_path": "",
                "result": "",
                "error": "",
            }

            try:
                # Reuse same URL without another network request.
                if url in url_cache and Path(url_cache[url][1]).exists():
                    digest, path = url_cache[url]
                    entry.update(sha256=digest, file_path=path, result="reused_url")
                    reused += 1
                else:
                    time.sleep(DELAY)
                    status, ctype, body = fetch(url)
                    entry.update(http_status=status, content_type=ctype)

                    ext = image_extension(body)
                    if not ctype.lower().startswith("image/") or not ext:
                        entry["result"] = "skipped_not_supported_image"
                    else:
                        digest = hashlib.sha256(body).hexdigest()
                        entry["sha256"] = digest

                        # Exact duplicate content: reuse existing image file.
                        if digest in hash_cache and Path(hash_cache[digest]).exists():
                            path = hash_cache[digest]
                            entry.update(file_path=path, result="reused_hash")
                            reused += 1
                        else:
                            filename = f"{safe(aid)}_{safe(entry['document_label'])}_{digest[:10]}{ext}"
                            path = OUT_DIR / filename
                            path.write_bytes(body)
                            hash_cache[digest] = str(path)
                            new_files += 1
                            entry.update(file_path=str(path), result="downloaded")
                        url_cache[url] = (digest, str(path))

                print(f"  {entry['result']}: {entry['document_label']}")

            except Exception as exc:
                entry["result"] = "failed"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                failures += 1
                print("  ERROR:", entry["error"])

            all_rows.append(entry)
            known_pairs.add(pair)

    except Exception as exc:
        failures += 1
        print("  Auction error:", type(exc).__name__, str(exc))

with MANIFEST.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(all_rows)

print("\nV2 COLLECTION COMPLETE")
print("Unique auction IDs available:", len(unique))
print("Auction IDs selected for this run:", min(LIMIT, len(unique)))
print("Auction pages accessed successfully:", pages_ok)
print("New image files:", new_files)
print("Reused existing images:", reused)
print("Failures:", failures)
print("Manifest rows:", len(all_rows))
print("Manifest:", MANIFEST.resolve())
print("Output folder:", OUT_DIR.resolve())
print("Original collector, existing downloads, database, app and deployment untouched.")
print("Unique auction IDs available:", len(unique))
print("Auction pages accessed:", pages_ok)
print("New image files:", new_files)
print("Reused existing images:", reused)
print("Failures:", failures)
print("Manifest:", MANIFEST.resolve())
print("Output folder:", OUT_DIR.resolve())
print("Original collector, existing downloads, database, app and deployment untouched.")
