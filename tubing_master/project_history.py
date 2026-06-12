"""Project bundles, global history index, and Save-to-History upserts."""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tubing_master.app_paths import projects_dir as app_projects_dir
from tubing_master.app_paths import suggested_projects_dir as app_suggested_projects_dir


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_projects_dir() -> Path:
    """User Projects folder (app data when installed, else Documents/Tubing Master/Projects)."""
    return app_projects_dir()


def history_root_dir() -> Path:
    """Legacy ~/.tubing_master root (retained for older tooling)."""
    return Path.home() / ".tubing_master"


def history_index_path() -> Path:
    """Legacy index path under ``~/.tubing_master`` (not used by the desktop UI)."""
    return history_root_dir() / "project_history.json"


def workdir_projects_dir() -> Path:
    """Projects folder (repo ``./Projects`` in dev, app data when frozen)."""
    return app_projects_dir()


def ensure_workdir_projects_folder() -> Path:
    """Create the projects folder the first time history is written."""
    d = workdir_projects_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_projects_dir() -> Path:
    d = default_projects_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_history_dir() -> Path:
    d = history_root_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class TubingProjectRecord:
    """Metadata + optional bundle slices for Fetch History."""

    id: str
    title: str
    created: str
    updated: str
    project: Dict[str, Any] = field(default_factory=dict)
    pass_schedule: Optional[Dict[str, Any]] = None
    pass_bom: Optional[Dict[str, Any]] = None
    quotation: Optional[Dict[str, Any]] = None

    def to_json_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @property
    def saved_at(self) -> str:
        return self.updated or self.created

    @property
    def in_od_mm(self) -> float:
        p = self.project or {}
        v = p.get("in_od_mm", p.get("incoming_od_mm", 0.0))
        return float(v)

    @property
    def in_id_mm(self) -> float:
        p = self.project or {}
        v = p.get("in_id_mm", p.get("incoming_id_mm", 0.0))
        return float(v)

    @property
    def out_od_mm(self) -> float:
        p = self.project or {}
        v = p.get("out_od_mm", p.get("target_od_mm", 0.0))
        return float(v)

    @property
    def out_id_mm(self) -> float:
        p = self.project or {}
        v = p.get("out_id_mm", p.get("target_id_mm", 0.0))
        return float(v)

    @property
    def material(self) -> str:
        return str((self.project or {}).get("material", ""))

    @property
    def drawing_method(self) -> str:
        return str((self.project or {}).get("drawing_method", ""))


def default_history_path() -> Path:
    """History index JSON under the active projects directory."""
    return workdir_projects_dir() / "project_history.json"


def suggested_projects_root() -> Path:
    """Directory for judgment / Fetch History JSON copies."""
    return app_suggested_projects_dir()


def now_iso_utc() -> str:
    """UTC ISO timestamp for record ``created`` / ``updated`` fields."""
    return _utc_now_iso()


def _row_to_record(row: Dict[str, Any]) -> Optional[TubingProjectRecord]:
    try:
        return TubingProjectRecord(
            id=str(row["id"]),
            title=str(row.get("title", "")),
            created=str(row.get("created", "")),
            updated=str(row.get("updated", "")),
            project=dict(row.get("project") or {}),
            pass_schedule=row.get("pass_schedule"),
            pass_bom=row.get("pass_bom"),
            quotation=row.get("quotation"),
        )
    except Exception:
        return None


def load_projects(path: Path | str) -> List[TubingProjectRecord]:
    """Load history entries from a ``project_history.json``-style index file."""
    path = Path(path)
    if not path.is_file():
        return []
    with open(path, "r", encoding="utf-8") as f:
        idx = json.load(f)
    out: List[TubingProjectRecord] = []
    for row in idx.get("projects") or []:
        rec = _row_to_record(row)
        if rec is not None:
            out.append(rec)
    return out


def make_record_from_ui(
    in_od_mm: float,
    in_id_mm: float,
    out_od_mm: float,
    out_id_mm: float,
    material: str,
    drawing_method: str,
) -> TubingProjectRecord:
    """Build a new history row from Tubing Project spin boxes / combos."""
    now = _utc_now_iso()
    rid = str(uuid.uuid4())
    proj: Dict[str, Any] = {
        "in_od_mm": float(in_od_mm),
        "in_id_mm": float(in_id_mm),
        "out_od_mm": float(out_od_mm),
        "out_id_mm": float(out_id_mm),
        "material": str(material),
        "drawing_method": str(drawing_method),
    }
    title = f"{material} {in_od_mm:.3f}/{in_id_mm:.3f} → {out_od_mm:.3f}/{out_id_mm:.3f}"
    return TubingProjectRecord(
        id=rid,
        title=title,
        created=now,
        updated=now,
        project=proj,
    )


