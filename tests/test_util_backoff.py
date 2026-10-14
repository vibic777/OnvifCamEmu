import random
from pathlib import Path

from onvif_emu.pipeline.backoff import FullJitterBackoff
from onvif_emu.services.events import _timeout_seconds
from onvif_emu.timeutil import clamp, nearly_equal, posix_tz
from onvif_emu.util import atomic_write_text


def test_full_jitter_is_bounded_and_grows_cap():
    rng_state = random.getstate()
    try:
        random.seed(0)
        b = FullJitterBackoff(base=1.0, cap=8.0)
        delays = [b.next_delay() for _ in range(12)]
        assert all(0 <= d <= 8.0 for d in delays)
        # Without reset, attempt keeps growing (cap stays at 8)
        assert b.attempt >= 12
        b.reset()
        assert b.attempt == 0
        assert b.peek_max() == 1.0
        # After reset, delays start small again
        random.seed(0)
        first = b.next_delay()
        assert first <= 1.0
    finally:
        random.setstate(rng_state)


def test_iso_duration_parser():
    assert _timeout_seconds("PT5S") == 5.0
    assert _timeout_seconds("PT1M") == 60.0
    assert _timeout_seconds("PT1H2M3S") == 3723.0
    assert _timeout_seconds("") == 60.0


def test_posix_tz_utc():
    assert posix_tz("UTC").startswith("UTC")
    assert nearly_equal(1.0, 1.0 + 1e-9, 1e-8)
    assert clamp(5, 0, 1) == 1
    assert clamp(-2, 0, 1) == 0


def test_atomic_write(tmp_path: Path):
    dest = tmp_path / "a.json"
    atomic_write_text(dest, '{"ok": true}')
    assert dest.read_text(encoding="utf-8") == '{"ok": true}'
    atomic_write_text(dest, "second")
    assert dest.read_text(encoding="utf-8") == "second"
    assert list(tmp_path.glob("*.tmp")) == []
