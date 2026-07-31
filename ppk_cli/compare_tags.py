"""Compare two sets of geotagged photos position-by-position, matched by filename.

Purpose: tolerance sign-off. Prove our PPK pipeline's tagged photos agree with
Emlid Studio's (or any reference) to within centimetres, per photo.

Each side (<ref> = trusted, <test> = ours) is EITHER:
  * a folder of tagged JPGs  -> this tool runs `exiftool -n -csv` to read the GPS, or
  * a CSV, auto-detected as one of:
      - exiftool style : SourceFile, GPSLatitude, GPSLongitude, GPSAltitude
      - pipeline style : photo, lat, lon, ellh, q   (ppk_pipeline camera_positions.csv)

Photos are matched by basename (the DJI_..._NNNN_V.JPG name). Deltas are reported
in centimetres (E/N/U), reusing the exact math from compare_pos.py. The altitude
datum offset (ellipsoidal-vs-MSL geoid gap) is auto-detected and reported separately
so a constant ~18 m vertical gap is never mistaken for a position error.

Usage:
    python compare_tags.py <ref> <test> [--label-a ES] [--label-b PPK]
                           [--tol-h 5] [--tol-v 10] [--out deltas.csv]

Exit code: 0 = within tolerance, 2 = out of tolerance, 1 = hard error.
"""
import argparse
import csv
import io
import math
import os
import shutil
import subprocess
import sys

from compare_pos import enu_delta, stats   # same directory — reuse the delta math

# Fallback if exiftool isn't on PATH (open shells miss it until restarted).
EXIFTOOL_FALLBACK = r"C:\Users\Bidha\AppData\Local\Programs\ExifTool\ExifTool.exe"


def fail(msg):
    """Print a clear error to stderr and stop with exit code 1 (hard failure)."""
    print(f"\n!! {msg}", file=sys.stderr)
    sys.exit(1)


def _basename(name):
    """DJI filename from a full path or bare name, slash-agnostic."""
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _find_exiftool():
    """Return a usable exiftool path: PATH first, then the known install, else fail."""
    exe = shutil.which("exiftool")
    if exe:
        return exe
    if os.path.exists(EXIFTOOL_FALLBACK):
        return EXIFTOOL_FALLBACK
    fail("exiftool not found. Install it, or open a FRESH terminal so it's on PATH, "
         "then re-run. (Or dump the folder to a CSV with `exiftool -n -csv ...` and "
         "pass the CSV instead of the folder.)")


