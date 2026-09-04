"""Pass-by-pass BOM rows (dies, mandrel/plug, auxiliary processes) from project + schedule."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from tubing_master.engine import annulus_od_id_trajectory, drawing_pass_dicts

_PROCESS_KIND_LABELS: Dict[str, str] = {
    "reducer": "Reducer",
    "drawing": "Drawing",
    "annealing": "Annealing",
    "straightening": "Straightening",
    "sandblasting": "Sandblasting",
}
_AUX_KINDS = frozenset({"reducer", "annealing", "straightening", "sandblasting"})


def _normalize_kind(raw: Any, *, area_reduction: float = 0.0) -> str:
    s = str(raw or "").strip().lower().replace(" ", "_")
    aliases = {
        "draw": "drawing",
        "drawing_pass": "drawing",
        "ht": "annealing",
        "heat_treatment": "annealing",
        "anneal": "annealing",
        "straightend": "straightening",
        "straightending": "straightening",
        "straighten": "straightening",
        "blast": "sandblasting",
        "sand_blast": "sandblasting",
        "sandblast": "sandblasting",
        "reduce": "reducer",
        "incoming": "reducer",
        "incoming_tube": "reducer",
    }
    s = aliases.get(s, s)
    if s in _PROCESS_KIND_LABELS:
        return s
    if float(area_reduction or 0.0) > 0.0:
        return "drawing"
    return "drawing"


def _kind_label(kind: str) -> str:
    k = _normalize_kind(kind)
    return _PROCESS_KIND_LABELS.get(k, k.title())


def _is_aux(kind: str) -> bool:
    return _normalize_kind(kind) in _AUX_KINDS


def merge_bom_detail_notes(
    saved_rows: List[Dict[str, Any]], fresh_rows: List[Dict[str, Any]]
) -> None:
    """
    Copy Notes from a previous detail_rows list onto freshly computed rows when
    (pass index, row_kind, kind) matches — survives pass-count changes better than
    row-index merge. Mutates fresh_rows in place.
    """

    def _key(r: Dict[str, Any]) -> tuple:
        return (int(r.get("pass", 0)), str(r.get("row_kind", "")), str(r.get("kind", "")))

    notes_map = {_key(r): str(r.get("notes", "")) for r in saved_rows}
    for row in fresh_rows:
        k = _key(row)
        if k in notes_map:
            row["notes"] = notes_map[k]


def _is_sink_drawing(method: str) -> bool:
    m = (method or "").lower()
    return "sink" in m or "rodless" in m


def _process_notes(p: Dict[str, Any], *, kind: str) -> str:
    parts: List[str] = []
    temp = str(p.get("temperature", "") or "").strip()
    time_s = str(p.get("time", "") or "").strip()
    gas = str(p.get("protective_gas", "") or "").strip()
    if kind in ("annealing", "drawing") or temp or time_s or gas:
        if temp or time_s or gas:
            ht = []
            if temp:
                ht.append(f"T={temp} °C")
            if time_s:
                ht.append(f"{time_s} min")
            if gas:
                ht.append(gas)
            parts.append(", ".join(ht))
    note = str(p.get("notes", "") or "").strip()
    if note:
        parts.append(note)
    return "; ".join(parts)


def _schedule_process_entries(project: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Ordered (role, process_dict) for Incoming + every pass_schedule.passes entry."""
    sch = project.get("pass_schedule") or {}
    out: List[Tuple[str, Dict[str, Any]]] = []
    incoming = sch.get("incoming_process")
    if isinstance(incoming, dict):
        inc = dict(incoming)
        kind = _normalize_kind(inc.get("kind") or "reducer", area_reduction=0.0)
        if kind == "drawing":
            kind = "reducer"
        inc["kind"] = kind
        inc["r"] = 0.0
        out.append(("incoming", inc))
    else:
        out.append(("incoming", {"kind": "reducer", "r": 0.0}))
    for p in list(sch.get("passes") or []):
        if isinstance(p, dict):
            out.append(("pass", dict(p)))
    return out


