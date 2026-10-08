"""Tests for ppk_pipeline.py - config building, the CSRS base override and its
safety checks, and the solver lookup. No RTKLIB run is needed for any of these."""

import pytest

import ppk_pipeline as p

NEAR_BASE = (48.45667, -123.30712, 40.0)          # ground marker used by the tests


@pytest.fixture
def base(make_rinex):
    """Synthetic base RINEX whose header antenna position is ~2 m above NEAR_BASE."""
    return make_rinex(approx=p._llh_to_ecef(NEAR_BASE[0], NEAR_BASE[1], NEAR_BASE[2] + 1.9),
                      delta_h=1.8)


def _conf(path):
    with open(path) as f:
        return f.read()


# --- geodesy -----------------------------------------------------------------------

def test_llh_to_ecef_reference_points():
    assert p._llh_to_ecef(0, 0, 0) == pytest.approx((6378137.0, 0, 0), abs=1e-6)
    x, y, z = p._llh_to_ecef(90, 0, 0)
    assert (x, y) == pytest.approx((0, 0), abs=1e-6)
    assert z == pytest.approx(6356752.3142, abs=1e-3)          # WGS84 polar radius


# --- build_config ---------------------------------------------------------------------

def test_no_override_keeps_validated_behaviour(base, tmp_path):
    antdelu = p.build_config(base, str(tmp_path / "c.conf"))
    text = _conf(tmp_path / "c.conf")
    assert antdelu == pytest.approx(1.8 + p.RS4_APC_L1_M)
    assert "=rinexhead" in text and "ant2-pos1" not in text


def test_override_switches_to_llh_and_keeps_antenna_height(base, tmp_path):
    antdelu = p.build_config(base, str(tmp_path / "c.conf"), NEAR_BASE)
    text = _conf(tmp_path / "c.conf")
    assert "=llh" in text and "=rinexhead" not in text
    for key in ("ant2-pos1", "ant2-pos2", "ant2-pos3"):
        assert key in text
    assert antdelu == pytest.approx(1.8 + p.RS4_APC_L1_M)       # marker in, antdelu unchanged


def test_override_far_from_header_is_refused(base, tmp_path):
    with pytest.raises(SystemExit):
        p.build_config(base, str(tmp_path / "c.conf"), (NEAR_BASE[0], NEAR_BASE[1], 400.0))


@pytest.mark.parametrize("raw", ["0.0000        0.0000        0.0000", "", "garbage"],
                         ids=["zeros", "blank", "garbage"])
def test_unusable_header_position_skips_check_instead_of_aborting(make_rinex, tmp_path, raw):
    rinex = make_rinex(approx_raw=raw)
    assert p._rinex_approx_xyz(rinex) is None
    p.build_config(rinex, str(tmp_path / "c.conf"), NEAR_BASE)    # must not raise


def test_missing_antenna_height_fails(make_rinex, tmp_path):
    with pytest.raises(SystemExit):
        p.build_config(make_rinex(delta_h=None), str(tmp_path / "c.conf"))


def test_template_without_antdelu_line_fails(base, tmp_path, monkeypatch):
    bad = tmp_path / "template.conf"
    bad.write_text("pos1-posmode       =kinematic\nant2-postype       =rinexhead\n")
    monkeypatch.setattr(p, "TEMPLATE", str(bad))
    with pytest.raises(SystemExit):
        p.build_config(base, str(tmp_path / "c.conf"))


# --- stale solver output -----------------------------------------------------------------

def test_stale_solution_files_are_removed(tmp_path):
    for name in ("solution.pos", "solution_events.pos"):
        (tmp_path / name).write_text("old run")
    p._remove_stale_solution(str(tmp_path))
    assert not (tmp_path / "solution.pos").exists()
    assert not (tmp_path / "solution_events.pos").exists()


# --- solver lookup ----------------------------------------------------------------------

def test_find_rnx2rtkp_prefers_explicit_path(tmp_path, monkeypatch):
    exe = tmp_path / "rnx2rtkp.exe"
    exe.write_text("")
    monkeypatch.delenv("RNX2RTKP", raising=False)
    assert p._find_rnx2rtkp(str(exe)) == str(exe)


def test_find_rnx2rtkp_fails_cleanly_when_missing(monkeypatch):
    monkeypatch.delenv("RNX2RTKP", raising=False)
    monkeypatch.setattr(p, "RNX2RTKP_DEFAULT", "Z:/definitely/not/here.exe")
    monkeypatch.setattr(p.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(p.os.path, "isfile", lambda path: False)
    with pytest.raises(SystemExit):
        p._find_rnx2rtkp(None)
