"""Tensile test report import."""

from tubing_master.tensile_import import (
    assess_nitinol_cycle,
    build_property_updates,
    build_tensile_comparison_rows,
    import_tensile_test_file,
    parse_tensile_text,
)


def test_parse_typical_metal_report():
    text = """
    Material test report — 316L stainless steel
    Young's modulus: 193 GPa
    0.2% yield strength: 290 MPa
    Ultimate tensile strength: 620 MPa
    Elongation: 45 %
    """
    parsed = parse_tensile_text(text)
    assert parsed["E_mpa"] == 193000.0
    assert parsed["yield_mpa"] == 290.0
    assert parsed["base_uts_mpa"] == 620.0
    assert parsed["elongation_pct"] == 45.0


def test_build_isotropic_updates():
    parsed = parse_tensile_text("Elastic modulus 200 GPa\nYield strength 250 MPa\nUTS 550 MPa")
    updates = build_property_updates(parsed, model="isotropic_hardening")
    assert updates["E_mpa"] == 200000.0
    assert updates["yield_mpa"] == 250.0
    assert updates["base_uts_mpa"] == 550.0
    assert updates["flow_C_mpa"] > 0


def test_nitinol_full_cycle_import():
    text = """
    Superelastic hysteresis loop — Nitinol tube
    Young's modulus 41 GPa
    Loading plateau: martensite start σ_ms 280 MPa, martensite finish σ_mf 420 MPa
    Unloading plateau: austenite finish σ_af 360 MPa, austenite start σ_as 140 MPa
    transformation strain 5.5 %
    UTS 900 MPa
    """
    parsed = parse_tensile_text(text)
    cycle = assess_nitinol_cycle(text, parsed)
    assert cycle.has_full_cycle
    assert not cycle.warning
    updates = build_property_updates(parsed, model="nitinol_superelastic", nitinol_cycle=cycle)
    nit = updates["nitinol"]
    assert nit["sigma_ms_mpa"] == 280.0
    assert nit["sigma_mf_mpa"] == 420.0
    assert nit["sigma_as_mpa"] == 140.0
    assert nit["sigma_af_mpa"] == 360.0


def test_nitinol_monotonic_warns_no_plateau_guess():
    text = "Young's modulus 41 GPa\nProof stress 380 MPa\nTensile strength 900 MPa"
    parsed = parse_tensile_text(text)
    cycle = assess_nitinol_cycle(text, parsed)
    assert not cycle.has_full_cycle
    assert cycle.warning
    updates = build_property_updates(parsed, model="nitinol_superelastic", nitinol_cycle=cycle)
    nit = updates.get("nitinol", {})
    assert "sigma_ms_mpa" not in nit
    assert nit.get("e_austenite_mpa") == 41000.0
    res = import_tensile_test_file("/no/such/tensile.pdf", model="nitinol_superelastic")
    assert not res.ok


def test_import_missing_file():
    res = import_tensile_test_file("/no/such/tensile.pdf", model="isotropic_hardening")
    assert not res.ok


def test_comparison_rows_isotropic():
    parsed = parse_tensile_text(
        "Elastic modulus 200 GPa\nYield strength 250 MPa\nUTS 550 MPa\nElongation 40 %"
    )
    updates = build_property_updates(parsed, model="isotropic_hardening")
    rows = build_tensile_comparison_rows(
        parsed,
        updates,
        model="isotropic_hardening",
        current={"E_mpa": 190000.0, "yield_mpa": 200.0},
    )
    labels = [r.report_label for r in rows]
    assert "Young's modulus E" in labels
    assert any(r.model_label == "Flow stress C" and r.note.startswith("estimated") for r in rows)
    assert any(r.report_label == "Elongation" and r.note.startswith("informational") for r in rows)


def test_comparison_rows_nitinol_plateau_gap():
    text = "Young's modulus 41 GPa\nProof stress 380 MPa\nTensile strength 900 MPa"
    parsed = parse_tensile_text(text)
    cycle = assess_nitinol_cycle(text, parsed)
    updates = build_property_updates(parsed, model="nitinol_superelastic", nitinol_cycle=cycle)
    rows = build_tensile_comparison_rows(
        parsed,
        updates,
        model="nitinol_superelastic",
        nitinol_cycle=cycle,
    )
    plateau_rows = [r for r in rows if "plateau" in r.report_label.lower()]
    assert plateau_rows
    assert all("not applied" in r.note for r in plateau_rows)
