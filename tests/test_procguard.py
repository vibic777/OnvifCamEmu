import socket
import subprocess
import sys
import time

import pytest

from onvif_emu import procguard


def test_port_in_use_sees_a_listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert procguard.port_in_use(port)
    finally:
        srv.close()
    assert not procguard.port_in_use(port)


def test_orphan_publishers_only_ours(monkeypatch):
    if sys.platform != "win32":
        assert procguard.kill_orphan_publishers(18554) == 0
        return
    listing = "\n".join([
        "101\tffmpeg.exe -re -i x -f rtsp rtsp://127.0.0.1:18554/ch1",
        "102\tffmpeg.exe -i rtsp://camera:554/live -f null -",  # someone else's ffmpeg
        "103\tffmpeg.exe -f rtsp rtsp://127.0.0.1:28554/ch1",  # another emulator's port
    ])
    killed = []
    monkeypatch.setattr(procguard, "_run", lambda cmd, timeout=8.0: listing)
    monkeypatch.setattr(procguard, "_kill", killed.append)
    assert procguard.kill_orphan_publishers(18554) == 1
    assert killed == [101]


def test_foreign_owner_is_reported_not_killed(monkeypatch, tmp_path):
    monkeypatch.setattr(procguard, "kill_orphan_publishers", lambda *a: 0)
    monkeypatch.setattr(procguard, "port_in_use", lambda port: True)
    owner = procguard.PortOwner(55, "mediamtx.exe", r"C:\other\mediamtx.exe C:\other\mediamtx.yml", parent_alive=False)
    monkeypatch.setattr(procguard, "port_owner", lambda port: owner)
    monkeypatch.setattr(procguard, "_kill", lambda pid: pytest.fail("must not kill a foreign MediaMTX"))
    reason = procguard.free_rtsp_port(8554, tmp_path / "data" / "mediamtx.yml")
    assert "8554" in reason and "PID 55" in reason


def test_own_leftover_mediamtx_is_removed(monkeypatch, tmp_path):
    cfg = tmp_path / "data" / "mediamtx.yml"
    state = {"busy": True}
    monkeypatch.setattr(procguard, "kill_orphan_publishers", lambda *a: 0)
    monkeypatch.setattr(procguard, "port_in_use", lambda port: state["busy"])
    owner = procguard.PortOwner(77, "mediamtx.exe", f"C:\\tmp\\mediamtx.exe {cfg}", parent_alive=False)
    monkeypatch.setattr(procguard, "port_owner", lambda port: owner)
    monkeypatch.setattr(procguard, "_kill", lambda pid: state.update(busy=False))
    assert procguard.free_rtsp_port(8554, cfg) == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_bound_child_dies_with_parent():
    """A parent that binds its child and is then killed hard takes the child with it."""
    code = (
        "import subprocess,sys,time;from onvif_emu.procguard import bind_child;"
        "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "bind_child(c.pid);print(c.pid,flush=True);time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child_pid = int(parent.stdout.readline())
    parent.kill()
    parent.wait()
    for _ in range(50):
        out = subprocess.run(["tasklist", "/FI", f"PID eq {child_pid}", "/NH"], capture_output=True, text=True).stdout
        if str(child_pid) not in out:
            break
        time.sleep(0.1)
    assert str(child_pid) not in out


def test_busy_side_ports_move():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    busy = srv.getsockname()[1]
    try:
        api, hls, webrtc, slot, notes = procguard.resolve_mtx_ports(busy + 50, busy, busy + 60, 0, busy + 70)
    finally:
        srv.close()
    assert hls != busy and hls not in (api, webrtc)
    assert any(n.startswith(f"HLS port {busy} busy") for n in notes)
