"""Plant equipment list persistence."""

from __future__ import annotations

from tubing_master.plant_equipment import (
    add_plant_equipment,
    equipment_choices_for_kind,
    equipment_known_for_kind,
    load_plant_equipment,
    save_plant_equipment,
)


def test_add_plant_equipment_and_choices(tmp_path, monkeypatch):
    path = tmp_path / "plant_equipment.json"
    monkeypatch.setattr("tubing_master.plant_equipment.plant_equipment_path", lambda: path)

    assert not equipment_known_for_kind("drawing", "Shop Drawbench #3")
    assert add_plant_equipment("drawing", "Shop Drawbench #3") is True
    assert equipment_known_for_kind("drawing", "Shop Drawbench #3")
    assert add_plant_equipment("drawing", "Shop Drawbench #3") is False  # duplicate
    choices = equipment_choices_for_kind("drawing")
    assert "Shop Drawbench #3" in choices
    assert "Drawbench" in choices  # built-in still present

    catalog = load_plant_equipment()
    assert "Shop Drawbench #3" in catalog["drawing"]
    save_plant_equipment(catalog)
    assert path.is_file()
