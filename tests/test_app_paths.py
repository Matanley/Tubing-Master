"""App path helpers for dev vs frozen installs."""

from pathlib import Path

import sys

import pytest
import runpy

from tubing_master.app_paths import (
    damask_templates_dir,
    dispatch_frozen_subprocess_if_needed,
    projects_dir,
    suggested_projects_dir,
    use_app_data_dirs,
)


def test_damask_templates_dir_exists():
    d = damask_templates_dir()
    assert d.is_dir()
    assert (d / "material.yaml").is_file()


def test_dev_projects_under_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("TUBING_MASTER_APP_DATA", raising=False)
    monkeypatch.chdir(tmp_path)
    p = projects_dir()
    assert p == tmp_path / "Projects"
    assert p.is_dir()


def test_frozen_subprocess_dispatch_c(monkeypatch):
    monkeypatch.setattr("tubing_master.app_paths.is_frozen", lambda: True)
    monkeypatch.setattr(sys, "argv", ["Tubing Master", "-c", "import sys; sys.exit(7)"])
    with pytest.raises(SystemExit) as exc:
        dispatch_frozen_subprocess_if_needed()
    assert exc.value.code == 7


def test_frozen_subprocess_ignored_when_not_frozen(monkeypatch):
    monkeypatch.setattr("tubing_master.app_paths.is_frozen", lambda: False)
    monkeypatch.setattr(sys, "argv", ["main.py", "-c", "import sys; sys.exit(7)"])
    dispatch_frozen_subprocess_if_needed()


def test_frozen_worker_pass_dispatch(monkeypatch):
    monkeypatch.setattr("tubing_master.app_paths.is_frozen", lambda: True)
    monkeypatch.setattr(sys, "argv", ["Tubing Master", "--worker-pass"])
    called: list[str] = []

    def fake_run_module(module: str, **_kw):
        called.append(module)
        raise SystemExit(0)

    monkeypatch.setattr("runpy.run_module", fake_run_module)
    with pytest.raises(SystemExit) as exc:
        dispatch_frozen_subprocess_if_needed()
    assert exc.value.code == 0
    assert called == ["tubing_master.fea_tube_die"]


def test_app_data_projects(monkeypatch, tmp_path):
    monkeypatch.setenv("TUBING_MASTER_APP_DATA", "1")
    monkeypatch.setattr(
        "tubing_master.app_paths.user_data_root",
        lambda: tmp_path / "AppData",
    )
    assert use_app_data_dirs()
    p = projects_dir()
    assert p == tmp_path / "AppData" / "Projects"
    s = suggested_projects_dir()
    assert s == tmp_path / "AppData" / "suggested_projects"
