"""
gcp_shift.py - automate the CSRS-PPP GCP shift (replaces the manual HTML tool).

The base station's on-device average can be ~1 m off; CSRS-PPP gives the true
position. Because every GCP was surveyed relative to that (wrong) base, they are
all off by the SAME vector - so shifting them all by (CSRS_true - original_base)
corrects them together. This is exactly what the team's HTML "GCP shift
calculator" does, automated.

Given an Emlid Flow GCP CSV, the base RINEX, and a CSRS-PPP result, this tool:
  - reads the ORIGINAL base position from the CSV's "Base ..." columns,
  - reads the CSRS estimated position (from a .sum, or --csrs-pos),
  - reads DELTA H (antenna height) from the base RINEX,
  - computes the shift = CSRS_true - original_base,
  - writes gcp_corrected.csv (every GCP shifted, base columns rewritten,
    CS name tagged 'Global CS (ITRF2020)'),
  - prints the base coordinate two ways: the ground MARKER (for our RTKLIB
    pipeline's --base-pos) and the ARP = marker + DELTA H (for DJI Terra's
    "Enter Center Points").

Usage:
  python gcp_shift.py --gcp <emlid.csv> --base <baseRINEXfolder|.yyO> \\
      (--csrs <report.sum> | --csrs-pos <lat> <lon> <ellh>) [--out gcp_corrected.csv]

Notes: --csrs-pos lat/lon accept DMS (quote them, e.g. "49 56 38.8") or decimal.
"""

import argparse
import csv
import glob
import math
import os
import sys

from csrs import parse_latlon, parse_sum, read_delta_h

# Emlid Flow GCP CSV column headers (as the HTML calculator expects them).
COL_LON, COL_LAT, COL_H = "Longitude", "Latitude", "Ellipsoidal height"
COL_BLON, COL_BLAT, COL_BH = "Base longitude", "Base latitude", "Base ellipsoidal height"
COL_CS = "CS name"


def fail(msg):
    """Print a clear error and stop (exit code 1)."""
    print(f"\n!! {msg}", file=sys.stderr)
    sys.exit(1)


def find_base_obs(base):
    """Resolve --base to a single base observation file. Accepts a .yyO file
    directly, or a folder containing exactly one (glob *.?[0-9]O)."""
    if os.path.isfile(base):
        return base
    hits = glob.glob(os.path.join(base, "*.?[0-9]O"))
    if len(hits) != 1:
        fail(f"expected exactly one base obs (*.yyO) in {base}, found {len(hits)}")
    return hits[0]


def read_base_pos_from_csv(rows):
    """Read the original base position (blat, blon, bh) from the first data row
    that carries the 'Base ...' columns. These are decimal degrees / metres."""
    for r in rows:
        try:
            blat = float(r[COL_BLAT]); blon = float(r[COL_BLON]); bh = float(r[COL_BH])
        except (KeyError, ValueError, TypeError):
            continue
        return blat, blon, bh
    fail(f"no usable '{COL_BLAT}'/'{COL_BLON}'/'{COL_BH}' values in the GCP CSV "
         "(needed as the original base position). Provide --base-pos instead.")


def compute_shift(base_pos, csrs_pos, dh):
    """Return the shift dict. Mirrors the HTML calculator exactly:
      marker = ph                (CSRS-PPP position IS the ground mark)
      terraH = ph + dh           (ARP = mark + antenna height; DJI Terra wants this)
      shift  = CSRS_true - original_base
    The metre figures (mN/mE) are display/warning only, using the tool's constants.
    """
    blat, blon, bh = base_pos
    plat, plon, ph = csrs_pos
    marker = ph
    terra_h = ph + dh
    dlat, dlon, du = plat - blat, plon - blon, terra_h - bh
    m_n = dlat * 111320.0
    m_e = dlon * 111320.0 * math.cos(math.radians(plat))
    return {"dlat": dlat, "dlon": dlon, "du": du, "marker": marker,
            "terra_h": terra_h, "plat": plat, "plon": plon, "m_n": m_n, "m_e": m_e}


