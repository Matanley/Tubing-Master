"""Tube/die geometry mapping."""

from tubing_master.fea_tube_die import tube_die_pass_geometry


def test_reduction_zone_positive():
    g = tube_die_pass_geometry(0.012, 0.010, 0.15, 10.0)
    assert g["reduction_zone_m"] > 0
    assert g["od_out_m"] < 0.012
