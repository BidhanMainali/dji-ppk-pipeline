"""Tests for gcp_shift.py - the automated CSRS-PPP GCP shift."""

import csv
import math
import os
import subprocess
import sys

import pytest

import gcp_shift as g
from conftest import PPK_CLI

HEADER = ["Name", "Longitude", "Latitude", "Ellipsoidal height",
          "Base longitude", "Base latitude", "Base ellipsoidal height", "CS name"]
BASE = ("-123.3071000", "48.4566700", "40.000")


def _rows(*gcps, base=BASE):
    """Build Emlid-style CSV rows: each gcp is (name, lon, lat, h)."""
    return [dict(zip(HEADER, [n, lo, la, h, base[0], base[1], base[2], "Local"]))
            for n, lo, la, h in gcps]


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    return str(path)


def _llh_to_ecef(lat, lon, h):
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    la, lo = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - e2) + h) * math.sin(la))


# --- the shift itself -----------------------------------------------------------

def test_compute_shift_matches_html_calculator():
    s = g.compute_shift((49.944, -127.224, 18.0), (49.94411799, -127.22434867, 16.5044), 1.895)
    assert s["marker"] == 16.5044
    assert s["terra_h"] == pytest.approx(18.3994)
    assert s["du"] == pytest.approx(0.3994)          # (ph + dh) - bh, as the HTML tool does
    assert s["dlat"] == pytest.approx(0.00011799)
    assert s["dlon"] == pytest.approx(-0.00034867)


def test_fix_csv_shifts_rows_and_rewrites_base(tmp_path):
    rows = _rows(("GCP1", "-127.2240000", "49.9440000", "50.000"))
    s = g.compute_shift((49.944, -127.224, 18.0), (49.94411799, -127.22434867, 16.5044), 1.895)
    out = tmp_path / "o.csv"
    assert g.fix_csv(rows, HEADER, s, str(out)) == 1
    r = rows[0]
    assert r["Longitude"] == "-127.22434867"
    assert r["Latitude"] == "49.94411799"
    assert r["Ellipsoidal height"] == "50.399"
    assert r["Base ellipsoidal height"] == "16.504"
    assert r["CS name"] == "Global CS (ITRF2020)"


@pytest.mark.parametrize("bad", ["", "abc", "nan", "inf", "48,456"],
                         ids=["blank", "text", "nan", "inf", "comma-decimal"])
def test_fix_csv_fails_on_bad_coordinate_instead_of_skipping(tmp_path, bad):
    rows = _rows(("GCP1", "-123.30710", "48.45667", "45.0"), ("GCP2", "-123.30711", bad, "45.0"))
    s = g.compute_shift((48.45667, -123.3071, 40.0), (48.45668, -123.30711, 38.5), 1.8)
    with pytest.raises(SystemExit):
        g.fix_csv(rows, HEADER, s, str(tmp_path / "o.csv"))
    assert not (tmp_path / "o.csv").exists()


def test_fix_csv_fails_on_empty_csv(tmp_path):
    s = g.compute_shift((48.45667, -123.3071, 40.0), (48.45668, -123.30711, 38.5), 1.8)
    with pytest.raises(SystemExit):
        g.fix_csv([], HEADER, s, str(tmp_path / "o.csv"))


def test_fix_csv_neutralises_spreadsheet_formulas(tmp_path):
    rows = _rows(("=HYPERLINK(\"x\")", "-123.30710", "48.45667", "45.0"))
    s = g.compute_shift((48.45667, -123.3071, 40.0), (48.45668, -123.30711, 38.5), 1.8)
    g.fix_csv(rows, HEADER, s, str(tmp_path / "o.csv"))
    assert rows[0]["Name"].startswith("'=")
    assert rows[0]["Longitude"].startswith("-")          # numeric columns untouched


# --- original base position -------------------------------------------------------

def test_base_position_read_from_csv():
    rows = _rows(("GCP1", "-123.3", "48.4", "45.0"), ("GCP2", "-123.3", "48.4", "46.0"))
    assert g.read_base_pos_from_csv(rows) == (48.45667, -123.3071, 40.0)


def test_base_position_inconsistent_across_rows_fails():
    rows = (_rows(("GCP1", "-123.3", "48.4", "45.0"))
            + _rows(("GCP2", "-123.3", "48.4", "46.0"), base=("-123.3071000", "48.4566700", "41.500")))
    with pytest.raises(SystemExit):
        g.read_base_pos_from_csv(rows)


# --- the CLI end to end -------------------------------------------------------------

@pytest.fixture
def cli(tmp_path, make_rinex):
    """Run gcp_shift.py as a user would, against a synthetic base + GCP CSV."""
    gcp = _write_csv(tmp_path / "gcps.csv", _rows(("GCP1", "-123.3071000", "48.4566700", "45.000")))
    base = make_rinex(approx=_llh_to_ecef(48.45667, -123.3071, 41.8), delta_h=1.8)

    def _run(*args, dh_base=None):
        cmd = [sys.executable, os.path.join(PPK_CLI, "gcp_shift.py"),
               "--gcp", gcp, "--base", dh_base or base, *args]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    return _run


def test_cli_normal_run_writes_csv(cli, tmp_path):
    r = cli("--csrs-pos", "48 27 24.1", "-123 18 25.6", "38.5")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "gcp_corrected.csv").exists()


def test_cli_refuses_absurd_shift_unless_forced(cli, tmp_path):
    r = cli("--csrs-pos", "48 27 24.1", "123 18 25.6", "38.5")      # longitude sign typo
    assert r.returncode == 1 and not (tmp_path / "gcp_corrected.csv").exists()
    r = cli("--csrs-pos", "48 27 24.1", "123 18 25.6", "38.5", "--force")
    assert r.returncode == 0 and (tmp_path / "gcp_corrected.csv").exists()


def test_cli_bad_coordinate_gives_clean_error(cli):
    r = cli("--csrs-pos", "bogus", "-123 18 25.6", "38.5")
    assert r.returncode == 1 and "Traceback" not in r.stderr


def test_cli_rejects_missing_antenna_height(cli, make_rinex):
    r = cli("--csrs-pos", "48 27 24.1", "-123 18 25.6", "38.5",
            dh_base=make_rinex(delta_h=0.0, name="zero.26O"))
    assert r.returncode == 1


def test_clean_only_deletes_its_own_output(tmp_path):
    keep = tmp_path / "emlid_export.csv"
    keep.write_text("x")
    script = os.path.join(PPK_CLI, "gcp_shift.py")
    r = subprocess.run([sys.executable, script, "clean", str(keep)], capture_output=True, text=True)
    assert r.returncode == 1 and keep.exists()

    mine = tmp_path / "gcp_corrected.csv"
    mine.write_text("x")
    r = subprocess.run([sys.executable, script, "clean", str(mine)], capture_output=True, text=True)
    assert r.returncode == 0 and not mine.exists()