def _baseline_close_enough(a: TubingProjectRecord, b: TubingProjectRecord) -> bool:
    tol = 1e-4
    if abs(a.in_od_mm - b.in_od_mm) > tol:
        return False
    if abs(a.in_id_mm - b.in_id_mm) > tol:
        return False
    if abs(a.out_od_mm - b.out_od_mm) > tol:
        return False
    if abs(a.out_id_mm - b.out_id_mm) > tol:
        return False
    if a.material.strip().lower() != b.material.strip().lower():
        return False
    if a.drawing_method.strip().lower() != b.drawing_method.strip().lower():
        return False
    return True


def find_exact(
    cur: TubingProjectRecord, projects: List[TubingProjectRecord]
) -> Optional[TubingProjectRecord]:
    """First history row matching incoming/target OD/ID, material, and drawing method."""
    for p in projects:
        if _baseline_close_enough(cur, p):
            return p
    return None


def _od_id_distance_mm(a: TubingProjectRecord, b: TubingProjectRecord) -> float:
    return float(
        math.sqrt(
            (a.in_od_mm - b.in_od_mm) ** 2
            + (a.in_id_mm - b.in_id_mm) ** 2
            + (a.out_od_mm - b.out_od_mm) ** 2
            + (a.out_id_mm - b.out_id_mm) ** 2
        )
    )


def find_closest(
    cur: TubingProjectRecord, projects: List[TubingProjectRecord], k: int = 3
) -> List[Tuple[float, TubingProjectRecord]]:
    """Nearest neighbors by Euclidean distance on the four OD/ID values (mm)."""
    scored: List[Tuple[float, TubingProjectRecord]] = []
    for p in projects:
        if _baseline_close_enough(cur, p):
            continue
        scored.append((_od_id_distance_mm(cur, p), p))
    scored.sort(key=lambda x: x[0])
    return scored[: max(0, k)]


def append_project(
    cur: TubingProjectRecord, path: Path | str, dedupe: bool = True
) -> None:
    """Persist ``cur`` via :func:`upsert_project` (global index). Optional semantic dedupe."""
    if dedupe:
        existing = load_projects(path)
        if find_exact(cur, existing):
            return
    upsert_project(cur)


def format_process_document_export(rec: TubingProjectRecord, history_file: Path | str) -> str:
    """Plain-text export for Fetch History."""
    lines = [
        "Tubing Master — process document (from project history)",
        "",
        f"Title: {rec.title}",
        f"Record id: {rec.id}",
        f"Saved: {rec.saved_at}",
        f"History file: {history_file}",
        "",
        "Geometry baseline (mm)",
        f"  Incoming OD / ID: {rec.in_od_mm:.4f} / {rec.in_id_mm:.4f}",
        f"  Target OD / ID:    {rec.out_od_mm:.4f} / {rec.out_id_mm:.4f}",
        "",
        f"Material: {rec.material}",
        f"Drawing method: {rec.drawing_method}",
        "",
    ]
    if rec.pass_schedule:
        lines.append("Pass schedule (snapshot):")
        lines.append(json.dumps(rec.pass_schedule, indent=2))
        lines.append("")
    if rec.pass_bom:
        lines.append("Pass BOM (snapshot):")
        lines.append(json.dumps(rec.pass_bom, indent=2))
        lines.append("")
    if rec.quotation:
        lines.append("Quotation (snapshot):")
        lines.append(json.dumps(rec.quotation, indent=2))
        lines.append("")
    lines.append("— End —")
    return "\n".join(lines)


