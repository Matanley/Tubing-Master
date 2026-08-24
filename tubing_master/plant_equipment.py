"""Plant equipment catalog — limited shop list, persisted per user/machine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from tubing_master.app_paths import user_data_root

_PLANT_EQUIPMENT_FILENAME = "plant_equipment.json"


def plant_equipment_path() -> Path:
    return user_data_root() / _PLANT_EQUIPMENT_FILENAME


def _kind_order() -> tuple[str, ...]:
    from tubing_master.ui.add_process_dialog import PROCESS_KIND_ORDER

    return PROCESS_KIND_ORDER


def _presets() -> Dict[str, List[str]]:
    from tubing_master.ui.add_process_dialog import PROCESS_EQUIPMENT_PRESETS

    return PROCESS_EQUIPMENT_PRESETS


def _empty_catalog() -> Dict[str, List[str]]:
    return {kind: [] for kind in _kind_order()}


def load_plant_equipment() -> Dict[str, List[str]]:
    """Load plant-saved equipment names keyed by process kind."""
    path = plant_equipment_path()
    out = _empty_catalog()
    if not path.is_file():
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return out
    if not isinstance(raw, dict):
        return out
    for kind in _kind_order():
        items = raw.get(kind)
        if not isinstance(items, list):
            continue
        seen: set[str] = set()
        cleaned: List[str] = []
        for item in items:
            name = str(item or "").strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            cleaned.append(name)
        out[kind] = cleaned
    return out


def save_plant_equipment(catalog: Dict[str, List[str]]) -> None:
    path = plant_equipment_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _empty_catalog()
    for kind in _kind_order():
        items = catalog.get(kind) if isinstance(catalog, dict) else None
        if not isinstance(items, list):
            continue
        seen: set[str] = set()
        cleaned: List[str] = []
        for item in items:
            name = str(item or "").strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            cleaned.append(name)
        payload[kind] = cleaned
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def equipment_choices_for_kind(kind: str) -> List[str]:
    """Built-in presets plus plant-saved names (unique, presets first)."""
    key = str(kind or "").strip().lower()
    presets = list(_presets().get(key, []))
    plant = list(load_plant_equipment().get(key, []))
    seen: set[str] = set()
    out: List[str] = []
    for name in presets + plant:
        n = str(name or "").strip()
        k = n.lower()
        if not n or k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def equipment_known_for_kind(kind: str, name: str) -> bool:
    needle = str(name or "").strip().lower()
    if not needle:
        return True
    return any(c.lower() == needle for c in equipment_choices_for_kind(kind))


def add_plant_equipment(kind: str, name: str) -> bool:
    """Append equipment to the plant list for ``kind``. Returns True if newly added."""
    key = str(kind or "").strip().lower()
    if key not in _kind_order():
        return False
    label = str(name or "").strip()
    if not label:
        return False
    catalog = load_plant_equipment()
    existing = catalog.get(key, [])
    if any(e.lower() == label.lower() for e in existing):
        return False
    presets = _presets().get(key, [])
    if any(e.lower() == label.lower() for e in presets):
        return False
    existing.append(label)
    catalog[key] = existing
    save_plant_equipment(catalog)
    return True
