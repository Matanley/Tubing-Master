"""Die inventory matching against analytical pass schedule (α + entry annulus OD)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from tubing_master.bom_detail import compute_pass_die_rows

DieMatchStatus = Literal["available", "unavailable", "none", "empty_inventory", "no_geometry"]

# Semi-die angle match tolerance (degrees): nearest inventory die must be within this of schedule α.
DEFAULT_ANGLE_TOLERANCE_DEG = 0.5
# Default bearing (land) length for new rows / legacy records (mm) — used in die schematic only; not in schedule match.
DEFAULT_BEARING_LENGTH_MM = 1.2

_EPS_OD_MM = 1e-6


def normalize_die_records(raw: Any) -> List[Dict[str, Any]]:
    """Parse loose JSON/list structures into die dicts with stable keys."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        did = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip() or "Die"
        try:
            alpha_deg = float(entry.get("alpha_deg", 0.0))
        except (TypeError, ValueError):
            alpha_deg = 0.0
        try:
            od_min_mm = float(entry.get("od_min_mm", 0.0))
        except (TypeError, ValueError):
            od_min_mm = 0.0
        try:
            od_max_mm = float(entry.get("od_max_mm", 0.0))
        except (TypeError, ValueError):
            od_max_mm = 0.0
        if od_max_mm < od_min_mm:
            od_min_mm, od_max_mm = od_max_mm, od_min_mm
        in_stock = bool(entry.get("in_stock", True))
        try:
            quantity = int(entry.get("quantity", 0))
        except (TypeError, ValueError):
            quantity = 0
        if quantity < 0:
            quantity = 0
        material = str(entry.get("material") or "").strip()
        supplier = str(entry.get("supplier") or "").strip()
        notes = str(entry.get("notes") or "")
        try:
            bearing_length_mm = float(
                entry.get("bearing_length_mm", DEFAULT_BEARING_LENGTH_MM)
            )
        except (TypeError, ValueError):
            bearing_length_mm = float(DEFAULT_BEARING_LENGTH_MM)
        if bearing_length_mm < 0:
            bearing_length_mm = 0.0
        out.append(
            {
                "id": did,
                "name": name,
                "alpha_deg": alpha_deg,
                "od_min_mm": od_min_mm,
                "od_max_mm": od_max_mm,
                "bearing_length_mm": bearing_length_mm,
                "material": material,
                "supplier": supplier,
                "in_stock": in_stock,
                "quantity": quantity,
                "notes": notes,
            }
        )
    return out


