from __future__ import annotations

import math

from onvif_emu.models import PtzConfig
from onvif_emu.ptz.servo import Axis, PtzServo, simulate_until


def test_constant_velocity_reaches_target_without_overshoot():
    ax = Axis("p", 0.0, -180.0, 180.0, max_speed=30.0, accel=0.0)
    ax.goto(45.0)
    pos = []
    t, dt = 0.0, 0.05
    while t < 5.0:
        ax.step(dt)
        pos.append(ax.value)
        t += dt
        if ax.target is None:
            break
    assert pos[-1] == 45.0
    assert max(pos) <= 45.0 + 1e-9
    assert ax.velocity == 0.0


def test_never_exceeds_mechanical_limits():
    ax = Axis("p", 0.0, -10.0, 10.0, max_speed=100.0)
    ax.goto(999.0)
    for _ in range(200):
        ax.step(0.05)
        assert -10.0 <= ax.value <= 10.0
    assert ax.value == 10.0


def test_continuous_stops_at_limit():
    ax = Axis("p", 0.0, -5.0, 5.0, max_speed=50.0)
    ax.continuous(1.0)
    for _ in range(400):
        ax.step(0.05)
        assert ax.value <= 5.0 + 1e-9
    assert abs(ax.value - 5.0) < 1e-6
    assert ax.velocity == 0.0


def test_dt_independence_final_position():
    def run(dt: float) -> float:
        ax = Axis("p", 0.0, -180.0, 180.0, max_speed=20.0)
        ax.goto(10.0)
        t = 0.0
        while t < 2.0:
            ax.step(dt)
            t += dt
        return ax.value

    assert math.isclose(run(0.01), 10.0, abs_tol=1e-6)
    assert math.isclose(run(0.05), 10.0, abs_tol=1e-6)
    assert math.isclose(run(0.1), 10.0, abs_tol=1e-6)


def test_trapezoidal_no_overshoot():
    ax = Axis("z", 0.0, 0.0, 1.0, max_speed=1.0, accel=4.0)
    ax.goto(0.5)
    peak = 0.0
    for _ in range(500):
        ax.step(0.01)
        peak = max(peak, ax.value)
        if ax.target is None and ax.velocity == 0.0:
            break
    assert ax.value == 0.5
    assert peak <= 0.5 + 1e-6


def test_relative_and_absolute_and_stop():
    s = PtzServo(1, PtzConfig(pan_speed=60, tilt_speed=40, zoom_speed=1))
    s.absolute_move(30.0, -10.0, 0.4, now=0)
    simulate_until(s, 3.0, 0.05)
    st = s.status()
    assert math.isclose(st.pan, 30.0, abs_tol=0.05)
    assert math.isclose(st.tilt, -10.0, abs_tol=0.05)
    assert math.isclose(st.zoom, 0.4, abs_tol=0.05)
    s.relative_move(dpan=5.0, now=10)
    simulate_until(s, 1.0, 0.05)
    assert s.pan.value > 30.0
    s.continuous_move(vpan=1.0, now=20)
    s.step(0.05, now=20.05)
    assert s.kind == "continuous"
    s.stop(now=21)
    assert s.pan.cmd_velocity is None
    assert s.last_command == "Stop"


def test_nan_and_inf_are_finite():
    ax = Axis("p", 0.0, -1.0, 1.0, max_speed=1.0)
    ax.goto(float("nan"))
    ax.step(0.05)
    assert math.isfinite(ax.value)
    ax.continuous(float("inf"))
    ax.step(0.05)
    assert math.isfinite(ax.value)
    assert abs(ax.value) <= 1.0


def test_zero_dt_is_noop():
    ax = Axis("p", 3.0, -10.0, 10.0, max_speed=10)
    ax.goto(9.0)
    assert ax.step(0.0) is False
    assert ax.value == 3.0
