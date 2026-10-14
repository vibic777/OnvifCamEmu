import sys
from pathlib import Path

from onvif_emu.paths import executable_dir, iter_bin_candidates, package_dir


def test_package_dir_has_default_yaml():
    assert (package_dir() / "data" / "default.yaml").is_file()


def test_iter_bin_candidates_includes_ffmpeg_name():
    names = [p.name.lower() for p in iter_bin_candidates("ffmpeg")]
    assert any(n.startswith("ffmpeg") for n in names)


def test_executable_dir_is_cwd_when_not_frozen():
    assert executable_dir().exists()
    assert executable_dir() == Path.cwd().resolve()


def test_executable_dir_skips_venv_scripts(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    fallback = tmp_path / "appdata"
    monkeypatch.setattr("onvif_emu.paths.user_data_dir", lambda: fallback)
    monkeypatch.chdir(scripts)
    monkeypatch.setattr(sys, "argv", [str(scripts / "onvif-emulator.exe")])
    got = executable_dir()
    assert got == fallback
    assert got.is_dir()