def load_history_index() -> Dict[str, Any]:
    path = default_history_path()
    if not path.is_file():
        return {"version": 1, "projects": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history_index(index: Dict[str, Any]) -> None:
    ensure_workdir_projects_folder()
    path = default_history_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    tmp.replace(path)


def list_records() -> List[TubingProjectRecord]:
    return load_projects(default_history_path())


def _record_payload(rec: TubingProjectRecord) -> Dict[str, Any]:
    return {
        "id": rec.id,
        "title": rec.title,
        "created": rec.created,
        "updated": rec.updated,
        "project": rec.project,
        "pass_schedule": rec.pass_schedule,
        "pass_bom": rec.pass_bom,
        "quotation": rec.quotation,
    }


def upsert_project(record: TubingProjectRecord) -> None:
    """Insert or replace a history entry by id (Save to History)."""
    idx = load_history_index()
    projects: List[Dict[str, Any]] = list(idx.get("projects") or [])
    payload = _record_payload(record)
    replaced = False
    for i, p in enumerate(projects):
        if str(p.get("id")) == record.id:
            projects[i] = payload
            replaced = True
            break
    if not replaced:
        projects.append(payload)
    idx["projects"] = projects
    idx["version"] = 1
    save_history_index(idx)

    snap = default_history_path().parent / "snapshots" / f"{record.id}.json"
    snap.parent.mkdir(parents=True, exist_ok=True)
    with open(snap, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def save_project_bundle(path: Path, bundle: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)
    tmp.replace(path)


def load_project_bundle(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def make_bundle_from_parts(
    *,
    project: Dict[str, Any],
    pass_schedule: Optional[Dict[str, Any]],
    pass_bom: Optional[Dict[str, Any]],
    quotation: Optional[Dict[str, Any]],
    record_meta: Optional[TubingProjectRecord] = None,
) -> Dict[str, Any]:
    """Single-file bundle for Save As New / Open Project."""
    rec = record_meta
    if rec is None:
        rid = str(uuid.uuid4())
        now = _utc_now_iso()
        rec = TubingProjectRecord(
            id=rid,
            title=project.get("note") or "Tubing Master project",
            created=now,
            updated=now,
            project=project,
            pass_schedule=pass_schedule,
            pass_bom=pass_bom,
            quotation=quotation,
        )
    return {
        "format_version": 1,
        "kind": "tubing_master_project_bundle",
        "record": _record_payload(rec),
        "project": project,
        "pass_schedule": pass_schedule,
        "pass_bom": pass_bom,
        "quotation": quotation,
    }


def split_bundle(data: Dict[str, Any]) -> Tuple[Dict[str, Any], TubingProjectRecord]:
    """Accept bundle or legacy flat project json (test-run style)."""
    if data.get("kind") == "tubing_master_project_bundle":
        rec_raw = data.get("record") or {}
        rec = TubingProjectRecord(
            id=str(rec_raw.get("id", uuid.uuid4())),
            title=str(rec_raw.get("title", "Project")),
            created=str(rec_raw.get("created", _utc_now_iso())),
            updated=str(rec_raw.get("updated", _utc_now_iso())),
            project=dict(data.get("project") or {}),
            pass_schedule=data.get("pass_schedule"),
            pass_bom=data.get("pass_bom"),
            quotation=data.get("quotation"),
        )
        proj = dict(data.get("project") or {})
        if "pass_schedule" in proj and rec.pass_schedule is None:
            rec.pass_schedule = proj.get("pass_schedule")
        return proj, rec

    if data.get("kind") == "tubing_master_project" and "project" in data:
        proj = dict(data["project"])
        rid = str(uuid.uuid4())
        now = _utc_now_iso()
        rec = TubingProjectRecord(
            id=rid,
            title=str(proj.get("note") or "Imported project"),
            created=str(proj.get("saved_at", now)),
            updated=now,
            project=proj,
            pass_schedule=proj.get("pass_schedule"),
            pass_bom=None,
            quotation=None,
        )
        return proj, rec

    # Flat snapshot from ~/.tubing_master/snapshots/*.json (upsert payload) or legacy saves without ``kind``.
    if isinstance(data.get("project"), dict) and not data.get("kind"):
        rec = TubingProjectRecord(
            id=str(data.get("id") or uuid.uuid4()),
            title=str(data.get("title", "Imported")),
            created=str(data.get("created", _utc_now_iso())),
            updated=str(data.get("updated", _utc_now_iso())),
            project=dict(data["project"]),
            pass_schedule=data.get("pass_schedule"),
            pass_bom=data.get("pass_bom"),
            quotation=data.get("quotation"),
        )
        proj = dict(rec.project)
        if rec.pass_schedule is not None:
            proj["pass_schedule"] = dict(rec.pass_schedule)
        return proj, rec

    raise ValueError("Unrecognized project file format.")
