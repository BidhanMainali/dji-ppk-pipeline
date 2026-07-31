"""
parse_mrk.py  —  Task 1, piece 1: parse the DJI .MRK mark file.

The .MRK file has one line per photo. Fields are separated by TABS.
Most fields look like  "value,LABEL"  (value first, label second), e.g.
    48.406,Ellh   ->  value 48.406, label "Ellh"
Exceptions:
    field 1  -> just the photo index           e.g. "1"
    field 3  -> GPS week in brackets            e.g. "[2419]"
    field 10 -> three std-devs in one field     e.g. "0.015820, 0.019742, 0.028599"

We parse defensively: split on tabs, then pull each piece out by position,
but VERIFY the label is what we expect so a format change can't silently
put the wrong number in the wrong column.
"""

# DJI RTK quality flag (last column "Q"): 50 = FIX, 34 = FLOAT, 16 = SINGLE.
Q_MEANING = {50: "FIX", 34: "FLOAT", 16: "SINGLE"}


def _val_label(field):
    """Split a 'value,LABEL' field -> (value_str, label_str), both stripped."""
    value, label = field.split(",")
    return value.strip(), label.strip()


def parse_mrk_line(line):
    """Parse one raw .MRK line into a dict. Raises if the layout is unexpected."""
    cols = line.rstrip("\n").split("\t")
    if len(cols) != 11:
        raise ValueError(f"expected 11 tab-separated columns, got {len(cols)}: {cols!r}")

    # --- field 1: photo index (matches the DJI_..._NNNN_V.JPG number) ---
    photo_index = int(cols[0])

    # --- field 2: GPS seconds-of-week ---
    gps_sow = float(cols[1])

    # --- field 3: GPS week, written as "[2419]" ---
    gps_week = int(cols[2].strip().lstrip("[").rstrip("]"))

    # --- fields 4-6: lever-arm offsets in millimetres, labelled N / E / V ---
    n_val, n_lab = _val_label(cols[3]); assert n_lab == "N", n_lab
    e_val, e_lab = _val_label(cols[4]); assert e_lab == "E", e_lab
    v_val, v_lab = _val_label(cols[5]); assert v_lab == "V", v_lab
    lever_n_mm, lever_e_mm, lever_v_mm = float(n_val), float(e_val), float(v_val)

    # --- fields 7-9: the RTK position (lat, lon, ellipsoidal height) ---
    lat_val, lat_lab = _val_label(cols[6]); assert lat_lab == "Lat", lat_lab
    lon_val, lon_lab = _val_label(cols[7]); assert lon_lab == "Lon", lon_lab
    ellh_val, ellh_lab = _val_label(cols[8]); assert ellh_lab == "Ellh", ellh_lab
    lat, lon, ellh = float(lat_val), float(lon_val), float(ellh_val)

    # --- field 10: three std-devs (metres): lat, lon, height ---
    std_lat, std_lon, std_hgt = [float(x) for x in cols[9].split(",")]

    # --- field 11: quality flag "50,Q" ---
    q_val, q_lab = _val_label(cols[10]); assert q_lab == "Q", q_lab
    q_flag = int(q_val)

    return {
        "photo_index": photo_index,
        "gps_sow": gps_sow,
        "gps_week": gps_week,
        "lever_n_mm": lever_n_mm,
        "lever_e_mm": lever_e_mm,
        "lever_v_mm": lever_v_mm,
        "lat": lat,
        "lon": lon,
        "ellh": ellh,
        "std_lat": std_lat,
        "std_lon": std_lon,
        "std_hgt": std_hgt,
        "q_flag": q_flag,
        "fix_status": Q_MEANING.get(q_flag, f"UNKNOWN({q_flag})"),
    }


def parse_mrk_file(mrk_path):
    """Parse every line of a .MRK file -> list of record dicts (in file order)."""
    records = []
    with open(mrk_path) as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue  # skip any blank lines
            try:
                records.append(parse_mrk_line(line))
            except Exception as e:
                raise ValueError(f"{mrk_path} line {lineno}: {e}") from e
    return records


def match_records_to_images(records, image_dir):
    """
    Attach the image file path to each record by its photo_index.
    Image files embed the index as a 4-digit chunk: DJI_..._0001_V.JPG.
    Returns (matched, missing_images, orphan_images):
      matched         - records that found exactly one image (path added in-place)
      missing_images  - records whose expected image file was not found
      orphan_images   - image files on disk that no record claims
    """
    import glob, os

    # Map "0001" -> full path, for every *_V.JPG image present.
    images_by_id = {}
    for path in glob.glob(os.path.join(image_dir, "DJI_*_V.JPG")):
        # filename like DJI_20260522132151_0001_V.JPG -> grab the "0001" piece
        token = os.path.basename(path).split("_")[2]
        images_by_id[token] = path

    matched, missing_images = [], []
    claimed = set()
    for rec in records:
        img_id = f"{rec['photo_index']:04d}"   # 1 -> "0001"
        path = images_by_id.get(img_id)
        if path is None:
            missing_images.append(rec)
        else:
            rec["image_id"] = img_id
            rec["image_path"] = path
            matched.append(rec)
            claimed.add(img_id)

    orphan_images = [p for tok, p in images_by_id.items() if tok not in claimed]
    return matched, missing_images, orphan_images


# Column order for the output CSV: identity/position first, then uncertainty,
# then quality, then the raw lever-arm offsets, then the source filename.
CSV_COLUMNS = [
    "image_id", "photo_index", "gps_week", "gps_sow",
    "lat", "lon", "ellh",
    "std_lat", "std_lon", "std_hgt",
    "fix_status", "q_flag",
    "lever_n_mm", "lever_e_mm", "lever_v_mm",
    "image_file",
]


def write_csv(matched, out_path):
    """Write matched records to a CSV at out_path (one row per image)."""
    import csv, os

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for rec in matched:
            row = dict(rec)
            row["image_file"] = os.path.basename(rec["image_path"])
            writer.writerow(row)


if __name__ == "__main__":
    import glob, os
    from collections import Counter

    mrk_path = glob.glob("DJI_*/*_D.MRK")[0]
    image_dir = os.path.dirname(mrk_path)
    print("reading:", mrk_path)

    records = parse_mrk_file(mrk_path)
    print(f"parsed records      : {len(records)}")

    matched, missing, orphans = match_records_to_images(records, image_dir)
    print(f"matched to an image : {len(matched)}")
    print(f"records w/o image   : {len(missing)}")
    print(f"images w/o record   : {len(orphans)}")

    fix_counts = Counter(r["fix_status"] for r in records)
    print("fix-status breakdown:", dict(fix_counts))

    if missing:
        print("  missing image for indices:", [r["photo_index"] for r in missing][:20])
    if orphans:
        print("  orphan images:", [os.path.basename(p) for p in orphans][:20])

    out_path = os.path.join("output", "mrk_positions.csv")
    write_csv(matched, out_path)
    print("-" * 60)
    print(f"wrote {len(matched)} rows -> {out_path}")