def _die_row_per_pass(project: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    rows = compute_pass_die_rows(project)
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        if str(r.get("row_kind") or "") != "die":
            continue
        try:
            p = int(r.get("pass", 0))
        except (TypeError, ValueError):
            continue
        if p > 0:
            out[p] = r
    return out


def match_die_for_pass(
    alpha_deg: float,
    entry_od_mm: float,
    inventory: List[Dict[str, Any]],
    *,
    angle_tolerance_deg: float = DEFAULT_ANGLE_TOLERANCE_DEG,
) -> Tuple[Optional[Dict[str, Any]], DieMatchStatus]:
    """
    Pick the inventory die that minimizes |Δα| among dies whose OD band contains ``entry_od_mm``.
    Returns ``(matched_die_or_none, status)``.
    """
    inv = normalize_die_records(inventory)
    if not inv:
        return None, "empty_inventory"

    candidates: List[Dict[str, Any]] = []
    for d in inv:
        lo = float(d.get("od_min_mm", 0.0))
        hi = float(d.get("od_max_mm", 0.0))
        if lo > hi:
            lo, hi = hi, lo
        if lo - _EPS_OD_MM <= entry_od_mm <= hi + _EPS_OD_MM:
            candidates.append(d)

    if not candidates:
        return None, "none"

    best = min(candidates, key=lambda x: abs(float(x.get("alpha_deg", 0.0)) - alpha_deg))
    best_alpha = float(best.get("alpha_deg", 0.0))
    if abs(best_alpha - alpha_deg) > float(angle_tolerance_deg):
        return None, "none"

    stock = bool(best.get("in_stock", True))
    return best, ("available" if stock else "unavailable")


def _od_contains(lo: float, hi: float, entry_od: float) -> bool:
    if lo > hi:
        lo, hi = hi, lo
    return lo - _EPS_OD_MM <= entry_od <= hi + _EPS_OD_MM


def pick_inventory_alpha_deg(
    entry_od_mm: float,
    inventory: List[Dict[str, Any]],
    *,
    current_alpha_deg: float = 12.0,
    prefer_in_stock: bool = True,
) -> Optional[float]:
    """
    Pick an inventory semi-die angle for ``entry_od_mm``.

    When ``prefer_in_stock`` is true, in-stock dies in the OD band are tried first; otherwise all
    band matches are considered. Among the pool, the die with α closest to ``current_alpha_deg`` wins.
    """
    inv = normalize_die_records(inventory)
    if not inv:
        return None
    candidates = [
        d
        for d in inv
        if _od_contains(float(d.get("od_min_mm", 0.0)), float(d.get("od_max_mm", 0.0)), entry_od_mm)
    ]
    if not candidates:
        return None
    if prefer_in_stock:
        stocked = [d for d in candidates if bool(d.get("in_stock", True))]
        pool = stocked if stocked else candidates
    else:
        pool = candidates
    best = min(pool, key=lambda x: abs(float(x.get("alpha_deg", 0.0)) - current_alpha_deg))
    return float(best.get("alpha_deg", 0.0))


def snap_alpha_deg_from_inventory(
    alpha_deg: float,
    entry_od_mm: float,
    inventory: List[Dict[str, Any]],
) -> Optional[float]:
    """Among dies whose OD band contains ``entry_od_mm``, return α of the nearest-angle die."""
    return pick_inventory_alpha_deg(
        entry_od_mm,
        inventory,
        current_alpha_deg=alpha_deg,
        prefer_in_stock=False,
    )


def inventory_alpha_updates_before_last_pass(
    project: Dict[str, Any],
    inventory: List[Dict[str, Any]],
    *,
    n_draw_passes: int,
    prefer_in_stock: bool = True,
) -> List[Tuple[int, float]]:
    """
    Suggested semi-die angles from inventory for passes ``1 .. n_draw_passes - 1`` (last pass unchanged).

    Returns ``(pass_number, alpha_deg)`` pairs using each pass's entry annulus OD from the analytical chain.
    """
    if n_draw_passes <= 1:
        return []
    try:
        die_by_pass = _die_row_per_pass(project)
    except Exception:
        return []
    out: List[Tuple[int, float]] = []
    for pass_no in range(1, n_draw_passes):
        dr = die_by_pass.get(pass_no)
        if dr is None:
            continue
        current = float(dr.get("alpha_deg", 12.0))
        entry_od = float(dr.get("od_before", 0.0))
        na = pick_inventory_alpha_deg(
            entry_od,
            inventory,
            current_alpha_deg=current,
            prefer_in_stock=prefer_in_stock,
        )
        if na is not None and abs(na - current) > 1e-9:
            out.append((pass_no, na))
    return out


def draw_pass_match_details(
    project: Dict[str, Any],
    inventory: List[Dict[str, Any]],
    *,
    n_draw_passes: int,
    angle_tolerance_deg: float = DEFAULT_ANGLE_TOLERANCE_DEG,
) -> List[Tuple[DieMatchStatus, str]]:
    """
    One ``(status, tooltip)`` per drawing pass (passes ``1 .. n_draw_passes`` in table order).
    """
    if n_draw_passes <= 0:
        return []

    try:
        die_by_pass = _die_row_per_pass(project)
    except Exception:
        return [("no_geometry", "Could not compute annulus trajectory for this project.")] * n_draw_passes

    if len(die_by_pass) == 0:
        return [("no_geometry", "No die geometry for this pass schedule.")] * n_draw_passes

    inv_norm = normalize_die_records(inventory)
    empty_inv = len(inv_norm) == 0
    tol = float(angle_tolerance_deg)

    out: List[Tuple[DieMatchStatus, str]] = []
    for pass_no in range(1, n_draw_passes + 1):
        die_row = die_by_pass.get(pass_no)
        if die_row is None:
            out.append(("no_geometry", f"Pass {pass_no}: missing die row in analytical BOM."))
            continue
        alpha_deg = float(die_row.get("alpha_deg", 0.0))
        entry_od = float(die_row.get("od_before", 0.0))
        if empty_inv:
            out.append(
                (
                    "empty_inventory",
                    f"Pass {pass_no}: schedule α={alpha_deg:.3f}°, entry OD ≈ {entry_od:.4f} mm — add dies to inventory.",
                )
            )
            continue
        die, st = match_die_for_pass(
            alpha_deg, entry_od, inv_norm, angle_tolerance_deg=angle_tolerance_deg
        )
        if st == "none":
            # Refine message: OD out of band vs angle
            odband = [d for d in inv_norm if _od_contains(float(d.get("od_min_mm", 0.0)), float(d.get("od_max_mm", 0.0)), entry_od)]
            if not odband:
                tip = (
                    f"Pass {pass_no}: entry OD ≈ {entry_od:.4f} mm is outside all inventory OD bands "
                    f"(α={alpha_deg:.3f}°)."
                )
            else:
                nearest = min(odband, key=lambda x: abs(float(x.get("alpha_deg", 0.0)) - alpha_deg))
                na = float(nearest.get("alpha_deg", 0.0))
                tip = (
                    f"Pass {pass_no}: no die within ±{tol:.3f}° (schedule α={alpha_deg:.3f}°; nearest in OD band is "
                    f"“{nearest.get('name', 'Die')}” at α={na:.3f}°)."
                )
            out.append((st, tip))
            continue
        if st == "available" and die is not None:
            tip = (
                f"Pass {pass_no}: matched “{die.get('name', 'Die')}” (α={float(die.get('alpha_deg', 0.0)):.3f}°, "
                f"OD {float(die.get('od_min_mm', 0.0)):.4f}–{float(die.get('od_max_mm', 0.0)):.4f} mm) — in stock."
            )
            out.append((st, tip))
            continue
        if st == "unavailable" and die is not None:
            tip = (
                f"Pass {pass_no}: matched “{die.get('name', 'Die')}” (α={float(die.get('alpha_deg', 0.0)):.3f}°, "
                f"OD {float(die.get('od_min_mm', 0.0)):.4f}–{float(die.get('od_max_mm', 0.0)):.4f} mm) — not in stock."
            )
            out.append((st, tip))
            continue
        out.append((st, f"Pass {pass_no}: unexpected match state."))
    return out
