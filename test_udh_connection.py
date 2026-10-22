
import json
import urllib.request
from pathlib import Path

data = json.loads(
    Path("deploy_data/auctions.json").read_text(encoding="utf-8")
)
rows = data.get("records", data) if isinstance(data, dict) else data

record = next(row for row in rows if row.get("detail_links"))
links = record["detail_links"]

if isinstance(links, str):
    links = json.loads(links)

url = links[0]
request = urllib.request.Request(
    url,
    headers={"User-Agent": "Mozilla/5.0"}
)

print("Auction:", record.get("auction_id"), record.get("scheme_name"))
print("URL:", url)

try:
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read()
        html = body.decode("utf-8", errors="replace").lower()

        print("HTTP STATUS:", response.status)
        print("CONTENT TYPE:", response.headers.get("Content-Type"))
        print("BYTES:", len(body))
        print("HTML TITLE PRESENT:", "<title" in html)
        print(
            "DOCUMENT KEYWORDS:",
            {
                word: html.count(word)
                for word in [
                    "site plan",
                    "location plan",
                    "showdocument",
                    "pdf",
                    "document",
                ]
            },
        )
        print("HTML PREVIEW:", html[:350].replace("\n", " "))

except Exception as error:
    print("REQUEST FAILED:", type(error).__name__, str(error))
