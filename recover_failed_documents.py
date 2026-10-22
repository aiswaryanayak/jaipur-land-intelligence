
import csv
import hashlib
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

BASE_HOST = "udhonline.rajasthan.gov.in"
ROOT = Path("data/location_research/downloads_v2")
MANIFEST = ROOT / "document_manifest.csv"
OUT_DIR = ROOT / "recovered_images"
RECOVERY_CSV = ROOT / "recovery_manifest.csv"

MAX_BYTES = 25 * 1024 * 1024  # 25 MiB
TIMEOUT = 30
DELAY = 1.2

FIELDS = [
    "auction_id",
    "scheme_name",
    "document_label",
    "detail_url",
    "document_url",
    "sha256",
    "http_status",
    "content_type",
    "file_path",
    "result",
    "error",
]


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


def fetch_image(url):
    parsed = urlparse(url)

    if parsed.scheme != "https" or parsed.hostname != BASE_HOST:
        raise ValueError("Blocked URL outside official UDH HTTPS host")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "JaipurLandIntelligenceResearch/1.0"
        },
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        final = urlparse(response.geturl())

        if final.scheme != "https" or final.hostname != BASE_HOST:
            raise ValueError("Unexpected redirect host")

        content_type = response.headers.get("Content-Type", "")
        length = response.headers.get("Content-Length", "")

        # Stop before reading the body if the declared size is too large.
        if length.isdigit() and int(length) > MAX_BYTES:
            size_mb = int(length) / (1024 * 1024)

            print(
                f"OVERSIZED IMAGE | {size_mb:.2f} MiB | "
                f"limit={MAX_BYTES / (1024 * 1024):.0f} MiB"
            )

            raise ValueError(
                f"Declared size {int(length)} bytes "
                f"({size_mb:.2f} MiB) exceeds "
                f"{MAX_BYTES} byte limit"
            )

        # Read at most one byte beyond the configured limit.
        body = response.read(MAX_BYTES + 1)

        if len(body) > MAX_BYTES:
            raise ValueError(
                f"Response exceeds {MAX_BYTES} byte limit"
            )

        ext = image_extension(body)

        if not content_type.lower().startswith("image/") or not ext:
            raise ValueError(
                "Unsupported image response: "
                f"{content_type or 'unknown content type'}"
            )

        return response.status, content_type, body, ext


def main():
    if not MANIFEST.exists():
        raise SystemExit(f"Manifest not found: {MANIFEST}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with MANIFEST.open(
        "r", encoding="utf-8-sig", newline=""
    ) as f:
        source_rows = list(csv.DictReader(f))

    failed = [
        row for row in source_rows
        if row.get("result") == "failed"
    ]

    # Keep one row per distinct failed URL.
    unique = {}

    for row in failed:
        url = row.get("document_url", "")
        if url:
            unique.setdefault(url, row)

    if RECOVERY_CSV.exists():
        with RECOVERY_CSV.open(
            "r", encoding="utf-8-sig", newline=""
        ) as f:
            recovery_rows = list(csv.DictReader(f))
    else:
        recovery_rows = []

    # Avoid retrying URLs that were already recovered successfully.
    completed_urls = {
        row.get("document_url")
        for row in recovery_rows
        if row.get("result") in {"downloaded", "reused_hash"}
    }

    hash_to_path = {}

    # Reuse images from previous recovery runs.
    for row in recovery_rows:
        digest = row.get("sha256", "")
        path = row.get("file_path", "")

        if digest and path and Path(path).exists():
            hash_to_path[digest] = path

    # Reuse images downloaded by the original collector.
    for row in source_rows:
        digest = row.get("sha256", "")
        path = row.get("file_path", "")

        if digest and path and Path(path).exists():
            hash_to_path.setdefault(digest, path)

    for index, (url, source) in enumerate(unique.items(), 1):
        if url in completed_urls:
            print(
                f"{index}/{len(unique)} | already recovered | "
                f"{source.get('auction_id')}"
            )
            continue

        result = {
            key: source.get(key, "") for key in FIELDS
        }

        result.update(
            sha256="",
            http_status="",
            content_type="",
            file_path="",
            result="",
            error="",
        )

        try:
            time.sleep(DELAY)

            status, content_type, body, ext = fetch_image(url)
            digest = hashlib.sha256(body).hexdigest()

            result["http_status"] = status
            result["content_type"] = content_type
            result["sha256"] = digest

            existing_path = hash_to_path.get(digest)

            if existing_path and Path(existing_path).exists():
                result["file_path"] = existing_path
                result["result"] = "reused_hash"
            else:
                filename = (
                    f"{source.get('auction_id', 'unknown')}_"
                    f"{digest[:12]}{ext}"
                )

                path = OUT_DIR / filename
                path.write_bytes(body)

                result["file_path"] = str(path)
                result["result"] = "downloaded"
                hash_to_path[digest] = str(path)

        except Exception as exc:
            result["result"] = "failed"
            result["error"] = (
                f"{type(exc).__name__}: {exc}"
            )

        recovery_rows.append(result)

        # Save progress after each URL.
        with RECOVERY_CSV.open(
            "w", encoding="utf-8-sig", newline=""
        ) as f:
            writer = csv.DictWriter(
                f, fieldnames=FIELDS
            )
            writer.writeheader()
            writer.writerows(recovery_rows)

        print(
            f"{index}/{len(unique)} | "
            f"{result['auction_id']} | "
            f"{result['result']} | "
            f"{result['error']}"
        )

    print("\nRECOVERY RUN FINISHED")
    print("Unique failed URLs:", len(unique))
    print("Recovery manifest:", RECOVERY_CSV.resolve())
    print("Recovered image folder:", OUT_DIR.resolve())
    print("Original manifest and database were not modified.")


if __name__ == "__main__":
    main()
