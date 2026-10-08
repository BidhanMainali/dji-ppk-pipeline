"""
csrs.py - shared helpers for applying a CSRS-PPP base-station correction.

Used by:
  - gcp_shift.py      (correct GCP coordinates + emit the DJI Terra base coord)
  - ppk_pipeline.py   (override the RTKLIB base position for the drone solve)

Background: CSRS-PPP (Natural Resources Canada) reprocesses a base station's raw
RINEX against precise orbit/clock products and reports an accurate "Estimated
Position" in a global ITRS-aligned frame (ITRF / IGS). Feeding that position back
into processing removes the ~1 m bias a short Emlid on-device average can carry.

Note on frames: the CSRS position is in ITRF/IGS at the observation epoch, while
the Emlid base and GCPs are in whatever frame the receiver used. A base shift
computed from the two absorbs that frame/epoch difference (a few cm) along with
the averaging error it is meant to remove. That is intended for this use.

parse_sum() reads the "POS LAT/LON/HGT <frame> <epoch> ..." lines of the .sum
report - validated against a real NRCan CSRS-PPP report (reference frame
"IGc20"). `--csrs-pos` is the manual fallback, and callers cross-check the
parsed position against the RINEX header, so a mis-parse is caught rather than
silently trusted.
"""

import math
import re
from dataclasses import dataclass
from pathlib import Path

# WGS84 / GRS80 ellipsoid (the two differ by < 0.1 mm at these scales)
_A = 6378137.0
_E2 = (1 / 298.257223563) * (2 - 1 / 298.257223563)

_UNICODE_MINUS = "−"   # often sneaks in when copying from a PDF or web page

# a coordinate after punctuation is normalised: sign, degrees, optional min, sec
_COORD_RE = re.compile(
    r"([-+])?\s*(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?")


def parse_latlon(s, limit=None):
    """Parse a latitude/longitude string to signed decimal degrees.

    Accepts:
      - decimal degrees:           "-127.224246"
      - DMS with symbols:          "49° 56' 36.61548\\""
      - DMS space/colon separated: "49 56 36.61548"  or  "49:56:36.61548"

    Sign comes from a leading '-' (ASCII or Unicode minus) or a hemisphere letter
    as the first or last character (N/E => +, S/W => -), and applies to the whole
    value; minutes and seconds are always positive. Giving both a minus and a
    hemisphere letter is rejected as ambiguous. Pass limit=90 for a latitude or
    limit=180 for a longitude to reject out-of-range values.
    """
    s = s.strip().replace(_UNICODE_MINUS, "-")
    if not s:
        raise ValueError("empty coordinate string")

    # hemisphere letter only at either end (so the 'e' in "1e-5" is never East)
    hemi = None
    if s[0] in "NSEWnsew":
        hemi, s = s[0].upper(), s[1:]
    elif s[-1] in "NSEWnsew":
        hemi, s = s[-1].upper(), s[:-1]

    # normalise DMS punctuation to spaces
    for ch in ("°", "'", '"', ":"):
        s = s.replace(ch, " ")
    m = _COORD_RE.fullmatch(s.strip())
    if not m:
        raise ValueError(f"not a recognised coordinate: {s.strip()!r}")
    sign_char, deg_s, min_s, sec_s = m.groups()

    if hemi and sign_char == "-":
        raise ValueError(f"both a minus sign and hemisphere {hemi!r} given - ambiguous")
    negative = sign_char == "-" or hemi in ("S", "W")   # string test: "-0 30 0" stays negative

    deg = float(deg_s)
    minutes = float(min_s) if min_s else 0.0
    seconds = float(sec_s) if sec_s else 0.0
    if min_s and deg != int(deg):
        raise ValueError(f"fractional degrees combined with minutes in {s.strip()!r}")
    if not (0 <= minutes < 60) or not (0 <= seconds < 60):
        raise ValueError(f"minutes/seconds out of range in {s.strip()!r}")

    value = deg + minutes / 60.0 + seconds / 3600.0
    value = -value if negative else value
    if limit is not None and abs(value) > limit:
        raise ValueError(f"{value:.6f} is outside +/-{limit} degrees - "
                         "check for a swapped latitude/longitude")
    return value


