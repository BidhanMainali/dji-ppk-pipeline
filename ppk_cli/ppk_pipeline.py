"""
ppk_pipeline.py — one-command PPK pipeline: solve + fix report + geotagged JPGs.

For each flight folder:
  1. discover      find rover OBS/NAV/MRK + photos, base OBS/NAV
  2. build_config  per-flight .conf from the validated iter02 template
                   (base antenna height computed from the base RINEX header)
  3. inject_events insert the .MRK shutter timestamps into a copy of the rover
                   OBS as RINEX event marks (epoch flag 5) — the same mechanism
                   Emlid Studio uses; RTKLIB then interpolates camera positions
  4. solve         run rnx2rtkp with a live progress % line
  5. report        final FIX/FLOAT/SINGLE percentages (epochs + camera events)
  6. tag_photos    copy each JPG and write the PPK position into its EXIF GPS

Usage:
    python ppk_pipeline.py <flight_dir> [<flight_dir2> ...] --base <base_dir>
                           [--min-fix 95]
    python ppk_pipeline.py clean                  delete all of ppk_cli\\output\\
    python ppk_pipeline.py clean <flight_name>    delete one flight's output only

Originals are never modified; everything lands in ppk_cli\\output\\<flight>\\.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from compare_pos import parse_pos, QNAME          # noqa: E402  (same dir)
from parse_mrk import parse_mrk_file              # noqa: E402  (scripts dir)

RNX2RTKP = r"C:\ACIS\tools\RTKLIB_EX_2.5.1\RTKLIB_EX_2.5.1\rnx2rtkp.exe"
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs", "iter02.conf")
OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
RS4_APC_L1_M = 0.095   # Emlid RS4 L1 phase-center offset (Emlid spec)
GPS_EPOCH = datetime(1980, 1, 6)


def fail(msg):
    """Print a clear error to stderr and stop the whole run (exit code 1).

    Called wherever a precondition is violated. The pipeline is deliberately
    designed to STOP LOUDLY rather than silently produce a wrong position."""
    print(f"\n!! {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- 1. discover

def _one(pattern, what):
    """Return the single file matching a glob pattern; fail if 0 or >1 match.

    Ambiguity (e.g. two *_D.OBS files in one folder) is treated as an error so
    the pipeline never has to guess which file the user meant."""
    hits = glob.glob(pattern)
    if len(hits) != 1:
        fail(f"expected exactly one {what} matching {pattern}, found {len(hits)}")
    return hits[0]


def discover(flight_dir, base_dir):
    """Locate every input file a flight needs, in the flight and base folders.

    Returns a dict of paths plus 'jpg_by_id' — a {4-digit token -> JPG path} map
    (from names like DJI_..._0001_V.JPG) used later to pair each photo with its
    MRK row. Fails loudly via _one() if any required file is missing/ambiguous."""
    files = {
        "obs": _one(os.path.join(flight_dir, "*_D.OBS"), "rover OBS"),
        "nav": _one(os.path.join(flight_dir, "*_D.NAV"), "rover NAV"),
        "mrk": _one(os.path.join(flight_dir, "*_D.MRK"), "MRK file"),
        "base_obs": _one(os.path.join(base_dir, "*.?[0-9]O"), "base obs (.yyO)"),
        "base_nav": _one(os.path.join(base_dir, "*.?[0-9]P"), "base nav (.yyP)"),
    }
    jpgs = sorted(glob.glob(os.path.join(flight_dir, "DJI_*_V.JPG")))
    if not jpgs:
        fail(f"no DJI_*_V.JPG photos in {flight_dir}")
    # index photos by the 4-digit token in the filename (DJI_..._0001_V.JPG)
    files["jpg_by_id"] = {os.path.basename(p).split("_")[2]: p for p in jpgs}
    return files


# ------------------------------------------------------------ 2. build_config

def build_config(base_obs, out_conf):
    """Copy the validated template, setting ant2-antdelu = pole height from the
    base RINEX 'ANTENNA: DELTA H/E/N' + the RS4 L1 phase-center offset."""
    delta_h = None
    with open(base_obs, errors="replace") as f:
        for line in f:
            if line[60:].strip() == "ANTENNA: DELTA H/E/N":
                delta_h = float(line[:14])
            if line[60:].strip() == "END OF HEADER":
                break
    if delta_h is None:
        fail(f"no 'ANTENNA: DELTA H/E/N' in {base_obs} — cannot set base antenna height")
    antdelu = delta_h + RS4_APC_L1_M

    out = []
    for line in open(TEMPLATE):
        if line.startswith("ant2-antdelu"):
            line = f"ant2-antdelu       ={antdelu:.4f}   # {delta_h} pole + {RS4_APC_L1_M} RS4 L1 APC\n"
        out.append(line)
    with open(out_conf, "w") as f:
        f.writelines(out)
    return antdelu


# ---------------------------------------------------------- 3. inject_events

def _sow_to_gpst(week, sow):
    """Convert a GPS (week number, seconds-of-week) pair to a calendar datetime."""
    return GPS_EPOCH + timedelta(weeks=week, seconds=sow)


def _event_line(t):
    """Format a datetime as a RINEX 3 event record (epoch flag 5 = camera event)."""
    # mirror the RINEX 3 epoch record layout: "> 2026  5 22 20 21 23.4000000  0 34"
    sec = t.second + t.microsecond / 1e6
    return f"> {t.year:4d} {t.month:2d} {t.day:2d} {t.hour:2d} {t.minute:2d}{sec:11.7f}  5  0\n"


def _epoch_time(line):
    """Read the calendar time from a RINEX '> yyyy mm dd hh mm ss ...' epoch line."""
    p = line.split()
    return datetime(int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5])) \
        + timedelta(seconds=float(p[6]))


def inject_events(obs_in, mrk_records, obs_out):
    """Write a copy of the rover OBS with one flag-5 event record per MRK row,
    inserted in chronological order among the observation epochs."""
    events = [_sow_to_gpst(r["gps_week"], r["gps_sow"]) for r in mrk_records]
    if events != sorted(events):
        fail("MRK timestamps are not in chronological order — refusing to inject")
    pending = list(events)
    inserted = 0

    with open(obs_in, errors="replace") as src, open(obs_out, "w") as dst:
        in_header = True
        for line in src:
            if in_header:
                dst.write(line)
                if line[60:].strip() == "END OF HEADER":
                    in_header = False
                continue
            if line.startswith("> "):
                t = _epoch_time(line)
                while pending and pending[0] <= t:
                    dst.write(_event_line(pending.pop(0)))
                    inserted += 1
            dst.write(line)
        for t in pending:                     # events after the last obs epoch
            dst.write(_event_line(t))
            inserted += 1

    if inserted != len(events):
        fail(f"event injection wrote {inserted} of {len(events)} events")
    return len(events)


# ------------------------------------------------------------------ 4. solve

def _obs_window(obs_path):
    """Read TIME OF FIRST/LAST OBS from a RINEX header -> (start, end) datetimes.

    solve() uses this window to turn each 'processing : <time>' line from
    rnx2rtkp into a 0-100% progress figure."""
    first = last = None
    with open(obs_path, errors="replace") as f:
        for line in f:
            label = line[60:].strip()
            if label == "TIME OF FIRST OBS":
                p = line.split()
                first = datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4])) \
                    + timedelta(seconds=float(p[5]))
            elif label == "TIME OF LAST OBS":
                p = line.split()
                last = datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4])) \
                    + timedelta(seconds=float(p[5]))
            elif label == "END OF HEADER":
                break
    if not first or not last or last <= first:
        fail(f"could not read TIME OF FIRST/LAST OBS from {obs_path}")
    return first, last


PROGRESS_RE = re.compile(
    r"processing\s*:\s*(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)\s+Q=(\d)")


def solve(conf, rover_obs, base_obs, rover_nav, base_nav, out_pos):
    """Run rnx2rtkp on the flight, drawing a live progress bar from its output.

    rnx2rtkp streams 'processing : <time> Q=<n>' status lines; we parse each one
    to advance the bar (elapsed time vs the OBS window) and tally the running
    FIX-so-far percentage. Fails loudly on a non-zero exit or an empty .pos."""
    t0, t1 = _obs_window(rover_obs)
    span = (t1 - t0).total_seconds()
    cmd = [RNX2RTKP, "-k", conf, "-o", out_pos, rover_obs, base_obs, rover_nav, base_nav]

    q_fix = q_all = 0
    shown = -1
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    buf = ""
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        # rnx2rtkp separates status updates with \r; keep the tail fragment
        parts = re.split(r"[\r\n]", buf)
        buf = parts.pop()
        for part in parts:
            m = PROGRESS_RE.search(part)
            if not m:
                continue
            y, mo, d, h, mi = (int(x) for x in m.groups()[:5])
            t = datetime(y, mo, d, h, mi) + timedelta(seconds=float(m.group(6)))
            q = int(m.group(7))
            if q > 0:                         # inside the rover window, solved
                q_all += 1
                q_fix += (q == 1)
            pct = min(100, max(0, (t - t0).total_seconds() / span * 100))
            if int(pct) > shown:              # redraw only on whole-% changes
                shown = int(pct)
                bar = "#" * (shown * 30 // 100)
                fix_pct = (100 * q_fix / q_all) if q_all else 0.0
                print(f"\r  solving  [{bar:<30}] {shown:3d}%   "
                      f"{t:%H:%M:%S}   fix-so-far {fix_pct:5.1f}%", end="", flush=True)
    proc.wait()
    print()
    if proc.returncode != 0:
        fail(f"rnx2rtkp exited with code {proc.returncode}")
    if not os.path.exists(out_pos) or os.path.getsize(out_pos) == 0:
        fail(f"rnx2rtkp produced no solution file at {out_pos}")


# ----------------------------------------------------------------- 5. report

def _breakdown(pos_path, label, lines_out):
    """Count Q (fix/float/single) flags in a .pos file and print the percentages.

    Appends the printed line to lines_out (so report() can also save it to
    report.txt) and returns (epochs_dict, FIX_percentage)."""
    epochs = parse_pos(pos_path)
    counts = {}
    for _, _, _, q in epochs.values():
        counts[q] = counts.get(q, 0) + 1
    total = len(epochs)
    parts = [f"{QNAME.get(q, q)} {100*n/total:.2f}% ({n}/{total})"
             for q, n in sorted(counts.items())]
    line = f"  {label:9s}: " + "   ".join(parts)
    print(line)
    lines_out.append(line)
    return epochs, 100 * counts.get(1, 0) / total


def report(out_pos, events_pos, n_photos, report_path):
    """Print + save the FIX/FLOAT breakdown for the full track and per-photo events.

    Hard-fails if the event count != photo count (that would mean we can't
    reliably pair positions to photos). Returns (events, fix_pct, ev_fix_pct)."""
    lines = []
    _, fix_pct = _breakdown(out_pos, "Solution", lines)
    if not os.path.exists(events_pos) or os.path.getsize(events_pos) == 0:
        fail(f"no event positions at {events_pos} — event injection failed?")
    events, ev_fix_pct = _breakdown(events_pos, "Events", lines)
    if len(events) != n_photos:
        fail(f"{len(events)} event positions but {n_photos} photos — refusing to tag")
    with open(report_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return events, fix_pct, ev_fix_pct


# ------------------------------------------------------------- 6. tag_photos

def _deg_to_dms_rationals(value):
    """abs(decimal degrees) -> ((d,1),(m,1),(s*1e7,1e7)) EXIF rationals."""
    value = abs(Fraction(value).limit_denominator(10**9))
    d = int(value)
    m = int((value - d) * 60)
    s = (value - d - Fraction(m, 60)) * 3600
    s = Fraction(s).limit_denominator(10**7)
    return ((d, 1), (m, 1), (s.numerator, s.denominator))


def _antenna_to_camera(lat, lon, hgt, rec):
    """Apply the per-photo MRK lever arm (antenna phase center -> camera
    center, N/E/V-down in mm) — the same correction Emlid Studio applies to
    its event positions (verified: ES events = antenna events + this offset,
    residual ~0.5 cm on the reference flight)."""
    import math
    lat += (rec["lever_n_mm"] / 1000.0) / 111132.95
    lon += (rec["lever_e_mm"] / 1000.0) / (111319.49 * math.cos(math.radians(lat)))
    hgt -= rec["lever_v_mm"] / 1000.0
    return lat, lon, hgt


def tag_photos(events, mrk_records, jpg_by_id, tagged_dir, csv_path):
    """events: {time_str: (lat, lon, h, q)} from _events.pos, in shutter order.
    Event i corresponds to MRK row i (both chronological); the MRK row's
    photo_index picks the JPG — the 1:1 match validated in Task 1."""
    import csv

    import piexif

    os.makedirs(tagged_dir, exist_ok=True)
    ev_list = [events[t] for t in sorted(events)]
    if len(ev_list) != len(mrk_records):
        fail(f"{len(ev_list)} events vs {len(mrk_records)} MRK rows — cannot pair")

    n = 0
    csv_rows = []
    for rec, (lat, lon, hgt, q) in zip(mrk_records, ev_list):
        lat, lon, hgt = _antenna_to_camera(lat, lon, hgt, rec)
        jpg = jpg_by_id.get(f"{rec['photo_index']:04d}")
        if jpg is None:
            fail(f"no photo for MRK index {rec['photo_index']}")
        dst = os.path.join(tagged_dir, os.path.basename(jpg))
        shutil.copy2(jpg, dst)

        exif = piexif.load(dst)
        alt = Fraction(abs(hgt)).limit_denominator(10**6)
        exif["GPS"] = {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _deg_to_dms_rationals(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _deg_to_dms_rationals(lon),
            piexif.GPSIFD.GPSAltitudeRef: 0 if hgt >= 0 else 1,
            piexif.GPSIFD.GPSAltitude: (alt.numerator, alt.denominator),
            piexif.GPSIFD.GPSStatus: b"A",
            piexif.GPSIFD.GPSMapDatum: b"WGS-84",
            piexif.GPSIFD.GPSProcessingMethod: b"ASCII\x00\x00\x00" +
                (b"PPK FIX" if q == 1 else b"PPK " + QNAME.get(q, "?").encode()),
        }
        piexif.insert(piexif.dump(exif), dst)
        csv_rows.append({"photo": os.path.basename(jpg), "lat": f"{lat:.9f}",
                         "lon": f"{lon:.9f}", "ellh": f"{hgt:.4f}",
                         "q": QNAME.get(q, q)})
        n += 1

    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["photo", "lat", "lon", "ellh", "q"])
        w.writeheader()
        w.writerows(csv_rows)
    return n


# ------------------------------------------------------------------- 7. main

def run_flight(idx, total, flight_dir, base_dir, min_fix):
    """Run all six stages for one flight and return a summary-row dict.

    Ties the pipeline together: discover -> build_config -> inject_events ->
    solve -> report -> tag_photos, printing a line of progress per stage."""
    name = os.path.basename(os.path.normpath(flight_dir))
    print(f"\nFlight {idx}/{total}: {name}")
    files = discover(flight_dir, base_dir)
    out_dir = os.path.join(OUT_ROOT, name)
    os.makedirs(out_dir, exist_ok=True)

    conf = os.path.join(out_dir, "flight.conf")
    antdelu = build_config(files["base_obs"], conf)
    print(f"  config   : base antenna height {antdelu:.3f} m -> flight.conf")

    mrk_records = parse_mrk_file(files["mrk"])
    obs_ev = os.path.join(out_dir, "rover_events.obs")
    n_ev = inject_events(files["obs"], mrk_records, obs_ev)
    print(f"  events   : {n_ev} shutter marks injected from MRK")

    out_pos = os.path.join(out_dir, "solution.pos")
    solve(conf, obs_ev, files["base_obs"], files["nav"], files["base_nav"], out_pos)

    events, fix_pct, ev_fix_pct = report(
        out_pos, os.path.join(out_dir, "solution_events.pos"),
        len(mrk_records), os.path.join(out_dir, "report.txt"))

    tagged_dir = os.path.join(out_dir, "tagged")
    n_tag = tag_photos(events, mrk_records, files["jpg_by_id"], tagged_dir,
                       os.path.join(out_dir, "camera_positions.csv"))
    print(f"  Tagged   : {n_tag} JPGs (antenna->camera lever arm applied) -> {tagged_dir}")

    ok = fix_pct >= min_fix
    if not ok:
        print(f"  !! WARNING: FIX {fix_pct:.2f}% is below --min-fix {min_fix}% — needs human review")
    return {"name": name, "fix": fix_pct, "ev_fix": ev_fix_pct,
            "events": len(events), "tagged": n_tag, "ok": ok}


def clean(target=None):
    """Delete pipeline output (everything in it is regenerable). With a flight
    name, delete just that flight's folder; without, delete all of output/."""
    path = os.path.join(OUT_ROOT, target) if target else OUT_ROOT
    if not os.path.isdir(path):
        print(f"{path} already gone — nothing to clean")
        return
    if os.path.commonpath([OUT_ROOT, os.path.abspath(path)]) != OUT_ROOT:
        fail(f"refusing to delete outside {OUT_ROOT}")
    shutil.rmtree(path)
    print(f"deleted {path}")


