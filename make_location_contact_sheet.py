
import csv
import hashlib
from collections import defaultdict
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps

MANIFEST = Path("data/location_research/downloads/document_manifest.csv")
OUT_DIR = Path("data/location_research")
SHEET_PATH = OUT_DIR / "location_plans_contact_sheet.jpg"
DUPLICATES_PATH = OUT_DIR / "duplicate_location_images.txt"

THUMB_W, THUMB_H = 320, 230
CELL_W, CELL_H = 350, 290
COLUMNS = 3

if not MANIFEST.exists():
    raise FileNotFoundError(f"Manifest not found: {MANIFEST}")

with MANIFEST.open("r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

downloaded = [
    row for row in rows
    if row.get("result") == "downloaded"
    and row.get("file_path")
    and Path(row["file_path"]).exists()
]

if not downloaded:
    raise RuntimeError("No downloaded images found in the manifest.")

OUT_DIR.mkdir(parents=True, exist_ok=True)

hash_groups = defaultdict(list)
cards = []

for row in downloaded:
    path = Path(row["file_path"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    hash_groups[digest].append(path.name)

    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            original_size = image.size
            image.thumbnail((THUMB_W, THUMB_H))
            cards.append({
                "row": row,
                "path": path,
                "image": image.copy(),
                "original_size": original_size,
                "hash": digest,
            })
    except Exception as error:
        print(f"IMAGE ERROR: {path.name}: {error}")

if not cards:
    raise RuntimeError("Images were found but none could be opened.")

duplicate_groups = [names for names in hash_groups.values() if len(names) > 1]

with DUPLICATES_PATH.open("w", encoding="utf-8") as f:
    f.write("EXACT DUPLICATE IMAGE GROUPS (SHA-256)\n")
    f.write("Only byte-for-byte identical files are grouped here.\n\n")
    if not duplicate_groups:
        f.write("No exact duplicate images found.\n")
    for index, names in enumerate(duplicate_groups, 1):
        f.write(f"Group {index}:\n")
        for name in names:
            f.write(f"  {name}\n")
        f.write("\n")

rows_count = (len(cards) + COLUMNS - 1) // COLUMNS
sheet = Image.new(
    "RGB",
    (COLUMNS * CELL_W, rows_count * CELL_H),
    "white"
)
draw = ImageDraw.Draw(sheet)

for index, card in enumerate(cards):
    x = (index % COLUMNS) * CELL_W
    y = (index // COLUMNS) * CELL_H

    image = card["image"]
    image_x = x + (CELL_W - image.width) // 2
    image_y = y + 8
    sheet.paste(image, (image_x, image_y))

    row = card["row"]
    auction = row.get("auction_id", "?")
    scheme = row.get("scheme_name", "Unknown scheme")
    label = row.get("document_label", "Unknown document")

    text_y = y + THUMB_H + 18
    lines = [
        f"Auction: {auction} | {scheme[:35]}",
        label[:48],
        f"Original: {card['original_size'][0]} x {card['original_size'][1]}",
        f"SHA-256: {card['hash'][:16]}...",
    ]
    for line in lines:
        draw.text((x + 10, text_y), line, fill="black")
        text_y += 17

    draw.rectangle(
        (x + 2, y + 2, x + CELL_W - 3, y + CELL_H - 3),
        outline="#999999",
        width=1,
    )

sheet.save(SHEET_PATH, quality=88)

print("CONTACT SHEET CREATED")
print("Images displayed:", len(cards))
print("Exact duplicate groups:", len(duplicate_groups))
print("Contact sheet:", SHEET_PATH.resolve())
print("Duplicate report:", DUPLICATES_PATH.resolve())
print("\nDUPLICATE GROUPS:")
if duplicate_groups:
    for group in duplicate_groups:
        print("  " + " | ".join(group))
else:
    print("  None found.")
print("\nNo source images, auction data, database, app, or live deployment were modified.")