def parse_llh(lat, lon, h):
    """Parse a (lat, lon, height) triple of strings, e.g. from a 3-value CLI flag,
    into (lat_deg, lon_deg, h_m) with latitude/longitude range checks."""
    lat_d = parse_latlon(lat, limit=90)
    lon_d = parse_latlon(lon, limit=180)
    try:
        h_m = float(h)
    except ValueError:
        raise ValueError(f"height {h!r} is not a number (metres)") from None
    return lat_d, lon_d, h_m


def read_delta_h(base_obs):
    """Return the base antenna height (metres) from a RINEX
    'ANTENNA: DELTA H/E/N' header line - the first (H) number.

    Shared by ppk_pipeline.build_config() and gcp_shift.py.
    Raises ValueError if the line is absent or unreadable."""
    with open(base_obs, encoding="ascii", errors="replace") as f:
        for line in f:
            label = line[60:].strip()
            if label == "ANTENNA: DELTA H/E/N":
                try:
                    return float(line[:14])
                except ValueError:
                    raise ValueError(
                        f"unreadable 'ANTENNA: DELTA H/E/N' value in {base_obs}: "
                        f"{line[:14]!r}") from None
            if label == "END OF HEADER":
                break
    raise ValueError(f"no 'ANTENNA: DELTA H/E/N' header in {base_obs}")


@dataclass(repr=False)
class CSRSResult:
    """The estimated base position from a CSRS-PPP run (ground marker)."""
    lat: float                  # signed decimal degrees
    lon: float                  # signed decimal degrees
    ellh: float                 # ellipsoidal height, metres (the marker)
    datum: str = None           # e.g. "ITRF2020" or "IGc20"
    epoch: str = None           # e.g. "26:240:83070" (yy:ddd:sssss, as printed)
    sigmas: tuple = None        # (s_lat, s_lon, s_h) metres, or None

    def __repr__(self):
        return (f"CSRSResult(lat={self.lat:.8f}, lon={self.lon:.8f}, "
                f"ellh={self.ellh:.3f}, datum={self.datum}, epoch={self.epoch})")


# --- .sum parsing --------------------------------------------------------------

_POS_RE = re.compile(r"^POS\s+(LAT|LON|HGT)\s+(\S+)\s+(\S+)\s+(.*)$", re.M)
# global, ITRS-aligned frames: ITRF2014 / ITRF20 / IGS20 / IGb14 / IGc20 ...
_GLOBAL_FRAME_RE = re.compile(r"(ITRF\d{2,4}|IG[A-Za-z]\d{2})", re.I)
_INT_RE = re.compile(r"[-+]?\d+")
_UINT_RE = re.compile(r"\d+")
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _dms_pair(rest, label):
    """Split '<a-priori D M S> <estimated D M S> [diff ...]' into the two DMS
    strings plus the reported diff (metres) if present. Each triple is validated
    token by token, so a shifted or missing column fails instead of silently
    reading the wrong number."""
    toks = rest.split()
    if len(toks) < 6:
        raise ValueError(f"expected a-priori + estimated {label} D M S, got {rest!r}")
    for i in (0, 3):
        d, m, s = toks[i:i + 3]
        if not (_INT_RE.fullmatch(d) and _UINT_RE.fullmatch(m) and _NUM_RE.fullmatch(s)):
            raise ValueError(f"{label} columns don't look like D M S at {toks[i:i + 3]}")
    diff = float(toks[6]) if len(toks) > 6 and _NUM_RE.fullmatch(toks[6]) else None
    return " ".join(toks[0:3]), " ".join(toks[3:6]), diff


def _scalar_pair(rest, label):
    """Split '<a-priori> <estimated> [diff ...]' into floats (+ reported diff)."""
    toks = rest.split()
    if len(toks) < 2 or not (_NUM_RE.fullmatch(toks[0]) and _NUM_RE.fullmatch(toks[1])):
        raise ValueError(f"expected a-priori + estimated {label} numbers, got {rest!r}")
    diff = float(toks[2]) if len(toks) > 2 and _NUM_RE.fullmatch(toks[2]) else None
    return float(toks[0]), float(toks[1]), diff


def _check_diff(label, computed_m, reported_m):
    """The report prints 'estimated - a priori' in metres. If our own difference
    disagrees, the columns were read from the wrong positions. A missing diff
    column means the layout changed - exactly the case this check exists for -
    so that fails too rather than silently skipping the check."""
    if reported_m is None:
        raise ValueError(f"{label}: no 'estimated - a priori' column to cross-check "
                         "against - the report layout looks different from expected")
    if abs(computed_m - reported_m) > 0.01 + 0.005 * abs(reported_m):
        raise ValueError(
            f"{label}: estimated - a-priori is {computed_m:+.4f} m from the parsed "
            f"columns but the report says {reported_m:+.4f} m - columns are misaligned")