def fix_csv(rows, fieldnames, shift, out_path):
    """Write gcp_corrected.csv: shift every GCP row and rewrite the base columns.
    Mirrors the HTML tool's fixCsv (8 dp lat/lon, 3 dp height)."""
    for col in (COL_LON, COL_LAT, COL_H):
        if col not in fieldnames:
            fail(f"column '{col}' not found - is this the Emlid Flow GCP CSV export? "
                 f"(found columns: {', '.join(fieldnames)})")
    n = 0
    for r in rows:
        try:
            r[COL_LON] = f"{float(r[COL_LON]) + shift['dlon']:.8f}"
            r[COL_LAT] = f"{float(r[COL_LAT]) + shift['dlat']:.8f}"
            r[COL_H] = f"{float(r[COL_H]) + shift['du']:.3f}"
        except (ValueError, TypeError):
            continue  # leave non-numeric rows (e.g. notes) untouched
        if COL_BLON in fieldnames:
            r[COL_BLON] = f"{shift['plon']:.8f}"
        if COL_BLAT in fieldnames:
            r[COL_BLAT] = f"{shift['plat']:.8f}"
        if COL_BH in fieldnames:
            r[COL_BH] = f"{shift['marker']:.3f}"
        if COL_CS in fieldnames:
            r[COL_CS] = "Global CS (ITRF2020)"
        n += 1
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return n


def main():
    ap = argparse.ArgumentParser(description="Automate the CSRS-PPP GCP shift")
    ap.add_argument("--gcp", required=True, help="Emlid Flow GCP CSV export")
    ap.add_argument("--base", required=True, help="base RINEX folder or .yyO file")
    ap.add_argument("--csrs", help="CSRS-PPP .sum report to read the estimate from")
    ap.add_argument("--csrs-pos", nargs=3, metavar=("LAT", "LON", "ELLH"),
                    help="manual CSRS estimate (DMS or decimal lat/lon, ellh in m)")
    ap.add_argument("--base-pos", nargs=3, metavar=("LAT", "LON", "ELLH"),
                    help="override the original base position (else read from the CSV)")
    ap.add_argument("--out", default="gcp_corrected.csv", help="output CSV path")
    args = ap.parse_args()

    if not args.csrs and not args.csrs_pos:
        fail("provide the CSRS estimate: --csrs <report.sum> or --csrs-pos LAT LON ELLH")

    # --- CSRS estimated position (the true base = ground marker) ---
    if args.csrs_pos:
        plat = parse_latlon(args.csrs_pos[0])
        plon = parse_latlon(args.csrs_pos[1])
        ph = float(args.csrs_pos[2])
        csrs_src = "manual (--csrs-pos)"
    else:
        res = parse_sum(args.csrs)
        plat, plon, ph = res.lat, res.lon, res.ellh
        csrs_src = f"{args.csrs} ({res.datum} {res.epoch or ''})".strip()

    # --- DELTA H from the base RINEX ---
    base_obs = find_base_obs(args.base)
    dh = read_delta_h(base_obs)
    if not (1.0 <= dh <= 2.5):
        print(f"  !! DELTA H = {dh} m is outside the usual 1.0-2.5 m - double-check "
              "the antenna height line in the base RINEX.")
    if dh < 0 or dh > 3:
        fail(f"DELTA H = {dh} m is implausible (expected ~1-2.5 m) - refusing to guess.")

    # --- original base position ---
    with open(args.gcp, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    if args.base_pos:
        base_pos = (parse_latlon(args.base_pos[0]), parse_latlon(args.base_pos[1]),
                    float(args.base_pos[2]))
    else:
        base_pos = read_base_pos_from_csv(rows)

    shift = compute_shift(base_pos, (plat, plon, ph), dh)

    # --- report ---
    print(f"\nCSRS estimate : {csrs_src}")
    print(f"DELTA H       : {dh:.4f} m (from {os.path.basename(base_obs)})")
    print(f"original base : {base_pos[0]:.8f}, {base_pos[1]:.8f}, {base_pos[2]:.3f} m")
    print(f"shift         : {shift['m_n']:+.2f} m N, {shift['m_e']:+.2f} m E, "
          f"{shift['du']:+.2f} m up")
    print("\nBase coordinate to use:")
    print(f"  MARKER (our pipeline --base-pos): "
          f"{shift['plat']:.8f} {shift['plon']:.8f} {shift['marker']:.3f}")
    print(f"  ARP    (DJI Terra Center Point) : "
          f"{shift['plat']:.8f} {shift['plon']:.8f} {shift['terra_h']:.3f}")
    if abs(shift["m_n"]) > 3 or abs(shift["m_e"]) > 3 or abs(shift["du"]) > 4:
        print("\n  !! Shift is unusually large (>3 m). Check the inputs - the CSRS-PPP "
              "result must be the ITRF2020 run, not NAD83.")

    # --- write corrected GCPs ---
    n = fix_csv(rows, fieldnames, shift, args.out)
    print(f"\nwrote {n} corrected GCP rows -> {args.out}")


if __name__ == "__main__":
    main()
