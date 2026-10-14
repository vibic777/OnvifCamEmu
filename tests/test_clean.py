import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("clean_script", _ROOT / "scripts" / "clean.py")
assert _spec is not None and _spec.loader is not None
_clean = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_clean)
iter_targets = _clean.iter_targets
clean_main = _clean.main


def test_dry_run_lists_pycache(tmp_path: Path):
    junk = tmp_path / "pkg" / "__pycache__"
    junk.mkdir(parents=True)
    (junk / "x.pyc").write_text("x", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "vendor" / "cache").mkdir(parents=True)
    (tmp_path / "vendor" / "cache" / "ffmpeg.zip").write_bytes(b"00")
    (tmp_path / ".venv").mkdir()
    targets = iter_targets(tmp_path, venv=False, vendor=True)
    names = {p.name for p in targets}
    assert "__pycache__" in names
    assert "build" in names
    assert "cache" in names
    assert not any(p.name == ".venv" for p in targets)
    rc = clean_main(["--dry-run", "--root", str(tmp_path)])
    assert rc == 0
    assert junk.is_dir()  # dry-run did not delete


def test_clean_deletes_and_keeps_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "a.pyc").write_bytes(b"1")
    rc = clean_main(["--root", str(tmp_path)])
    assert rc == 0
    assert not cache.exists()
    assert (tmp_path / ".git" / "HEAD").is_file()


def test_all_removes_venv(tmp_path: Path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    rc = clean_main(["--all", "--root", str(tmp_path)])
    assert rc == 0
    assert not venv.exists()