def parse_sum(path):
    """Parse a CSRS-PPP `.sum` report into a CSRSResult (estimated global position).

    Reads the "POS LAT/LON/HGT <frame> <epoch> <a priori> <estimated> <diff> ..."
    lines NRCan's CSRS-PPP report prints, e.g. (real report, frame "IGc20"):

        POS LAT IGc20 26:240:83070    49 56 36.91448    49 56 36.82477   -2.7717 ...
        POS LON IGc20 26:240:83070  -127 13 27.66091  -127 13 27.65521    0.1137 ...
        POS HGT IGc20 26:240:83070           22.1232           16.3044   -5.8188 ...

    The lines are grouped by (frame, epoch) and only a group that has all three
    lines AND a global ITRS-aligned frame (ITRFxxxx / IGb.. / IGc.. / IGS..) is
    used, so a report that also carries a NAD83(CSRS) block is handled whichever
    block comes first, and LAT/LON/HGT can never be mixed across blocks.

    Each estimate is cross-checked against the report's own "estimated - a
    priori" metres column, so a misaligned column fails loudly. Use --csrs-pos
    when a report can't be parsed. Callers additionally cross-check the result
    against the RINEX-header position.
    """
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")

    groups = {}                                  # (frame, epoch) -> {label: rest}
    for m in _POS_RE.finditer(text):
        label, frame, epoch, rest = m.groups()
        groups.setdefault((frame, epoch), {})[label] = rest
    if not groups:
        raise ValueError(
            f"{path}: could not find 'POS LAT/LON/HGT' lines - this doesn't look "
            "like a CSRS-PPP .sum report. Use --csrs-pos to enter the position "
            "manually.")

    complete = [(k, v) for k, v in groups.items() if {"LAT", "LON", "HGT"} <= v.keys()]
    usable = [(k, v) for k, v in complete if _GLOBAL_FRAME_RE.fullmatch(k[0])]
    if not usable:
        frames = ", ".join(sorted({k[0] for k in groups}))
        if any("NAD83" in k[0].upper() for k in groups):
            reason = ("only a NAD83(CSRS) solution (a Canada-fixed datum) was found; "
                      "the pipeline needs the GLOBAL ITRF/IGS solution.")
        else:
            reason = "no complete LAT/LON/HGT block in a global ITRF/IGS frame."
        raise ValueError(f"{path}: {reason} Frames seen: {frames}. Use --csrs-pos "
                         "to enter the position manually if this is the right file.")
    if len(usable) > 1:
        which = ", ".join(f"{k[0]} @ {k[1]}" for k, _ in usable)
        raise ValueError(f"{path}: more than one global solution block ({which}) - "
                         "ambiguous which to use. Use --csrs-pos to enter the "
                         "position manually.")
    (datum, epoch), lines = usable[0]

    try:
        lat_ap_s, lat_est_s, lat_diff = _dms_pair(lines["LAT"], "LAT")
        lon_ap_s, lon_est_s, lon_diff = _dms_pair(lines["LON"], "LON")
        h_ap, ellh, h_diff = _scalar_pair(lines["HGT"], "HGT")
        lat_ap, lat = parse_latlon(lat_ap_s, 90), parse_latlon(lat_est_s, 90)
        lon_ap, lon = parse_latlon(lon_ap_s, 180), parse_latlon(lon_est_s, 180)

        # metres per radian along the meridian (M) and the parallel (N cos lat)
        sin2 = math.sin(math.radians(lat)) ** 2
        m_rad = _A * (1 - _E2) / (1 - _E2 * sin2) ** 1.5
        n_rad = _A / math.sqrt(1 - _E2 * sin2)
        _check_diff("LAT", math.radians(lat - lat_ap) * m_rad, lat_diff)
        _check_diff("LON", math.radians(lon - lon_ap) * n_rad
                    * math.cos(math.radians(lat)), lon_diff)
        _check_diff("HGT", ellh - h_ap, h_diff)
    except ValueError as e:
        raise ValueError(
            f"{path}: found POS LAT/LON/HGT lines ({datum}) but could not parse the "
            f"estimated position ({e}). Use --csrs-pos to enter the position "
            "manually.") from e

    return CSRSResult(lat, lon, ellh, datum=datum, epoch=epoch)
