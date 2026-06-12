"""Pass-by-pass BOM rows (dies, mandrel/plug) from project + analytical schedule."""

from __future__ import annotations

from typing import Any, Dict, List

from tubing_master.engine import annulus_od_id_trajectory, drawing_pass_dicts


def merge_bom_detail_notes(
    saved_rows: List[Dict[str, Any]], fresh_rows: List[Dict[str, Any]]
) -> None:
    """
    Copy Notes from a previous detail_rows list onto freshly computed rows when
    (pass index, row_kind) matches — survives pass-count changes better than row-index merge.
    Mutates fresh_rows in place.
    """
    def _key(r: Dict[str, Any]) -> tuple:
        return (int(r.get("pass", 0)), str(r.get("row_kind", "")))

    notes_map = {_key(r): str(r.get("notes", "")) for r in saved_rows}
    for row in fresh_rows:
        k = _key(row)
        if k in notes_map:
            row["notes"] = notes_map[k]


def _is_sink_drawing(method: str) -> bool:
    m = (method or "").lower()
    return "sink" in m or "rodless" in m


def compute_pass_die_rows(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    One primary row per pass: Die with α, r, annulus OD/ID before→after.

    For non-sink drawing modes, inserts a second row (Mandrel / plug) with
    internal tool OD nominally equal to tube ID after the pass (verify with engineering).
    """
    in_od = float(project.get("in_od_mm", 12.0))
    in_id = float(project.get("in_id_mm", 10.0))
    method = str(project.get("drawing_method", ""))
    sink = _is_sink_drawing(method)

    sch = project.get("pass_schedule") or {}
    passes = drawing_pass_dicts(list(sch.get("passes") or []))
    if not passes:
        return []

    traj = annulus_od_id_trajectory(in_od, in_id, passes)
    rows: List[Dict[str, Any]] = []

    for i, p in enumerate(passes):
        od_b, id_b = traj[i]
        od_a, id_a = traj[i + 1]
        rows.append(
            {
                "pass": i + 1,
                "item": "Die",
                "alpha_deg": float(p.get("alpha_deg", 12.0)),
                "r": float(p.get("r", 0.0)),
                "od_before": od_b,
                "id_before": id_b,
                "od_after": od_a,
                "id_after": id_a,
                "notes": "",
                "row_kind": "die",
            }
        )
        if not sink:
            rows.append(
                {
                    "pass": i + 1,
                    "item": "Mandrel / plug",
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
