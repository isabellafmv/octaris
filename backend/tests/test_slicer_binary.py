"""Which CuraEngine binary the slicer runs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend import slicer


def test_bundled_cura_engine_preferred_over_cura_launcher(tmp_path, monkeypatch):
    bin_dir = tmp_path / "resources" / "bin" / "macos"
    bin_dir.mkdir(parents=True)
    for name in ("UltiMaker-Cura", "CuraEngine"):
        (bin_dir / name).write_text("")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setattr(slicer, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(slicer, "load_config", lambda: SimpleNamespace(target="macos"))
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda p: False if "Applications" in str(p) else real_exists(p))

    assert slicer._find_cura_engine() == str(bin_dir / "CuraEngine")
