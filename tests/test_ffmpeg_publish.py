from onvif_emu.models import ChannelConfig
from onvif_emu.pipeline.ffmpeg import build_command, publish_url
from onvif_emu.pipeline.manager import HEALTHY_S


def test_publish_url_host():
    assert publish_url(8554, "ch1") == "rtsp://127.0.0.1:8554/ch1"
    assert publish_url(8554, "ch1", host="mediamtx") == "rtsp://mediamtx:8554/ch1"


def test_build_command_uses_publish_host():
    ch = ChannelConfig(id=1, name="Main", rtsp_path="ch1")
    cmd = build_command("ffmpeg", ch, 8554, "", False, publish_host="mediamtx")
    assert cmd[-1] == "rtsp://mediamtx:8554/ch1"
    assert HEALTHY_S >= 5.0
