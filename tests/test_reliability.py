import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from onvif_emu.config_apply import apply_config
from onvif_emu.events.bus import MOTION, TAMPER, EventBus
from onvif_emu.models import AppConfig
from onvif_emu.onvif_dispatch import dispatch_pull
from onvif_emu.pipeline.manager import ChannelPipeline
from onvif_emu.pipeline.snapshot import grab_jpeg
from onvif_emu.soap import parse_envelope
from onvif_emu.state import StateStore


@pytest.mark.parametrize('raw', [[], None, {'channels': [1]}, {'channels': {'1': None}}])
def test_malformed_state_recovers(tmp_path, raw):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    store = StateStore(path)
    store.load(AppConfig())
    assert store.data.endpoint_uuid
    assert path.with_suffix('.json.bak').exists()


def test_sync_obeys_topic_filter():
    bus = EventBus()
    bus.trigger(MOTION, True)
    bus.trigger(TAMPER, True)
    sub = bus.create(topics=[MOTION])
    bus.set_sync(sub.token)
    assert {ev.topic for ev in sub.pull(128)} == {MOTION}


def test_parallel_pulls_deliver_once():
    bus = EventBus()
    sub = bus.create()
    for _ in range(128):
        bus.trigger(MOTION, True)
    with ThreadPoolExecutor(max_workers=16) as pool:
        batches = list(pool.map(lambda _: sub.pull(128), range(100)))
    assert sum(map(len, batches)) == 128


def test_unsubscribe_wakes_waiter():
    bus = EventBus()
    sub = bus.create()
    with ThreadPoolExecutor() as pool:
        waiting = pool.submit(sub.pull_wait, 10, 30)
        bus.unsubscribe(sub.token)
        assert waiting.result(timeout=1) == []


@pytest.mark.parametrize('channels', [
    [{'id': 1, 'rtsp_path': 'a'}, {'id': 1, 'rtsp_path': 'b'}],
    [{'id': 1, 'rtsp_path': 'a'}, {'id': 2, 'rtsp_path': 'a'}],
    [{'id': 1, 'rtsp_path': 'a'}, {'id': 2, 'rtsp_path': 'a_sub'}],
])
def test_conflicting_channels_rejected(channels):
    with pytest.raises(ValidationError):
        AppConfig(channels=channels)


async def test_cancelled_snapshot_reaps_child(monkeypatch, tmp_path):
    executable = tmp_path / 'ffmpeg.exe'
    executable.touch()
    started = asyncio.Event()

    class Child:
        pid = None
        returncode = None
        killed = False
        reaped = False

        async def communicate(self):
            if self.killed:
                self.reaped = True
                return b'', b''
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -1

    child = Child()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=child))
    task = asyncio.create_task(grab_jpeg(str(executable), AppConfig().channels[0]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.killed and child.reaped


async def test_pipeline_stops_producer_before_children(runtime):
    pipe = ChannelPipeline(runtime, 1)
    child = AsyncMock()
    entered = asyncio.Event()

    async def producer():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            pipe.main = child

    pipe.task = asyncio.create_task(producer())
    await entered.wait()
    await pipe.stop()
    child.stop.assert_awaited_once()
    assert pipe.task is None


async def test_push_work_is_bounded_and_joined(runtime, monkeypatch):
    async def stalled(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime.diag, '_post_async', stalled)
    for _ in range(1000):
        runtime.diag._post('http://localhost/', b'{}', 'application/json')
    assert len(runtime.diag._push_tasks) == 128
    assert runtime.diag.dropped == 872
    await runtime.diag.stop()
    assert not runtime.diag._push_tasks


async def test_non_video_config_does_not_restart_encoders(runtime, monkeypatch):
    monkeypatch.setattr(runtime.media, 'restart_pipelines', AsyncMock())
    runtime.media.pipelines = object()
    cfg = runtime.config.model_copy(deep=True)
    cfg.logging.levels = ['ERROR']
    result = await apply_config(runtime, cfg)
    assert not result.pipelines_restarted
    runtime.media.restart_pipelines.assert_not_awaited()


def test_rtsp_paths_affect_media_signature(runtime):
    before = runtime.media.signature()
    runtime.config.channels[0].rtsp_path = 'new_path'
    assert before != runtime.media.signature()


async def test_async_pull_cancel_and_unknown_token(runtime):
    sub = runtime.events.create()
    def request(token):
        return parse_envelope(
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
            f'<s:Body><PullMessages><SubscriptionId>{token}</SubscriptionId>'
            '<Timeout>PT600S</Timeout></PullMessages></s:Body></s:Envelope>'
        )
    assert 'ResourceUnknown' in await dispatch_pull(runtime, request('wrong'), anonymous=True)
    task = asyncio.create_task(dispatch_pull(runtime, request(sub.token), anonymous=True))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    runtime.events.trigger(MOTION, True)
    assert 'MotionAlarm' in await dispatch_pull(runtime, request(sub.token), anonymous=True)

@pytest.mark.parametrize('spec', ['1-999999999999', '0-65535'])
def test_port_ranges_rejected_before_allocation(spec):
    from onvif_emu.fleet import parse_ports
    with pytest.raises(ValueError):
        parse_ports(spec, 8080, 2)


@pytest.mark.parametrize('raw', [None, 12, {'users': None}])
def test_malformed_users_recovers(tmp_path, raw):
    from onvif_emu.users import UserStore
    path = tmp_path / 'users.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    store = UserStore(path)
    store.load()
    assert store.find('admin') is not None
    assert path.with_suffix('.json.bad').exists()


def test_sessions_reclaim_expired_and_bound():
    from onvif_emu.webauth import IDLE_S, Sessions
    sessions = Sessions()
    old = sessions.create('admin')
    sessions._by_token[old].last -= IDLE_S + 1
    sessions.create('admin')
    assert old not in sessions._by_token
    for _ in range(1100):
        sessions.create('admin')
    assert len(sessions._by_token) == 1024

async def test_app_setup_failure_stops_runtime(tmp_path, monkeypatch):
    from onvif_emu.app import AppFacade
    app = AppFacade(AppConfig(state_path=str(tmp_path / 'state.json')))
    app.runtime.start_background = AsyncMock()
    app.runtime.stop = AsyncMock()
    monkeypatch.setattr('aiohttp.web.AppRunner.setup', AsyncMock(side_effect=RuntimeError('setup failed')))
    with pytest.raises(RuntimeError, match='setup failed'):
        await app.run()
    app.runtime.stop.assert_awaited_once()


def test_linux_binary_search_skips_windows_vendor(tmp_path, monkeypatch):
    from onvif_emu import paths
    monkeypatch.setattr(paths.sys, 'platform', 'linux')
    monkeypatch.setattr(paths, 'vendor_dir', lambda: tmp_path / 'vendor')
    monkeypatch.setattr(paths, 'executable_dir', lambda: tmp_path)
    monkeypatch.setattr(paths, 'meipass', lambda: tmp_path)
    candidates = paths.iter_bin_candidates('ffmpeg')
    assert tmp_path / 'vendor' / 'linux64' / 'ffmpeg' in candidates
    assert all('win64' not in str(path) and path.suffix != '.exe' for path in candidates)
