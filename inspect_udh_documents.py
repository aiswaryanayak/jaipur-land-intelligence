
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
import json

class PageInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        values = " ".join(
            str(attrs.get(key, ""))
            for key in ("id", "href", "onclick", "src", "title", "class")
        )
        if any(word in values.lower() for word in (
            "document", "plan", "showdocument", "download", "pdf"
        )):
            self.elements.append((tag, attrs))

data = json.loads(
    Path("deploy_data/auctions.json").read_text(encoding="utf-8")
)
rows = data.get("records", data) if isinstance(data, dict) else data
record = next(row for row in rows if row.get("detail_links"))
links = record["detail_links"]
if isinstance(links, str):
    links = json.loads(links)

request = urllib.request.Request(
    links[0], headers={"User-Agent": "Mozilla/5.0"}
)

with urllib.request.urlopen(request, timeout=20) as response:
    html = response.read().decode("utf-8", errors="replace")

inspector = PageInspector()
inspector.feed(html)

print("AUCTION:", record.get("auction_id"), record.get("scheme_name"))
print("MATCHING HTML ELEMENTS:", len(inspector.elements))

for tag, attrs in inspector.elements:
    print(f"\nTAG: {tag}")
    for key, value in attrs.items():
        print(f"  {key}: {value}")

print("\nSHOWDOCUMENT CONTEXT:")
for match in re.finditer("showdocument", html, re.IGNORECASE):
    start = max(0, match.start() - 300)
    end = min(len(html), match.end() + 500)
    print(html[start:end].replace("\r", " ").replace("\n", " ")[:900])
    print("-" * 60)
