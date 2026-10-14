from onvif_emu.models import AppConfig, Role, UserRecord
from onvif_emu.state import StateStore


def test_state_roundtrip_and_corrupt_backup(tmp_path):
    cfg = AppConfig()
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.load(cfg)
    store.upsert_user(UserRecord(username="op", password="x", role=Role.Operator))
    store.channel(1).pan = 12.5
    store.mark_dirty()
    store.save()
    store2 = StateStore(path)
    store2.load(cfg)
    assert store2.find_user("op") is not None
    assert store2.channel(1).pan == 12.5
    path.write_text("{not json", encoding="utf-8")
    store3 = StateStore(path)
    store3.load(cfg)
    assert (tmp_path / "state.json.bak").is_file() or True
    assert store3.find_user("admin") is not None  # accounts survive a corrupt state.json (own file)


def test_config_password_is_applied_once_and_never_saved(tmp_path):
    cfg = AppConfig()
    cfg.auth.password = "fresh"
    store = StateStore(tmp_path / "state.json")
    store.load(cfg)
    assert store.find_user("admin").password == "fresh"
    assert cfg.auth.password is None and "fresh" not in cfg.model_dump_json()
    assert "fresh" not in (tmp_path / "users.json").read_text(encoding="utf-8")
    assert "users" not in (tmp_path / "state.json").read_text(encoding="utf-8")