def _rows_from_folder(path):
    """Run exiftool on a folder and yield its -csv rows as dicts."""
    exe = _find_exiftool()
    cmd = [exe, "-n", "-csv", "-GPSLatitude", "-GPSLongitude", "-GPSAltitude", path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        fail(f"exiftool failed on {path}\n{proc.stderr.strip()}")
    return list(csv.DictReader(io.StringIO(proc.stdout)))


def _rows_from_csv(path):
    """Read a CSV into a list of row dicts. utf-8-sig strips any Excel byte-order
    mark so the first column name matches cleanly."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f))


def _columns(fieldnames):
    """Detect the CSV dialect and return its (name, lat, lon, alt) column keys.

    Accepts exiftool -csv (SourceFile/GPSLatitude/GPSLongitude/GPSAltitude) or
    the pipeline's own camera_positions.csv (photo/lat/lon/ellh). Fails loudly on
    anything else so we never read the wrong column as a coordinate."""
    fn = set(fieldnames or [])
    if {"GPSLatitude", "GPSLongitude"} <= fn:                 # exiftool -csv
        return "SourceFile", "GPSLatitude", "GPSLongitude", "GPSAltitude"
    if {"photo", "lat", "lon"} <= fn:                         # pipeline camera_positions.csv
        return "photo", "lat", "lon", "ellh"
    fail(f"unrecognised CSV columns {sorted(fn)} — expected exiftool "
         "(SourceFile/GPSLatitude/...) or pipeline (photo/lat/lon/ellh)")


def load_positions(path):
    """Return {basename: (lat, lon, alt)} and the count of rows skipped for no GPS."""
    if os.path.isdir(path):
        rows = _rows_from_folder(path)
    elif os.path.isfile(path):
        rows = _rows_from_csv(path)
    else:
        fail(f"not a folder or file: {path}")

    if not rows:
        fail(f"no rows read from {path}")
    name_col, lat_col, lon_col, alt_col = _columns(rows[0].keys())

    positions, skipped = {}, 0
    for row in rows:
        lat, lon = row.get(lat_col, ""), row.get(lon_col, "")
        if lat in ("", "-", None) or lon in ("", "-", None):
            skipped += 1
            continue
        try:
            alt_raw = row.get(alt_col, "")
            alt = float(alt_raw) if alt_raw not in ("", "-", None) else float("nan")
            positions[_basename(row[name_col])] = (float(lat), float(lon), alt)
        except (ValueError, KeyError):
            skipped += 1
    if not positions:
        fail(f"no usable GPS rows in {path}")
    return positions, skipped


def _median(vals):
    """Middle value of a list (mean of the two middle values if even length).

    Used to find the constant vertical offset (datum/geoid shift); the median is
    robust to a few noisy photos, unlike the mean."""
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def main():
    """CLI entry point: load both sides, match by filename, report the deltas.

    Exit codes: 0 = within tolerance, 2 = out of tolerance, 1 = hard error."""
    ap = argparse.ArgumentParser(description="Per-photo geotag comparison (cm), by filename")
    ap.add_argument("ref", help="reference: tagged folder or CSV (e.g. Emlid Studio)")
    ap.add_argument("test", help="test: tagged folder or CSV (e.g. our pipeline)")
    ap.add_argument("--label-a", default="REF")
    ap.add_argument("--label-b", default="TEST")
    ap.add_argument("--tol-h", type=float, default=5.0, help="horizontal tolerance, cm (default 5)")
    ap.add_argument("--tol-v", type=float, default=10.0, help="vertical tolerance, cm (default 10)")
    ap.add_argument("--out", help="write a per-photo delta CSV here")
    ns = ap.parse_args()

    a, skip_a = load_positions(ns.ref)
    b, skip_b = load_positions(ns.test)
    print(f"[{ns.label_a}] {ns.ref}\n    {len(a)} photos with GPS"
          + (f" ({skip_a} skipped, no GPS)" if skip_a else ""))
    print(f"[{ns.label_b}] {ns.test}\n    {len(b)} photos with GPS"
          + (f" ({skip_b} skipped, no GPS)" if skip_b else ""))

    names = sorted(set(a) & set(b))
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    print(f"\nMatched by filename: {len(names)}   "
          f"only-{ns.label_a}: {len(only_a)}   only-{ns.label_b}: {len(only_b)}")
    if not names:
        fail("no photos share a filename — cannot compare")
    if only_a or only_b:
        print("  !! WARNING: some photos are unmatched (expected 1:1 for the same flight)")
        for n in only_a[:5]:
            print(f"     only in {ns.label_a}: {n}")
        for n in only_b[:5]:
            print(f"     only in {ns.label_b}: {n}")

    lat0 = a[names[0]][0]
    rows = []          # (name, de, dn, du) in metres, test - ref
    for n in names:
        de, dn, du = enu_delta(a[n], b[n], lat0)
        rows.append((n, de, dn, du))

    horiz = [math.hypot(de, dn) for _, de, dn, _ in rows]
    du_signed = [du for *_, du in rows if not math.isnan(du)]

    hm, hr, hx = stats(horiz)
    print(f"\nHorizontal delta ({ns.label_b} - {ns.label_a}), {len(horiz)} photos")
    print(f"  mean {hm*100:7.2f} cm   rms {hr*100:7.2f} cm   max {hx*100:7.2f} cm")

    debias_vx = 0.0
    if du_signed:
        vm, vr, vx = stats([abs(x) for x in du_signed])
        med = _median(du_signed)
        resid = [x - med for x in du_signed]
        max_resid = max(abs(r) for r in resid)
        dm, dr, dx = stats([abs(r) for r in resid])
        debias_vx = dx
        print(f"\nVertical delta (raw), {len(du_signed)} photos")
        print(f"  mean {vm*100:7.2f} cm   rms {vr*100:7.2f} cm   max {vx*100:7.2f} cm")
        print(f"\nConstant vertical offset (median dU): {med:+.3f} m")
        if abs(med) > 0.5 and max_resid < 0.5:
            print(f"  -> looks like a DATUM/GEOID offset (ellipsoidal vs MSL), NOT a position error.")
            print(f"     The two datasets use different height references; de-biased vertical is the real signal.")
        elif abs(med) > 0.5:
            print(f"  -> large offset but scattered (residuals up to {max_resid*100:.1f} cm) — investigate, not a clean datum shift.")
        else:
            print(f"  -> small; heights share a datum.")
        print(f"\nVertical delta (de-biased: dU - median), {len(du_signed)} photos")
        print(f"  mean {dm*100:7.2f} cm   rms {dr*100:7.2f} cm   max {dx*100:7.2f} cm")
    else:
        print("\nNo altitudes to compare (one side has no GPSAltitude).")

    # worst offenders by horizontal delta
    worst = sorted(rows, key=lambda r: math.hypot(r[1], r[2]), reverse=True)[:10]
    print(f"\nWorst {len(worst)} photos by horizontal delta:")
    print(f"  {'photo':<34} {'dE cm':>8} {'dN cm':>8} {'dU cm':>8} {'horiz cm':>9}")
    for n, de, dn, du in worst:
        duc = "     nan" if math.isnan(du) else f"{du*100:8.1f}"
        print(f"  {n:<34} {de*100:8.1f} {dn*100:8.1f} {duc} {math.hypot(de,dn)*100:9.1f}")

    if ns.out:
        med = _median(du_signed) if du_signed else 0.0
        with open(ns.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["photo", "dE_cm", "dN_cm", "dU_cm", "dU_debiased_cm", "horiz_cm"])
            for n, de, dn, du in rows:
                duc = "" if math.isnan(du) else f"{du*100:.2f}"
                ddb = "" if math.isnan(du) else f"{(du-med)*100:.2f}"
                w.writerow([n, f"{de*100:.2f}", f"{dn*100:.2f}", duc, ddb,
                            f"{math.hypot(de,dn)*100:.2f}"])
        print(f"\nWrote per-photo deltas -> {ns.out}")

    # PASS/FAIL — vertical judged on the de-biased max so a geoid offset can't false-fail
    h_ok = hx * 100 <= ns.tol_h
    v_ok = (debias_vx * 100 <= ns.tol_v) if du_signed else True
    print("\n" + "=" * 60)
    verdict = "PASS" if (h_ok and v_ok) else "FAIL"
    print(f"  {verdict}  (tolerance: horiz <= {ns.tol_h:.1f} cm, "
          f"vert(de-biased) <= {ns.tol_v:.1f} cm)")
    print(f"    horizontal max {hx*100:.2f} cm  -> {'ok' if h_ok else 'OVER'}")
    if du_signed:
        print(f"    vertical(de-biased) max {debias_vx*100:.2f} cm  -> {'ok' if v_ok else 'OVER'}")
    print("=" * 60)
    sys.exit(0 if (h_ok and v_ok) else 2)


if __name__ == "__main__":
    main()
