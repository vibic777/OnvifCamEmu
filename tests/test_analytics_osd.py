from onvif_emu.analytics.simulator import AnalyticsSimulator
from onvif_emu.models import AnalyticsConfig, ImagingConfig, OsdConfig
from onvif_emu.pipeline.osd import OsdFrame, build_osd_filter, hex_to_ffmpeg, imaging_eq


def test_boxes_stay_normalized_and_ordered():
    sim = AnalyticsSimulator(AnalyticsConfig(max_objects=6, box_speed=0.9))
    sim.seed(7)
    for _ in range(400):
        sim.step(0.05)
        for obj in sim.objects:
            b = obj.bbox()
            assert 0.0 <= b["left"] <= b["right"] <= 1.0
            assert 0.0 <= b["top"] <= b["bottom"] <= 1.0


def test_deterministic_seed():
    a = AnalyticsSimulator(AnalyticsConfig(max_objects=3))
    b = AnalyticsSimulator(AnalyticsConfig(max_objects=3))
    a.seed(99)
    b.seed(99)
    assert [(o.x, o.y, o.klass) for o in a.objects] == [(o.x, o.y, o.klass) for o in b.objects]


def test_osd_includes_arrows_and_escapes():
    frame = OsdFrame(pan=1, tilt=-1, zoom=1, motion=True, text="ch1 pan:1.0")
    flt = build_osd_filter(frame, OsdConfig(), ImagingConfig())
    assert "eq=brightness" in flt
    assert "drawtext" in flt
    assert "RIGHT" in flt
    assert "DOWN" in flt
    assert "Z+" in flt
    assert "MOTION" in flt


def test_osd_disabled_only_eq():
    osd = OsdConfig(enabled=False)
    flt = build_osd_filter(OsdFrame(pan=1), osd, ImagingConfig(brightness=50, contrast=50))
    assert "drawtext" not in flt
    assert "eq=" in flt


def test_hex_color():
    assert hex_to_ffmpeg("#0f8", 1.0).startswith("0x00FF88")
    eq = imaging_eq(ImagingConfig(brightness=50, contrast=50, saturation=50, sharpness=50))
    assert "brightness=0.000" in eq
