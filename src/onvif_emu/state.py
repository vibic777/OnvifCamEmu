from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from onvif_emu.models import AppConfig, UserRecord
from onvif_emu.users import ADMIN, UserError, UserStore
from onvif_emu.util import atomic_write_text

log = logging.getLogger(__name__)


@dataclass
class Preset:
    token: str
    name: str
    pan: float
    tilt: float
    zoom: float


@dataclass
class ChannelState:
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0
    iris: float = 0.5
    focus: float = 0.5
    presets: list[Preset] = field(default_factory=list)
    enabled: bool = True


@dataclass
class PersistentState:
    # accounts live in data/users.json; this only carries plain-text users of an old state.json into it once
    legacy_users: list[UserRecord] = field(default_factory=list)
    channels: dict[int, ChannelState] = field(default_factory=dict)
    hostname: str = "onvif-emu"
    scopes: list[str] = field(default_factory=list)
    device_serial: str = ""
    endpoint_uuid: str = ""
    wizard_done: bool = False
    odm_checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channels": {
                str(k): {
                    "pan": v.pan,
                    "tilt": v.tilt,
                    "zoom": v.zoom,
                    "iris": v.iris,
                    "focus": v.focus,
                    "enabled": v.enabled,
                    "presets": [p.__dict__ for p in v.presets],
                }
                for k, v in self.channels.items()
            },
            "hostname": self.hostname,
            "scopes": self.scopes,
            "device_serial": self.device_serial,
            "endpoint_uuid": self.endpoint_uuid,
            "wizard_done": self.wizard_done,
            "odm_checks": self.odm_checks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersistentState:
        users = [UserRecord.model_validate(u) for u in data.get("users", [])]
        channels: dict[int, ChannelState] = {}
        for k, v in (data.get("channels") or {}).items():
            presets = [Preset(**p) for p in v.get("presets", [])]
            channels[int(k)] = ChannelState(
                pan=float(v.get("pan", 0)),
                tilt=float(v.get("tilt", 0)),
                zoom=float(v.get("zoom", 0)),
                iris=float(v.get("iris", 0.5)),
                focus=float(v.get("focus", 0.5)),
                enabled=bool(v.get("enabled", True)),
                presets=presets,
            )
        return cls(
            legacy_users=users,
            channels=channels,
            hostname=str(data.get("hostname", "onvif-emu")),
            scopes=list(data.get("scopes") or []),
            device_serial=str(data.get("device_serial", "")),
            endpoint_uuid=str(data.get("endpoint_uuid", "")),
            wizard_done=bool(data.get("wizard_done", False)),
            odm_checks=dict(data.get("odm_checks") or {}),
        )


class StateStore:
    """Thread-safe JSON state with atomic replace. Debounced saves from the event loop."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data = PersistentState()
        self._dirty = False
        self.users = UserStore.for_path(self.path.parent / "users.json")

    def load(self, cfg: AppConfig) -> None:
        with self._lock:
            if self.path.is_file():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    self.data = PersistentState.from_dict(raw)
                    log.info("State loaded path=%s", self.path)
                except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
                    log.warning("State corrupt %s: %s — resetting", self.path, exc)
                    bak = self.path.with_suffix(".json.bak")
                    try:
                        self.path.replace(bak)
                    except OSError:
                        pass
                    self.data = PersistentState()
            else:
                log.debug("State file missing %s — using defaults", self.path)
            legacy = list(self.data.legacy_users) + list(cfg.users)
            if cfg.auth.password is not None:
                legacy.insert(0, UserRecord(username=cfg.auth.username or ADMIN, password=cfg.auth.password))
            self.users.load(legacy)
            self.apply_config_accounts(cfg)
            self.data.legacy_users = []  # never written again: state.json holds no passwords any more
            if not self.data.scopes:
                self.data.scopes = list(cfg.scopes)
            if not self.data.hostname:
                self.data.hostname = cfg.device.hostname
            if not self.data.endpoint_uuid:
                from onvif_emu.util import new_uuid

                self.data.endpoint_uuid = new_uuid()
            for ch in cfg.channels:
                self.data.channels.setdefault(ch.id, ChannelState(enabled=ch.enabled))
            self._flush_unlocked()

    def apply_config_accounts(self, cfg: AppConfig) -> None:
        """One-shot account inputs carried by a config (CLI --user/--password, old files, scripts): apply, then drop."""
        for u in cfg.users:
            try:
                cur = self.users.find(u.username)
                self.users.upsert(u if cur is None else cur.model_copy(
                    update={"password": u.password, "role": u.role, "channels": u.channels}))
            except UserError as exc:
                log.warning("Config account %s ignored: %s", u.username, exc)
        if cfg.auth.password is not None:
            name = cfg.auth.username or ADMIN
            cur = self.users.find(name)
            try:
                if cur is None:
                    self.users.upsert(UserRecord(username=name, password=cfg.auth.password), create=True)
                else:
                    self.users.update(name, password=cfg.auth.password)
            except UserError as exc:
                log.warning("Config password for %s ignored: %s", name, exc)
        cfg.users = []
        cfg.auth.username = cfg.auth.password = None

    def sync_identity_from_config(self, cfg: AppConfig) -> None:
        """Identity fields from AppConfig (GUI PUT / import); accounts only through apply_config_accounts."""
        self.apply_config_accounts(cfg)
        with self._lock:
            if cfg.scopes:
                self.data.scopes = list(cfg.scopes)
            if cfg.device.hostname:
                self.data.hostname = cfg.device.hostname
            self._dirty = True

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True

    def save(self) -> None:
        with self._lock:
            self._flush_unlocked()

    def save_if_dirty(self) -> None:
        with self._lock:
            if self._dirty:
                self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        atomic_write_text(self.path, json.dumps(self.data.to_dict(), indent=2, ensure_ascii=False))
        self._dirty = False
        log.debug("State saved path=%s", self.path)

    def channel(self, channel_id: int) -> ChannelState:
        with self._lock:
            return self.data.channels.setdefault(channel_id, ChannelState())

    # accounts: thin wrappers over data/users.json (kept for the ONVIF user operations and old callers)
    def find_user(self, username: str) -> UserRecord | None:
        return self.users.find(username)

    def upsert_user(self, user: UserRecord) -> None:
        self.users.upsert(user)

    def delete_user(self, username: str) -> bool:
        try:
            self.users.delete(username)
            return True
        except UserError:
            return False

    def active_users(self) -> list[UserRecord]:
        return self.users.active()

    def users_public(self) -> list[dict]:
        return self.users.public()

    def has_admin(self) -> bool:
        return True  # admin always exists
