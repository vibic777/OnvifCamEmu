from onvif_emu.cheat import print_cheat
from onvif_emu.cli import build_parser, load_from_namespace, overlay_from_args
from onvif_emu.doctor import run_doctor
from onvif_emu.exit_codes import OK
from onvif_emu.models import AppConfig


def test_help_contains_examples():
    p = build_parser()
    text = p.format_help()
    assert "--auth" in text
    assert "onvif-emulator --no-gui" in text
    assert "--log-level" in text
    assert "--log-file" in text
    assert "--no-conf" in text
    assert "--service" in text
    assert "--video-dir" in text
    assert "--open-browser" in text
    assert text.count("onvif-emulator") >= 5


def test_overlay_service_keeps_gui_and_video_dir():
    ns = build_parser().parse_args(["--service", "--video-dir", "D:/clips", "--open-browser"])
    o = overlay_from_args(ns)
    assert o["gui"] is True
    assert o["video_dir"] == "D:/clips"
    assert ns.open_browser is True
    ns2 = build_parser().parse_args(["--service", "--headless"])
    assert overlay_from_args(ns2)["gui"] is False


def test_overlay_channels_and_auth():
    ns = build_parser().parse_args(
        ["--auth", "none", "--channels", "3", "--channel", "2:source=./a.mp4,encoder=h264", "--headless", "--no-discovery"]
    )
    o = overlay_from_args(ns)
    assert o["auth"]["mode"] == "none"
    assert o["gui"] is False
    assert o["discovery"] is False
    assert len(o["channels"]) == 3
    assert o["channels"][1]["source"]["uri"] == "./a.mp4"


def test_check_config_and_cheat(tmp_path, monkeypatch):
    ns = build_parser().parse_args(["--check-config"])
    cfg = load_from_namespace(ns)
    assert cfg.channels
    sheet = print_cheat("exit")
    assert "0  OK" in sheet
    assert "Unknown" in print_cheat("nope")


def test_doctor_runs():
    code, text = run_doctor(AppConfig())
    assert "FFmpeg" in text
    assert code in {0, 2, 3}


def test_cli_main_check_config():
    from onvif_emu.cli import main

    assert main(["--check-config"]) == OK
    assert main(["cheat-sheet", "launch"]) == OK
    assert main(["--version"]) == 0
