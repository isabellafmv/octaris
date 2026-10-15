"""Finding CuraEngine per target, and the target's default."""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend import slicer
from backend.config import Config, default_target


@pytest.mark.parametrize(
    ("system", "target"), [("Windows", "windows"), ("Darwin", "macos"), ("Linux", "rpi")]
)
def test_target_defaults_to_the_running_os(system, target):
    with patch("backend.config.platform.system", return_value=system):
        assert default_target() == target
        assert Config().target == target
        assert Config(target="rpi").target == "rpi"


@pytest.fixture
def windows(tmp_path, monkeypatch) -> Path:
    """A Windows target with an empty project and Program Files; returns the project root."""
    root = tmp_path / "project"
    (root / "resources" / "bin" / "windows").mkdir(parents=True)
    monkeypatch.setattr(slicer, "PROJECT_ROOT", root)
    monkeypatch.setattr(slicer, "load_config", lambda: Config(target="windows"))
    program_files = tmp_path / "Program Files"
    program_files.mkdir()
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.setattr(slicer.shutil, "which", lambda name: None)
    return root


def _install_cura(windows: Path, folder: str) -> Path:
    exe = windows.parent / "Program Files" / folder / "CuraEngine.exe"
    exe.parent.mkdir()
    exe.touch()
    return exe


def test_windows_prefers_the_bundled_engine(windows):
    bundled = windows / "resources" / "bin" / "windows" / "CuraEngine.exe"
    bundled.touch()
    _install_cura(windows, "UltiMaker Cura 5.8.0")
    assert slicer._find_cura_engine() == str(bundled)


def test_windows_falls_back_to_the_newest_cura_install(windows):
    _install_cura(windows, "Ultimaker Cura 5.2.1")
    newest = _install_cura(windows, "UltiMaker Cura 5.10.0")
    _install_cura(windows, "UltiMaker Cura 5.9.1")
    _install_cura(windows, "SomethingElse 9.0")
    assert slicer._find_cura_engine() == str(newest)


def test_windows_falls_back_to_path(windows, monkeypatch):
    monkeypatch.setattr(slicer.shutil, "which", lambda name: r"C:\tools\CuraEngine.exe")
    assert slicer._find_cura_engine() == r"C:\tools\CuraEngine.exe"


def test_windows_without_cura_says_where_to_put_it(windows):
    with pytest.raises(slicer.SlicingError, match=r"CuraEngine\.exe"):
        slicer._find_cura_engine()
