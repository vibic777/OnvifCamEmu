import json

import pytest

from onvif_emu import secret
from onvif_emu.models import Role, UserRecord
from onvif_emu.users import UserError, UserStore


def _store(tmp_path, legacy=None):
    s = UserStore(tmp_path / "users.json")
    s.load(legacy)
    return s


def test_encrypt_roundtrip_and_tamper():
    tok = secret.encrypt("пароль:1")
    assert tok.startswith("enc1:") and "пароль" not in tok
    assert secret.decrypt(tok) == "пароль:1"
    assert secret.encrypt("x") != secret.encrypt("x")  # random nonce
    with pytest.raises(ValueError):
        secret.decrypt(tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB"))


def test_first_run_migrates_and_encrypts(tmp_path):
    s = _store(tmp_path, [UserRecord(username="admin", password="old"), UserRecord(username="op", password="p", role=Role.Operator)])
    raw = (tmp_path / "users.json").read_text(encoding="utf-8")
    assert "old" not in raw and '"p"' not in raw and "enc1:" in raw
    assert s.check("admin", "old") and s.check("op", "p")


def test_admin_reset_by_deleting_password_line(tmp_path):
    s = _store(tmp_path)
    s.update("admin", password="secret")
    data = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    del data["users"][0]["password"]
    (tmp_path / "users.json").write_text(json.dumps(data), encoding="utf-8")
    s2 = _store(tmp_path)
    assert s2.check("admin", "admin") and s2.admin_default_password


def test_plain_password_typed_in_file_gets_encrypted(tmp_path):
    (tmp_path / "users.json").write_text(json.dumps({"users": [{"username": "admin", "password": "typed"}]}), encoding="utf-8")
    s = _store(tmp_path)
    assert s.check("admin", "typed")
    assert "typed" not in (tmp_path / "users.json").read_text(encoding="utf-8")


def test_admin_rules_and_disabled_accounts(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(UserError):
        s.delete("admin")
    with pytest.raises(UserError):
        s.update("admin", enabled=False)
    with pytest.raises(UserError):
        s.update("admin", role=Role.Viewer)
    s.upsert(UserRecord(username="v1", password="p", role=Role.Viewer, channels=[2]), create=True)
    with pytest.raises(UserError):
        s.upsert(UserRecord(username="v1", password="p"), create=True)
    assert s.find("v1").sees(2) and not s.find("v1").sees(1)
    s.update("v1", enabled=False)
    assert s.check("v1", "p") is None and all(u.username != "v1" for u in s.active())
    with pytest.raises(UserError):
        s.upsert(UserRecord(username="bad name!", password="p"))
    s.delete("v1")
    assert s.find("v1") is None


def test_onvif_levels():
    assert Role.Viewer.onvif_level == "User"
    assert Role.from_any("User") is Role.Viewer and Role.from_any("operator") is Role.Operator


@pytest.mark.asyncio
async def test_old_config_is_scrubbed_of_passwords(tmp_path):
    import yaml

    from onvif_emu.config import load_yaml
    from onvif_emu.models import AppConfig
    from onvif_emu.runtime import Runtime

    conf = tmp_path / "old.yaml"
    conf.write_text(yaml.safe_dump({"auth": {"mode": "digest", "username": "admin", "password": "legacy"},
                                    "users": [{"username": "op", "password": "oppw", "role": "Operator"}],
                                    "state_path": str(tmp_path / "state.json")}), encoding="utf-8")
    cfg = AppConfig.model_validate(load_yaml(conf))
    rt = Runtime(cfg, conf)
    rt.state.load(rt.config)
    rt._scrub_config_passwords()
    text = conf.read_text(encoding="utf-8")
    assert "legacy" not in text and "oppw" not in text and "password" not in text
    assert rt.state.users.check("admin", "legacy") and rt.state.users.check("op", "oppw")
