Jaipur Land Intelligence — coordinate research starter

Purpose
-------
Create a location-evidence queue from deploy_data/auctions.json. This is the first safe step toward a coordinate-generation pipeline. It does not guess coordinates from scheme names or plot numbers.

Install
-------
No extra packages are required. Uses Python standard library only.

Run from the project root in PowerShell
---------------------------------------
python .\generate_location_candidates.py

If you put the script elsewhere, pass paths explicitly:
python .\generate_location_candidates.py --input .\deploy_data\auctions.json --out .\data\location_research

Outputs
-------
data/location_research/location_candidates.csv
    Reviewable spreadsheet with one row per unique auction_id. Coordinate fields are blank until verified.
data/location_research/location_candidates.json
    Same records in JSON form, with source metadata.

Policy
------
- Scheme/plot text is an identifier, not a coordinate.
- Do not fill latitude/longitude unless supported by authoritative GIS data or a georeferenced plan with validated control points.
- For plot-level locations, record the official evidence URL and method, location_level=plot, verification_status=verified only after validation.
- For a general scheme location, use location_level=scheme and mark approximate/unverified honestly.
- Preserve the original auctions.json unchanged.
