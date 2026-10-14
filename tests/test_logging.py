from pathlib import Path

from onvif_emu.cli import build_parser, overlay_from_args
from onvif_emu.confdump import render_conf
from onvif_emu.config import startup_config
from onvif_emu.logging_setup import (
    parse_log_levels,
    peek_log_level,
    peek_log_levels,
    resolve_log_path,
    setup_logging,
)
from onvif_emu.models import AppConfig


def test_verbose_sets_all_levels():
    ns = build_parser().parse_args(["-v", "--no-conf"])
    o = overlay_from_args(ns)
    assert o["logging"]["levels"] == ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def test_log_level_flag_wins_over_verbose():
    ns = build_parser().parse_args(["-v", "--log-level", "ERROR", "--no-conf"])
    o = overlay_from_args(ns)
    assert o["logging"]["levels"] == ["ERROR"]


def test_quiet_sets_error_and_critical():
    ns = build_parser().parse_args(["-q", "--no-conf"])
    assert overlay_from_args(ns)["logging"]["levels"] == ["ERROR", "CRITICAL"]


def test_multi_log_levels_cli():
    ns = build_parser().parse_args(["--log-level", "INFO,ERROR,CRITICAL", "--no-conf"])
    o = overlay_from_args(ns)
    assert o["logging"]["levels"] == ["INFO", "ERROR", "CRITICAL"]


def test_peek_log_level():
    assert peek_log_levels(["-v"]) == ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    assert peek_log_levels(["--log-level", "ERROR", "-v"]) == ["ERROR"]
    assert peek_log_levels([]) == ["CRITICAL"]
    assert peek_log_level([]) == "CRITICAL"


def test_parse_log_levels_all_and_aliases():
    assert parse_log_levels("ALL") == ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    assert parse_log_levels("WARN,FATAL") == ["WARNING", "CRITICAL"]
    assert parse_log_levels(["INFO", "info", "TRACE"]) == ["INFO", "TRACE"]


def test_default_log_beside_launch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("onvif_emu.logging_setup.executable_dir", lambda: tmp_path)
    assert resolve_log_path("").name == "onvif-emulator.log"
    assert resolve_log_path("").parent == tmp_path
    p = resolve_log_path(None, str(tmp_path / "custom.log"))
    assert p == tmp_path / "custom.log"


def test_log_dir_places_default_name(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("onvif_emu.logging_setup.executable_dir", lambda: tmp_path)
    folder = tmp_path / "logs"
    p = resolve_log_path(log_dir=folder)
    assert p == folder / "onvif-emulator.log"


def test_setup_logging_writes_file(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "t.log"
    log = setup_logging(levels=["ERROR", "CRITICAL"], log_file=log_path)
    log.error("boom-test")
    log.info("should-not-appear")
    text = log_path.read_text(encoding="utf-8")
    assert "boom-test" in text
    assert "should-not-appear" not in text


def test_independent_levels_skip_middle(tmp_path: Path):
    log_path = tmp_path / "sel.log"
    log = setup_logging(levels=["TRACE", "CRITICAL"], log_file=log_path)
    log.trace("trace-hit")  # type: ignore[attr-defined]
    log.debug("debug-skip")
    log.info("info-skip")
    log.critical("crit-hit")
    text = log_path.read_text(encoding="utf-8")
    assert "trace-hit" in text
    assert "crit-hit" in text
    assert "debug-skip" not in text
    assert "info-skip" not in text


def test_warn_normalizes_to_warning():
    cfg = AppConfig.model_validate({"logging": {"level": "warn"}})
    assert cfg.logging.level == "WARNING"
    assert cfg.logging.levels == ["WARNING"]


def test_levels_list_in_config():
    cfg = AppConfig.model_validate({"logging": {"levels": ["INFO", "ERROR"]}})
    assert cfg.logging.levels == ["INFO", "ERROR"]
    assert cfg.logging.level == "INFO"


def test_first_run_conf_russian(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("onvif_emu.confdump.executable_dir", lambda: tmp_path)
    monkeypatch.setattr("onvif_emu.paths.executable_dir", lambda: tmp_path)
    cfg, path, created = startup_config(explicit=None, no_conf=False, create_missing=True, use_env=False)
    assert created is True
    assert path is not None and path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "host:" in text
    assert "logging:" in text
    assert "#" in text


def test_no_conf_skips_auto(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("onvif_emu.confdump.executable_dir", lambda: tmp_path)
    (tmp_path / "onvif-emulator.conf").write_text("onvif_port: 9999\n", encoding="utf-8")
    cfg, path, created = startup_config(explicit=None, no_conf=True, use_env=False, create_missing=True)
    assert created is False
    assert path is None
    assert cfg.onvif_port == 8080


def test_auto_conf_is_loaded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("onvif_emu.confdump.executable_dir", lambda: tmp_path)
    (tmp_path / "onvif-emulator.conf").write_text("onvif_port: 9099\nlanguage: en\n", encoding="utf-8")
    cfg, path, created = startup_config(explicit=None, no_conf=False, use_env=False, create_missing=True)
    assert created is False
    assert cfg.onvif_port == 9099
    assert path == tmp_path / "onvif-emulator.conf"


def test_conf_comments_follow_language():
    ru = render_conf(AppConfig(), "ru")
    en = render_conf(AppConfig(), "en")
    assert "video_dir" in ru and "video_dir" in en
    assert "рядом" in ru.lower() or "Полный" in ru or "Адрес" in ru
    assert "next to the program" in en or "Full ONVIF" in en
