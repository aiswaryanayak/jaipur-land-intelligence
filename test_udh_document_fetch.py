
import json
import urllib.request
from pathlib import Path
from urllib.parse import urljoin
from html.parser import HTMLParser

BASE = "https://udhonline.rajasthan.gov.in"

class DocumentLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href", "").startswith("/Portal/ShowDocument"):
            self.current = {
                "url": urljoin(BASE, attrs["href"]),
                "label": ""
            }
            self.links.append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current["label"] += data.strip()

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None

data = json.loads(
    Path("deploy_data/auctions.json").read_text(encoding="utf-8")
)
rows = data.get("records", data) if isinstance(data, dict) else data
record = next(row for row in rows if row.get("detail_links"))
detail_links = record["detail_links"]

if isinstance(detail_links, str):
    detail_links = json.loads(detail_links)

request = urllib.request.Request(
    detail_links[0],
    headers={"User-Agent": "Mozilla/5.0"}
)

with urllib.request.urlopen(request, timeout=20) as response:
    page = response.read().decode("utf-8", errors="replace")

parser = DocumentLinks()
parser.feed(page)

print("AUCTION:", record.get("auction_id"), record.get("scheme_name"))
print("DOCUMENT LINKS FOUND:", len(parser.links))

# Test only the first site/location document.
target = next(
    (item for item in parser.links
     if "site plan" in item["label"].lower()
     or "location plan" in item["label"].lower()),
    None
)

if target is None:
    print("No site/location plan link found.")
else:
    print("DOCUMENT LABEL:", target["label"])
    print("DOCUMENT URL:", target["url"])

    req = urllib.request.Request(
        target["url"],
        headers={"User-Agent": "Mozilla/5.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()
            print("HTTP STATUS:", response.status)
            print("CONTENT TYPE:", response.headers.get("Content-Type"))
            print("FINAL URL:", response.geturl())
            print("SIZE BYTES:", len(content))
            print("FILE SIGNATURE:", repr(content[:20]))

    except Exception as error:
        print("DOCUMENT REQUEST FAILED:", type(error).__name__, str(error))
