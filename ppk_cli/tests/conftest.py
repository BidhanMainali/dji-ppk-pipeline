"""Shared pytest fixtures for the PPK pipeline tests.

Everything here is synthetic - the tests never touch real drone or base-station
data (that is gitignored and won't exist on a fresh clone).
Run from the repo root or ppk_cli/:   python -m pytest ppk_cli/tests -v
"""

import os
import sys

import pytest

PPK_CLI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PPK_CLI)

# A real CSRS-PPP report's global-frame block (values from an NRCan IGc20 run).
SUM_GLOBAL = (
    "POS LAT IGc20 26:240:83070    49 56 36.91448    49 56 36.82477   -2.7717 0.004\n"
    "POS LON IGc20 26:240:83070  -127 13 27.66091  -127 13 27.65521    0.1137 0.004\n"
    "POS HGT IGc20 26:240:83070           22.1232           16.3044   -5.8188 0.014\n"
)

# Same block re-labelled as a NAD83(CSRS) solution, with a different latitude
# (and a matching diff column) so tests can tell which block was picked.
SUM_NAD83 = (SUM_GLOBAL.replace("IGc20", "NAD83(CSRS)")
             .replace("36.82477", "36.80000").replace("-2.7717", "-3.4775"))


def _header_line(content, label):
    """One RINEX header line: content in columns 1-60, label from column 61."""
    return f"{content:<60}{label}\n"


@pytest.fixture
def make_rinex(tmp_path):
    """Factory: write a minimal RINEX observation header and return its path.

    approx      -- (X, Y, Z) ECEF metres for APPROX POSITION XYZ, or None to omit
    delta_h     -- antenna height for ANTENNA: DELTA H/E/N, or None to omit
    approx_raw  -- write this literal text as the APPROX POSITION content instead
    """
    def _make(approx=None, delta_h=1.8, approx_raw=None, name="base.26O"):
        lines = [_header_line("     3.03           OBSERVATION DATA    M: Mixed",
                              "RINEX VERSION / TYPE")]
        if approx_raw is not None:
            lines.append(_header_line(approx_raw, "APPROX POSITION XYZ"))
        elif approx is not None:
            lines.append(_header_line("".join(f"{v:14.4f}" for v in approx),
                                      "APPROX POSITION XYZ"))
        if delta_h is not None:
            lines.append(_header_line(f"{delta_h:14.4f}{0.0:14.4f}{0.0:14.4f}",
                                      "ANTENNA: DELTA H/E/N"))
        lines.append(_header_line("", "END OF HEADER"))
        path = tmp_path / name
        path.write_text("".join(lines), encoding="ascii")
        return str(path)
    return _make


@pytest.fixture
def write_text(tmp_path):
    """Factory: write a UTF-8 text file into tmp_path and return its path."""
    def _write(name, text):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)
    return _write
