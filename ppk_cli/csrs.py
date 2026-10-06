"""
csrs.py - shared helpers for applying a CSRS-PPP base-station correction.

Used by:
  - gcp_shift.py      (correct GCP coordinates + emit the DJI Terra base coord)
  - ppk_pipeline.py   (override the RTKLIB base position for the drone solve)

Background: CSRS-PPP (Natural Resources Canada) reprocesses a base station's raw
RINEX against precise orbit/clock products and reports an accurate "Estimated
Position" in ITRF2020. Feeding that position back into processing removes the
~1 m bias a short Emlid on-device average can carry.

parse_sum() reads the "POS LAT/LON/HGT <frame> <epoch> ..." lines of the .sum
report - validated against a real NRCan CSRS-PPP report (reference frame
"IGc20"). `--csrs-pos` is the manual fallback, and callers cross-check the
parsed position against the RINEX header, so a mis-parse is caught rather than
silently trusted.
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
        self.datum = datum        # e.g. "ITRF2020" or "IGc20"
        self.epoch = epoch        # e.g. "26:240:83070" (yy:ddd:sssss, as printed)
        self.sigmas = sigmas      # (s_lat, s_lon, s_h) metres, or None

    def __repr__(self):
        return (f"CSRSResult(lat={self.lat:.8f}, lon={self.lon:.8f}, "
                f"ellh={self.ellh:.3f}, datum={self.datum}, epoch={self.epoch})")


def parse_sum(path):
    """Parse a CSRS-PPP `.sum` report into a CSRSResult (estimated global position).

    Reads the "POS LAT/LON/HGT <frame> <epoch> <a priori> <estimated> <diff> ..."
    lines NRCan's CSRS-PPP report prints for the GLOBAL (ITRS-aligned) solution,
    e.g. (real report, reference frame "IGc20"):

        POS LAT IGc20 26:240:83070    49 56 36.91448    49 56 36.82477   -2.7717 ...
        POS LON IGc20 26:240:83070  -127 13 27.66091  -127 13 27.65521    0.1137 ...
        POS HGT IGc20 26:240:83070           22.1232           16.3044   -5.8188 ...

    LAT/LON values are a-priori-then-estimated DMS triples; HGT is a-priori-then-
    estimated single decimal numbers. The reference frame tag varies by report
    vintage - older runs say "ITRFxxxx", current ones say "IGb.."/"IGc.." (an IGS
    realization of ITRF) - both are ITRS-aligned geocentric frames so either is
    accepted; a "NAD83(CSRS)" run (Canada-fixed, not ITRS-aligned) is rejected.

    Fails loudly if the POS LAT/LON/HGT lines aren't found or don't parse, so a
    bad parse never masquerades as a good position - use --csrs-pos instead.
    Callers additionally cross-check the result against the RINEX-header position.
    """
    text = open(path, errors="replace").read()

    def _pos_line(label):
        return re.search(rf"^POS\s+{label}\s+(\S+)\s+(\S+)\s+(.*)$", text, re.M)

    lat_m, lon_m, hgt_m = _pos_line("LAT"), _pos_line("LON"), _pos_line("HGT")
    if not (lat_m and lon_m and hgt_m):
        raise ValueError(
            f"{path}: could not find 'POS LAT/LON/HGT' lines - this doesn't look "
            "like a CSRS-PPP .sum report. Use --csrs-pos to enter the position "
            "manually.")

    datum, epoch = lat_m.group(1), lat_m.group(2)
    if "NAD83" in datum.upper():
        raise ValueError(
            f"{path}: position is in {datum} (NAD83(CSRS), a Canada-fixed datum), "
            "not a global ITRS frame (ITRFxxxx / IGb.. / IGc..). The pipeline "
            "needs the GLOBAL solution. If this really is the right file, use "
            "--csrs-pos to enter the position manually.")

    def _dms_estimated(rest, label):
        # rest: "<apriori deg min sec>   <estimated deg min sec>   <diff> ..."
        toks = rest.split()
        if len(toks) < 6:
            raise ValueError(f"expected a-priori + estimated {label} DMS, got {rest!r}")
        return " ".join(toks[3:6])

    def _scalar_estimated(rest, label):
        # rest: "<apriori>   <estimated>   <diff> ..."
        toks = rest.split()
        if len(toks) < 2:
            raise ValueError(f"expected a-priori + estimated {label}, got {rest!r}")
        return toks[1]

    try:
        lat = parse_latlon(_dms_estimated(lat_m.group(3), "LAT"))
        lon = parse_latlon(_dms_estimated(lon_m.group(3), "LON"))
        ellh = float(_scalar_estimated(hgt_m.group(3), "HGT"))
    except (ValueError, IndexError) as e:
        raise ValueError(
            f"{path}: found POS LAT/LON/HGT lines but could not parse the "
            f"estimated position ({e}). Use --csrs-pos to enter the position "
            "manually.") from e

    return CSRSResult(lat, lon, ellh, datum=datum, epoch=epoch)
