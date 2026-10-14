import base64
from pathlib import Path

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from onvif_emu.http_app import create_app
from onvif_emu.videos import dest_path, resolve_video_dir, safe_filename


def _basic(user: str = "admin", password: str = "admin") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_safe_filename_rejects_escape_and_bad_ext():
    assert safe_filename("clip.mp4") == "clip.mp4"
    assert safe_filename(r"..\clip.mp4") == "clip.mp4"
    with pytest.raises(ValueError):
        safe_filename("../secret.txt")
    with pytest.raises(ValueError):
        safe_filename("..")
    with pytest.raises(ValueError):
        safe_filename("")


def test_resolve_video_dir_default_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr("onvif_emu.videos.executable_dir", lambda: tmp_path)
    d = resolve_video_dir("")
    assert d == (tmp_path / "video").resolve()
    assert d.is_dir()
    custom = resolve_video_dir("clips")
    assert custom == (tmp_path / "clips").resolve()
    abs_dir = tmp_path / "abs"
    got = resolve_video_dir(str(abs_dir))
    assert got == abs_dir.resolve()


def test_dest_path_stays_inside_folder(tmp_path):
    folder = tmp_path / "video"
    folder.mkdir()
    dest = dest_path(folder, "a.mp4")
    assert dest.parent == folder.resolve()
    with pytest.raises(ValueError):
        dest_path(folder, "nope.bin")


@pytest.mark.asyncio
async def test_videos_list_and_upload(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr("onvif_emu.http_app.probe_video", lambda path, ffprobe: {"codec": "h264", "width": 640, "height": 360, "duration": 3.0})
    runtime.config.video_dir = str(tmp_path / "lib")
    app = create_app(runtime)
    auth = _basic()
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            listed = await client.get("/api/videos", headers=auth)
            assert listed.status == 200
            body = await listed.json()
            assert body["files"] == []
            assert Path(body["dir"]).is_dir()
            form = FormData()
            form.add_field("file", b"fake-mp4", filename="demo.mp4", content_type="video/mp4")
            form.add_field("channel", "1")
            up = await client.post("/api/videos", data=form, headers=auth)
            assert up.status == 200
            data = await up.json()
            assert data["ok"] is True
            assert data["file"]["name"] == "demo.mp4"
            saved = Path(data["file"]["path"])
            assert saved.is_file()
            assert saved.read_bytes() == b"fake-mp4"
            ch = next(c for c in runtime.config.channels if c.id == 1)
            assert ch.source.uri == str(saved)
            assert ch.source.type.value == "file"
            listed2 = await (await client.get("/api/videos", headers=auth)).json()
            assert any(f["name"] == "demo.mp4" for f in listed2["files"])


@pytest.mark.asyncio
async def test_video_upload_limit_removes_partial_file(runtime, tmp_path):
    runtime.config.video_dir = str(tmp_path / "limited-lib")
    runtime.config.limits.max_upload_mb = 1
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            form = FormData()
            form.add_field("file", b"x" * (1024 * 1024 + 1), filename="large.mp4", content_type="video/mp4")
            response = await client.post("/api/videos", data=form, headers=_basic())
            assert response.status == 413
            assert not list((tmp_path / "limited-lib").glob("*"))


def test_safe_filename_sanitizes_instead_of_rejecting():
    assert safe_filename("Моё видео, 2024 #1.MP4") == "Моё видео_ 2024 _1.mp4"
    assert safe_filename("Dad&Mom.mov") == "Dad_Mom.mov"
    assert safe_filename("it's.mkv") == "it_s.mkv"
    for ext in (".flv", ".wmv", ".3gp", ".mts", ".vob", ".mpeg", ".avi", ".m4v"):
        assert safe_filename("x" + ext) == "x" + ext
    with pytest.raises(ValueError):
        safe_filename("notes.txt")


def test_upload_never_overwrites(tmp_path):
    folder = tmp_path / "video"
    folder.mkdir()
    (folder / "a.mp4").write_bytes(b"old")
    assert dest_path(folder, "a.mp4", unique=True).name == "a (1).mp4"


def test_can_copy_only_matching_codec():
    from onvif_emu.videos import can_copy

    assert can_copy({"codec": "h264"}, "h264")
    assert can_copy({"codec": "hevc"}, "h265")
    assert not can_copy({"codec": "mpeg2video"}, "h264")
    assert not can_copy({"codec": "mpeg4"}, "h264")  # Xvid/DivX in AVI
    assert not can_copy({"codec": "hevc"}, "h264")
    assert not can_copy(None, "h264")


def test_browse_lists_dirs_and_videos(tmp_path):
    from onvif_emu.videos import browse

    (tmp_path / "sub").mkdir()
    (tmp_path / "a.MOV").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    got = browse(str(tmp_path))
    assert got["dirs"] == ["sub"]
    assert [f["name"] for f in got["files"]] == ["a.MOV"]
    assert browse(str(tmp_path / "a.MOV"))["dir"] == str(tmp_path.resolve())
    with pytest.raises(ValueError):
        browse(str(tmp_path / "missing"))


@pytest.mark.asyncio
async def test_local_file_is_used_without_copy(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr("onvif_emu.http_app.probe_video", lambda path, ffprobe: {"codec": "mpeg4", "width": 720, "height": 576, "duration": 9.0})
    clip = tmp_path / "Архив" / "cam 1.avi"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            r = await client.post("/api/videos/local", json={"channel": 1, "path": f'"{clip}"'}, headers=_basic())
            body = await r.json()
            assert r.status == 200 and body["ok"], body
            ch = next(c for c in runtime.config.channels if c.id == 1)
            assert ch.source.type.value == "file" and Path(ch.source.uri) == clip.resolve()
            bad = await client.post("/api/videos/local", json={"channel": 1, "path": str(tmp_path / "nope.mp4")}, headers=_basic())
            assert bad.status == 400 and "not found" in (await bad.json())["error"]
            listing = await (await client.get("/api/videos/browse", params={"dir": str(clip.parent)}, headers=_basic())).json()
            assert listing["files"][0]["name"] == "cam 1.avi"


@pytest.mark.asyncio
async def test_non_video_upload_is_refused(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr("onvif_emu.http_app.probe_video", lambda path, ffprobe: None)
    runtime.config.video_dir = str(tmp_path / "lib")
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            form = FormData()
            form.add_field("file", b"not a video", filename="fake.mp4", content_type="video/mp4")
            r = await client.post("/api/videos", data=form, headers=_basic())
            assert r.status == 400 and "no video stream" in (await r.json())["error"]
            assert not (tmp_path / "lib" / "fake.mp4").exists()
