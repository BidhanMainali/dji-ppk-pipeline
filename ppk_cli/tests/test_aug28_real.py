"""Regression tests on the real 2026-08-28 survey, plus the self-test gate.

tests/fixtures/ holds trimmed copies of that day's real files (base RINEX
header, CSRS-PPP POS block, three GCP rows with the Author column anonymised).
The expected numbers match the team's HTML GCP shift calculator, and the height
formula reflects what the field crew confirmed: Flow's base antenna height is
left at 0, so the CSV 'Base ellipsoidal height' is the antenna, not the ground.
"""

import csv
import os
import subprocess
import sys

import pytest

import gcp_shift
from csrs import parse_sum, read_delta_h

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    return os.path.join(FIXTURES, name)


def run_main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["gcp_shift.py", *args])
    gcp_shift.main()


def std_args(out):
    return ["--gcp", fixture("gcp_aug28.csv"), "--base", fixture("base_aug28.26O"),
            "--csrs", fixture("aug28.sum"), "--out", str(out)]


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- real inputs ---------------------------------------------------------------

def test_real_sum_report():
    res = parse_sum(fixture("aug28.sum"))
    assert res.lat == pytest.approx(49.94356244, abs=1e-8)
    assert res.lon == pytest.approx(-127.22434867, abs=1e-8)
    assert res.ellh == pytest.approx(16.3044, abs=1e-6)
    assert (res.datum, res.epoch) == ("IGc20", "26:240:83070")


def test_real_sum_nad83_only_rejected():
    with pytest.raises(ValueError, match="NAD83"):
        parse_sum(fixture("nad83_only.sum"))


def test_real_sum_global_block_after_nad83():
    assert parse_sum(fixture("nad83_then_itrf.sum")).datum == "IGc20"


def test_real_rinex_delta_h():
    assert read_delta_h(fixture("base_aug28.26O")) == 1.13


def test_real_csv_base_position():
    with open(fixture("gcp_aug28.csv"), newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert gcp_shift.read_base_pos_from_csv(rows) == (49.94356587, -127.22435111, 19.147)


# --- the shift -----------------------------------------------------------------

def test_aug28_shift():
    s = gcp_shift.compute_shift((49.94356587, -127.22435111, 19.147),
                                (49.94356244, -127.22434867, 16.3044), 1.13)
    assert s["m_n"] == pytest.approx(-0.38, abs=0.005)
    assert s["m_e"] == pytest.approx(+0.17, abs=0.005)
    assert s["du"] == pytest.approx(-1.71, abs=0.005)
    assert s["terra_h"] == pytest.approx(17.4344)


def test_csv_base_height_is_treated_as_antenna():
    # crew leaves Flow's base antenna height at 0 -> du = (ph + dh) - bh, not ph - bh
    s = gcp_shift.compute_shift((0.0, 0.0, 10.0), (0.0, 0.0, 9.0), 1.5)
    assert s["du"] == pytest.approx(0.5)


# --- end to end ----------------------------------------------------------------

def test_aug28_end_to_end_matches_html_calculator(monkeypatch, tmp_path):
    out = tmp_path / "gcp_corrected.csv"
    run_main(monkeypatch, *std_args(out))
    rows = read_rows(out)
    assert [(r["Longitude"], r["Latitude"], r["Ellipsoidal height"]) for r in rows] == [
        ("-127.22411521", "49.94367243", "16.086"),
        ("-127.22424531", "49.94386783", "18.221"),
        ("-127.22116381", "49.94037269", "-11.636"),
    ]
    for r in rows:
        assert (r["Base latitude"], r["Base longitude"], r["Base ellipsoidal height"]) == \
            ("49.94356244", "-127.22434867", "16.304")
        assert r["CS name"] == "Global CS (ITRF2020)"
        assert r["Antenna height"] == "1.895"          # rover pole height, untouched


def test_manual_csrs_pos_matches_sum(monkeypatch, tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    run_main(monkeypatch, *std_args(a))
    run_main(monkeypatch, "--gcp", fixture("gcp_aug28.csv"), "--base", fixture("base_aug28.26O"),
             "--csrs-pos", "49 56 36.82477", "-127 13 27.65521", "16.3044", "--out", str(b))
    assert a.read_text() == b.read_text()


def test_swapped_csrs_pos_writes_nothing(monkeypatch, tmp_path):
    out = tmp_path / "o.csv"
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "--gcp", fixture("gcp_aug28.csv"), "--base", fixture("base_aug28.26O"),
                 "--csrs-pos", "-127.22434867", "49.94356244", "16.3", "--out", str(out))
    assert not out.exists()


# --- self-test gate ------------------------------------------------------------

def test_self_tests_skipped_inside_suite(monkeypatch, tmp_path):
    monkeypatch.setattr(gcp_shift, "run_self_tests",
                        lambda: pytest.fail("suite must not start itself"))
    run_main(monkeypatch, *std_args(tmp_path / "o.csv"))


def test_self_tests_run_before_processing(monkeypatch, tmp_path):
    monkeypatch.delenv("GCP_SHIFT_SKIP_TESTS")
    called = []
    monkeypatch.setattr(gcp_shift, "run_self_tests", lambda: called.append(1))
    run_main(monkeypatch, *std_args(tmp_path / "o.csv"))
    assert called == [1]


def test_skip_tests_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("GCP_SHIFT_SKIP_TESTS")
    monkeypatch.setattr(gcp_shift, "run_self_tests",
                        lambda: pytest.fail("--skip-tests was ignored"))
    run_main(monkeypatch, *std_args(tmp_path / "o.csv"), "--skip-tests")


def test_failing_suite_stops_the_run(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, returncode=1))
    with pytest.raises(SystemExit):
        gcp_shift.run_self_tests()