def compute_pass_die_rows(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    BOM detail rows for the full process schedule:

    - Incoming (row_kind=incoming)
    - Each drawing pass: Die (+ Mandrel/plug when not sink)
    - Each auxiliary process: one process row (annealing, straightener, etc.)

    Die/tool rows keep integer ``pass`` = drawing index (1-based) for inventory match
    and quotation. Auxiliary / Incoming use ``pass`` = 0 and ``schedule_index`` for order.
    """
    in_od = float(project.get("in_od_mm", 12.0))
    in_id = float(project.get("in_id_mm", 10.0))
    method = str(project.get("drawing_method", ""))
    sink = _is_sink_drawing(method)

    entries = _schedule_process_entries(project)
    draw_dicts = drawing_pass_dicts([p for role, p in entries if role == "pass"])
    traj: List[Tuple[float, float]] = [(in_od, in_id)]
    if draw_dicts:
        traj = annulus_od_id_trajectory(in_od, in_id, draw_dicts)

    rows: List[Dict[str, Any]] = []
    draw_i = 0
    cur_od, cur_id = traj[0]
    schedule_index = 0

    for role, p in entries:
        schedule_index += 1
        try:
            r_raw = float(p.get("r", 0.0) or 0.0)
        except (TypeError, ValueError):
            r_raw = 0.0
        kind = _normalize_kind(p.get("kind"), area_reduction=r_raw)
        equipment = str(p.get("equipment", "") or "").strip()
        notes = _process_notes(p, kind=kind)

        if role == "incoming":
            if kind == "drawing":
                kind = "reducer"
            rows.append(
                {
                    "pass": 0,
                    "schedule_index": schedule_index,
                    "step": "Incoming",
                    "item": _kind_label(kind),
                    "kind": kind,
                    "equipment": equipment,
                    "alpha_deg": None,
                    "r": None,
                    "od_before": cur_od,
                    "id_before": cur_id,
                    "od_after": cur_od,
                    "id_after": cur_id,
                    "notes": notes,
                    "row_kind": "incoming",
                }
            )
            continue

        is_draw = kind == "drawing" and r_raw > 0.0
        if is_draw and draw_i + 1 < len(traj):
            od_b, id_b = traj[draw_i]
            od_a, id_a = traj[draw_i + 1]
            pass_no = draw_i + 1
            draw_i += 1
            cur_od, cur_id = od_a, id_a
            try:
                alpha = float(p.get("alpha_deg", 12.0))
            except (TypeError, ValueError):
                alpha = 12.0
            rows.append(
                {
                    "pass": pass_no,
                    "schedule_index": schedule_index,
                    "step": f"Pass {pass_no}",
                    "item": "Die",
                    "kind": "drawing",
                    "equipment": equipment,
                    "alpha_deg": alpha,
                    "r": r_raw,
                    "od_before": od_b,
                    "id_before": id_b,
                    "od_after": od_a,
                    "id_after": id_a,
                    "notes": notes,
                    "row_kind": "die",
                }
            )
            if not sink:
                rows.append(
                    {
                        "pass": pass_no,
                        "schedule_index": schedule_index,
                        "step": f"Pass {pass_no}",
                        "item": "Mandrel / plug",
                        "kind": "drawing",
                        "equipment": equipment,
                        "alpha_deg": None,
                        "r": None,
                        "od_before": id_a,
                        "id_before": None,
                        "od_after": id_a,
                        "id_after": None,
                        "notes": "Internal tool OD ≈ tube ID after pass (nominal).",
                        "row_kind": "tool",
                    }
                )
            continue

        if not _is_aux(kind):
            kind = _normalize_kind(kind, area_reduction=0.0)
            if kind == "drawing":
                kind = "annealing"
        label = _kind_label(kind)
        rows.append(
            {
                "pass": 0,
                "schedule_index": schedule_index,
                "step": label,
                "item": label,
                "kind": kind,
                "equipment": equipment,
                "alpha_deg": None,
                "r": None,
                "od_before": cur_od,
                "id_before": cur_id,
                "od_after": cur_od,
                "id_after": cur_id,
                "notes": notes,
                "row_kind": "process",
            }
        )

    return rows


def merge_detail_into_pass_bom_payload(
    project: Dict[str, Any],
    detail_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Attach detailed rows for JSON bundle; keep quotation `lines` from summarize."""
    from tubing_master.quotation import build_pass_bom

    base = build_pass_bom(project)
    base["version"] = 2
    base["detail_rows"] = detail_rows
    return base
