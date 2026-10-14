from onvif_emu.config_apply import ConfigApplyResult
from onvif_emu.identity import IdentityStore
from onvif_emu.models import AppConfig, Role, UserRecord
from onvif_emu.runtime import REBOOT_DELAY_S, Runtime
from onvif_emu.state import StateStore


def test_identity_store_sync(tmp_path):
    cfg = AppConfig()
    cfg.auth.password = "fresh"
    store = StateStore(tmp_path / "state.json")
    store.load(cfg)
    ident = IdentityStore(store)
    ident.sync_from_config(cfg)
    assert ident.find_user("admin").password == "fresh"
    assert ident.has_admin()
    ident.upsert_user(UserRecord(username="op", password="x", role=Role.Operator))
    assert ident.find_user("op") is not None
    assert ident.effective_scopes(cfg)


def test_config_apply_result_defaults():
    r = ConfigApplyResult()
    assert r.identity_synced is False
    assert r.mediamtx_restarted is False
    assert r.pipelines_restarted is False


def test_reboot_semantics_constant():
    assert REBOOT_DELAY_S == 3.0


def test_runtime_exposes_identity_and_media(tmp_path):
    cfg = AppConfig()
    cfg.state_path = str(tmp_path / "state.json")
    cfg.mediamtx.embedded = False
    rt = Runtime(cfg, config_path=tmp_path / "cfg.yaml")
    assert isinstance(rt.identity, IdentityStore)
    assert rt.media is not None
    assert rt.media.signature()[0] == cfg.network.rtsp_port
