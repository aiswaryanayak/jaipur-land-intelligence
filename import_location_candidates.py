
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "jaipur_land_intelligence.sqlite3"
CANDIDATES_PATH = (
    ROOT / "data" / "location_research" / "location_candidates.json"
)


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    payload = json.loads(
        CANDIDATES_PATH.read_text(encoding="utf-8")
    )
    candidates = payload.get("records", [])

    if not isinstance(candidates, list):
        raise ValueError("Expected a records list in candidate JSON.")

    # Require exact scheme labels; do not guess or fuzzy-match.
    labels = {
        str(row.get("scheme_name") or "").strip()
        for row in candidates
    }
    if not labels or "" in labels:
        raise ValueError("Missing scheme labels; no import performed.")

    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS location_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scheme_label_id INTEGER NOT NULL UNIQUE,
                location_level TEXT NOT NULL DEFAULT 'scheme_level',
                verification_status TEXT NOT NULL DEFAULT 'pending',
                candidate_count INTEGER NOT NULL DEFAULT 0,
                source_urls_json TEXT NOT NULL DEFAULT '[]',
                evidence_url TEXT,
                review_notes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (scheme_label_id)
                    REFERENCES scheme_labels(id)
            )
            """
        )

        scheme_rows = conn.execute(
            "SELECT id, label FROM scheme_labels"
        ).fetchall()
        scheme_ids = {label: scheme_id for scheme_id, label in scheme_rows}

        unmatched = sorted(labels - set(scheme_ids))
        if unmatched:
            raise ValueError(
                "Unmatched labels; import cancelled: "
                + ", ".join(unmatched)
            )

        # Count candidates and collect source links for each exact label.
        grouped = {}
        for row in candidates:
            label = str(row.get("scheme_name") or "").strip()
            group = grouped.setdefault(
                label, {"count": 0, "urls": set()}
            )
            group["count"] += 1

            for field in ("source_url", "report_url"):
                url = str(row.get(field) or "").strip()
                if url.startswith("https://") or url.startswith("http://"):
                    group["urls"].add(url)

            detail_links = row.get("detail_links") or []
            if isinstance(detail_links, list):
                for item in detail_links:
                    url = (
                        str(item.get("url") or "").strip()
                        if isinstance(item, dict)
                        else str(item).strip()
                    )
                    if url.startswith(("https://", "http://")):
                        group["urls"].add(url)

        inserted = 0
        updated = 0

        for label, group in grouped.items():
            scheme_id = scheme_ids[label]
            existing = conn.execute(
                """
                SELECT candidate_count, source_urls_json, review_notes
                FROM location_reviews
                WHERE scheme_label_id = ?
                """,
                (scheme_id,),
            ).fetchone()

            urls_json = json.dumps(
                sorted(group["urls"]), ensure_ascii=False
            )
            notes = (
                "Research required. These are observed auction scheme "
                "labels, not confirmed unique physical schemes. Find "
                "authoritative location evidence before assigning "
                "coordinates. Scheme-level evidence does not establish "
                "an individual plot's position."
            )

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO location_reviews (
                        scheme_label_id, location_level,
                        verification_status, candidate_count,
                        source_urls_json, evidence_url, review_notes,
                        created_at, updated_at
                    ) VALUES (?, 'scheme_level', 'pending', ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        scheme_id, group["count"], urls_json,
                        notes, now, now,
                    ),
                )
                inserted += 1
            else:
                # Refresh only derived candidate counts and source links.
                # Preserve review status, evidence URL and reviewer notes.
                conn.execute(
                    """
                    UPDATE location_reviews
                    SET candidate_count = ?,
                        source_urls_json = ?,
                        updated_at = ?
                    WHERE scheme_label_id = ?
                    """,
                    (group["count"], urls_json, now, scheme_id),
                )
                updated += 1

        total = conn.execute(
            "SELECT COUNT(*) FROM location_reviews"
        ).fetchone()[0]
        pending = conn.execute(
            """
            SELECT COUNT(*) FROM location_reviews
            WHERE verification_status = 'pending'
            """
        ).fetchone()[0]
        conn.commit()

    print("LOCATION REVIEW QUEUE IMPORT COMPLETE")
    print(f"Candidate rows read: {len(candidates)}")
    print(f"Distinct scheme labels matched: {len(grouped)}")
    print(f"New review records: {inserted}")
    print(f"Existing review records refreshed: {updated}")
    print(f"Total review records: {total}")
    print(f"Pending review records: {pending}")
    print("Coordinates created: 0")
    print("The existing locations table was not modified.")
    print("No app, Git, GitHub, or live deployment was changed.")


if __name__ == "__main__":
    main()
