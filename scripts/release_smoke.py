import json
import socket
import subprocess
import time
import tomllib
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
smoke = root / "build" / "release-smoke"
smoke.mkdir(parents=True, exist_ok=True)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base, path, body=None, content_type="application/json"):
    req = urllib.request.Request(base + path, data=body, headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.read()


def soap(base, action):
    body = (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
        f"<s:Body><tds:{action}/></s:Body></s:Envelope>"
    ).encode()
    return request(base, "/onvif/device_service", body, "application/soap+xml")


for suffix in ("win64", "win64-gui"):
    exe = root / "dist" / f"onvif-emulator-v{version}-{suffix}.exe"
    http, rtsp = port(), port()
    cfg = {
        "host": "127.0.0.1",
        "public_host": "127.0.0.1",
        "onvif_port": http,
        "discovery": False,
        "gui": True,
        "auth": {"mode": "none"},
        "state_path": str(smoke / suffix / "state.json"),
        "video_dir": str(smoke / "video"),
        "network": {"rtsp_port": rtsp},
        "mediamtx": {"api_port": port(), "hls_port": port(), "webrtc_port": port()},
        "channels": [
            {"id": 1, "streams": 1, "encoder": {"width": 320, "height": 240, "fps": 10, "bitrate": 256}}
        ],
        "osd": {"enabled": False},
        "logging": {"file": str(smoke / f"{suffix}.log"), "levels": ["ERROR", "CRITICAL"]},
    }
    config = smoke / f"{suffix}.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")
    with (smoke / f"{suffix}-console.log").open("wb") as output:
        proc = subprocess.Popen(
            [str(exe), "--config", str(config), "--no-conf", "--no-open-browser", "--no-tray"],
            cwd=smoke,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            base = f"http://127.0.0.1:{http}"
            deadline = time.monotonic() + 75
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f"{suffix} exited: {proc.returncode}")
                try:
                    health = json.loads(request(base, "/health"))
                    if health["mediamtx"] and health["pipelines"].get("1"):
                        break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{suffix} not ready")
                time.sleep(0.5)
            assert health["version"] == version, health
            assert b"<html" in request(base, "/").lower()
            assert b"GetDeviceInformationResponse" in soap(base, "GetDeviceInformation")
            jpg = request(base, "/snapshot/1.jpg")
            assert jpg.startswith(b"\xff\xd8") and len(jpg) > 1000
            result = subprocess.run(
                [
                    str(root / "vendor/win64/ffprobe.exe"),
                    "-v",
                    "error",
                    "-rtsp_transport",
                    "tcp",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height",
                    "-of",
                    "json",
                    f"rtsp://127.0.0.1:{rtsp}/ch1",
                ],
                capture_output=True,
                timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert result.returncode == 0, result.stderr
            video = json.loads(result.stdout)["streams"][0]
            assert video == {"codec_name": "h264", "width": 320, "height": 240}, video
            assert b"SystemRebootResponse" in soap(base, "SystemReboot")
            assert proc.wait(timeout=20) == 0
            with socket.socket() as sock:
                assert sock.connect_ex(("127.0.0.1", rtsp)) != 0, "RTSP port leaked after shutdown"
            print(
                f"{suffix}: HTTP, GUI assets, SOAP, JPEG, RTSP H264 320x240, graceful shutdown, RTSP port release OK",
                flush=True,
            )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