def main():
    """CLI entry point: parse args, run each flight, print the batch summary.

    Exit codes: 0 = all flights met --min-fix; 1 = a hard error stopped a run;
    2 = finished but at least one flight fell below --min-fix (needs review)."""
    # `python ppk_pipeline.py clean [flight_name]` -> wipe output and stop
    if len(sys.argv) > 1 and sys.argv[1] == "clean":
        clean(sys.argv[2] if len(sys.argv) > 2 else None)
        return

    ap = argparse.ArgumentParser(description="Batch PPK solve + fix report + geotagged JPGs")
    ap.add_argument("flights", nargs="+", help="flight folder(s) with OBS/NAV/MRK/JPGs")
    ap.add_argument("--base", required=True, help="folder with base RINEX (.yyO/.yyP)")
    ap.add_argument("--min-fix", type=float, default=95.0,
                    help="warn if solution FIX%% is below this (default 95)")
    args = ap.parse_args()

    if not os.path.exists(RNX2RTKP):
        fail(f"rnx2rtkp not found at {RNX2RTKP}")
    results = [run_flight(i, len(args.flights), fl, args.base, args.min_fix)
               for i, fl in enumerate(args.flights, 1)]

    print("\n" + "=" * 72)
    print(f"  {'flight':<42} {'FIX%':>7} {'evFIX%':>7} {'tagged':>7}")
    for r in results:
        flag = "OK" if r["ok"] else "** LOW FIX **"
        print(f"  {r['name']:<42} {r['fix']:6.2f}% {r['ev_fix']:6.2f}% "
              f"{r['tagged']:>4}/{r['events']:<4} {flag}")
    print("=" * 72)
    if not all(r["ok"] for r in results):
        sys.exit(2)


if __name__ == "__main__":
    main()
