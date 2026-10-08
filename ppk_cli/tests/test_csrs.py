"""Tests for csrs.py - coordinate parsing, RINEX antenna height and .sum parsing."""

import pytest

import csrs
from conftest import SUM_GLOBAL, SUM_NAD83


# --- parse_latlon ---------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("49 56 38.82477", 49.94411799),
    ("-127 13 27.65521", -127.22434867),
    ("49° 56' 36.61548\"", 49.94350430),
    ("49:56:36.61548", 49.94350430),
    ("-127.224246", -127.224246),
    ("48:29:12.5 N", 48.48680556),
    ("123 30 0 W", -123.5),
    ("S 33 51 0", -33.85),
    ("−127 13 27.65521", -127.22434867),   # Unicode minus from a PDF
    ("-0 30 0", -0.5),                          # sign must survive a zero degree
], ids=["dms", "dms-neg", "dms-symbols", "dms-colon", "decimal", "hemi-N",
        "hemi-W", "hemi-leading-S", "unicode-minus", "minus-zero-degrees"])
def test_parse_latlon_valid(text, expected):
    assert round(csrs.parse_latlon(text), 8) == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize("text", [
    "", "abc", "1e-5", "-49 56 0 N", "49 61 0", "49 30 60", "49.5 30 0", "1 2 3 4",
], ids=["empty", "letters", "exponent", "minus-and-hemisphere", "minutes-60+",
        "seconds-60", "fractional-deg-with-min", "too-many-fields"])
def test_parse_latlon_rejects(text):
    with pytest.raises(ValueError):
        csrs.parse_latlon(text)


def test_parse_latlon_limit():
    assert csrs.parse_latlon("89 59 59", limit=90) > 89
    with pytest.raises(ValueError, match="outside"):
        csrs.parse_latlon("91 0 0", limit=90)


def test_parse_llh_range_checks_and_height():
    assert csrs.parse_llh("48.5", "-123.3", "40.25") == (48.5, -123.3, 40.25)
    with pytest.raises(ValueError):
        csrs.parse_llh("123 0 0", "49 0 0", "10")      # lat/lon swapped
    with pytest.raises(ValueError, match="not a number"):
        csrs.parse_llh("48.5", "-123.3", "high")


# --- read_delta_h -----------------------------------------------------------------

def test_read_delta_h(make_rinex):
    assert csrs.read_delta_h(make_rinex(delta_h=1.28)) == pytest.approx(1.28)


def test_read_delta_h_missing_line(make_rinex):
    with pytest.raises(ValueError, match="no 'ANTENNA"):
        csrs.read_delta_h(make_rinex(delta_h=None))


# --- parse_sum ----------------------------------------------------------------------

def test_parse_sum_real_format(write_text):
    r = csrs.parse_sum(write_text("r.sum", "header junk\n" + SUM_GLOBAL))
    assert r.datum == "IGc20"
    assert r.epoch == "26:240:83070"
    assert r.lat == pytest.approx(49.943562436, abs=1e-9)    # the ESTIMATED value
    assert r.lon == pytest.approx(-127.224348669, abs=1e-9)
    assert r.ellh == 16.3044


def test_parse_sum_picks_global_block_when_nad83_comes_first(write_text):
    r = csrs.parse_sum(write_text("r.sum", SUM_NAD83 + SUM_GLOBAL))
    assert r.datum == "IGc20"
    assert r.lat == pytest.approx(49.943562436, abs=1e-9)


def test_parse_sum_rejects_nad83_only(write_text):
    with pytest.raises(ValueError, match="NAD83"):
        csrs.parse_sum(write_text("r.sum", SUM_NAD83))


def test_parse_sum_rejects_inconsistent_columns(write_text):
    # estimated height no longer agrees with the report's own diff column
    with pytest.raises(ValueError, match="misaligned"):
        csrs.parse_sum(write_text("r.sum", SUM_GLOBAL.replace("16.3044", "16.9044")))


def test_parse_sum_rejects_incomplete_block(write_text):
    no_hgt = "".join(l for l in SUM_GLOBAL.splitlines(True) if "HGT" not in l)
    with pytest.raises(ValueError):
        csrs.parse_sum(write_text("r.sum", no_hgt))


def test_parse_sum_rejects_non_report(write_text):
    with pytest.raises(ValueError, match="POS LAT/LON/HGT"):
        csrs.parse_sum(write_text("r.sum", "just some text\n"))


def test_parse_sum_rejects_missing_diff_column(write_text):
    no_diff = "".join(" ".join(l.split()[:-2]) + "\n" for l in SUM_GLOBAL.splitlines())
    with pytest.raises(ValueError, match="cross-check"):
        csrs.parse_sum(write_text("r.sum", no_diff))


def test_parse_sum_rejects_two_global_blocks(write_text):
    other_epoch = SUM_GLOBAL.replace("26:240:83070", "26:241:00000")
    with pytest.raises(ValueError, match="ambiguous"):
        csrs.parse_sum(write_text("r.sum", SUM_GLOBAL + other_epoch))


def test_parse_sum_handles_bom(write_text):
    assert csrs.parse_sum(write_text("r.sum", "﻿" + SUM_GLOBAL)).ellh == 16.3044
