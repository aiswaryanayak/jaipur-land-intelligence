
import sqlite3
from pathlib import Path

db_path = Path("data/jaipur_land_intelligence.sqlite3")

if not db_path.exists():
    print("ERROR: Database file not found.")
    raise SystemExit(1)

with sqlite3.connect(db_path) as connection:
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name",
        ("table",),
    ).fetchall()

    print("TABLES:")
    for (name,) in tables:
        print(f"  {name}")

    print("\nCOUNTS:")
    for table in (
        "auctions",
        "auction_source_records",
        "scheme_labels",
        "auction_scheme_labels",
    ):
        count = connection.execute(
            f"SELECT COUNT(*) FROM [{table}]"
        ).fetchone()[0]
        print(f"  {table}: {count}")
