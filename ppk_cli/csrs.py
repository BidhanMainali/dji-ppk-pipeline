"""
csrs.py - shared helpers for applying a CSRS-PPP base-station correction.

Used by:
  - gcp_shift.py      (correct GCP coordinates + emit the DJI Terra base coord)
  - ppk_pipeline.py   (override the RTKLIB base position for the drone solve)

Background: CSRS-PPP (Natural Resources Canada) reprocesses a base station's raw
RINEX against precise orbit/clock products and reports an accurate "Estimated
Position" in ITRF2020. Feeding that position back into processing removes the
~1 m bias a short Emlid on-device average can carry.

IMPORTANT: parse_sum() is written against the CSRS report layout seen in the
sample PDF. The exact `.sum` text format has NOT been validated against a real
file yet - verify before production. `--csrs-pos` is the manual fallback, and
callers cross-check the parsed position against the RINEX header, so a mis-parse
is caught rather than silently trusted.
"""

import re


def parse_latlon(s):
    """Parse a latitude/longitude string to signed decimal degrees.

    Accepts:
      - decimal degrees:           "-127.224246"
      - DMS with symbols:          "49° 56' 36.61548\\""
      - DMS space/colon separated: "49 56 36.61548"  or  "49:56:36.61548"

    Sign comes from a leading '-' or a hemisphere letter (N/E => +, S/W => -) and
    applies to the whole value; minutes and seconds are always positive.
    """
    s = s.strip()
    if not s:
        raise ValueError("empty coordinate string")

    # hemisphere letter, if present at either end
    sign = 1.0
    hemi = re.search(r"[NSEWnsew]", s)
    if hemi:
        if hemi.group(0).upper() in ("S", "W"):
            sign = -1.0
        s = re.sub(r"[NSEWnsew]", " ", s)

    # normalise DMS punctuation to spaces, then pull the numbers
    for ch in ("°", "'", '"', ":"):
        s = s.replace(ch, " ")
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", s)
    if not nums:
        raise ValueError(f"no numeric value in coordinate: {s!r}")

    deg = float(nums[0])
    if deg < 0:                       # explicit negative degrees => negative
        sign = -1.0
        deg = -deg
    minutes = float(nums[1]) if len(nums) > 1 else 0.0
    seconds = float(nums[2]) if len(nums) > 2 else 0.0
    if not (0 <= minutes < 60) or not (0 <= seconds < 60):
        raise ValueError(f"minutes/seconds out of range in {s!r}")

    return sign * (deg + minutes / 60.0 + seconds / 3600.0)


def read_delta_h(base_obs):
    """Return the base antenna height (metres) from a RINEX
    'ANTENNA: DELTA H/E/N' header line - the first (H) number.

    This mirrors the reader in ppk_pipeline.build_config(); both now call here.
    Raises ValueError if the line is absent."""
    with open(base_obs, errors="replace") as f:
        for line in f:
            if line[60:].strip() == "ANTENNA: DELTA H/E/N":
                return float(line[:14])
            if line[60:].strip() == "END OF HEADER":
                break
    raise ValueError(f"no 'ANTENNA: DELTA H/E/N' header in {base_obs}")


class CSRSResult:
    """The estimated base position from a CSRS-PPP run (ground marker)."""

    def __init__(self, lat, lon, ellh, datum=None, epoch=None, sigmas=None):
        self.lat = lat            # signed decimal degrees
        self.lon = lon            # signed decimal degrees
        self.ellh = ellh          # ellipsoidal height, metres (the marker)
        self.datum = datum        # e.g. "ITRF2020"
        self.epoch = epoch        # e.g. "2026.7"
        self.sigmas = sigmas      # (s_lat, s_lon, s_h) metres, or None

    def __repr__(self):
        return (f"CSRSResult(lat={self.lat:.8f}, lon={self.lon:.8f}, "
                f"ellh={self.ellh:.3f}, datum={self.datum}, epoch={self.epoch})")


def parse_sum(path):
    """Parse a CSRS-PPP `.sum` report into a CSRSResult (estimated ITRF position).

    !! INFERRED FORMAT - validate against a real `.sum` before trusting it. !!

    Strategy: require an ITRF datum tag (rejects a NAD83 run), then locate the
    estimated LAT / LON / HGT lines. Fails loudly if anything can't be found so a
    bad parse never masquerades as a good position - use --csrs-pos instead.
    Callers additionally cross-check the result against the RINEX-header position.
    """
    text = open(path, errors="replace").read()

    # datum + optional epoch, e.g. "ITRF2020/IGc20 (2026.7)"
    dm = re.search(r"(ITRF\d{2,4})[^\n(]*(?:\(\s*(\d{4}\.\d+)\s*\))?", text)
    if not dm:
        raise ValueError(
            f"{path}: no ITRF datum found. The pipeline needs the ITRF2020 run "
            "(not NAD83). If this really is the right file, use --csrs-pos to "
            "enter the position manually.")
    datum, epoch = dm.group(1), dm.group(2)

    def _line_with(label):
        m = re.search(rf"^[^\n]*\b{label}\b[^\n]*$", text, re.I | re.M)
        return m.group(0) if m else None

    lat_line = _line_with("LAT")
    lon_line = _line_with("LON")
    hgt_line = _line_with("HGT") or _line_with("Ell")
    if not (lat_line and lon_line and hgt_line):
        raise ValueError(
            f"{path}: could not locate the estimated LAT/LON/HGT lines. The .sum "
            "layout may differ from what was expected - verify the file, or use "
            "--csrs-pos to enter the position manually.")

    try:
        lat = parse_latlon(lat_line.split(None, 1)[1])
        lon = parse_latlon(lon_line.split(None, 1)[1])
    except (IndexError, ValueError) as e:
        raise ValueError(
            f"{path}: found LAT/LON lines but could not parse them ({e}). "
            "Use --csrs-pos to enter the position manually.") from e

    hm = re.search(r"[-+]?\d+\.\d+", hgt_line)
    if not hm:
        raise ValueError(
            f"{path}: no numeric height in the estimated height line: "
            f"{hgt_line!r}. Use --csrs-pos to enter the position manually.")
    ellh = float(hm.group(0))

    return CSRSResult(lat, lon, ellh, datum=datum, epoch=epoch)
