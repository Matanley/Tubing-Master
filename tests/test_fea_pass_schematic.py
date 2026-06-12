"""FEA pass schematic helpers."""

from tubing_master.fea_pass_schematic import (
    FeaPassSchematicSpec,
    build_fea_pass_schematic_layout,
    tooling_kind_from_drawing_method,
    tube_wall_band_polygons,
)


def test_tooling_kind_mapping():
    assert tooling_kind_from_drawing_method("Sink drawing (rodless drawing)") == "sink"
    assert tooling_kind_from_drawing_method("Long mandrel drawing") == "mandrel"
    assert tooling_kind_from_drawing_method("Fixed plug drawing") == "plug"
    assert tooling_kind_from_drawing_method("Floating plug drawing") == "floating_plug"


def test_build_layout_reduces_od():
    spec = FeaPassSchematicSpec(
        od_in_mm=12.0,
        id_in_mm=10.0,
        area_reduction_fraction=0.15,
        semi_die_angle_deg=12.0,
    )
    lay = build_fea_pass_schematic_layout(spec)
    assert lay is not None
    assert lay.od_out_mm < lay.od_in_mm
    assert lay.id_out_mm == lay.id_in_mm
    upper, lower = tube_wall_band_polygons(lay, z0=lay.z_stock_start, z1=lay.die.z3, n_seg=8)
    assert len(upper) >= 4
    assert len(lower) >= 4
