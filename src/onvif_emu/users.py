"""Accounts: data/users.json with encrypted passwords (shared by ONVIF, RTSP, snapshots and the web UI).

Rules
* ``admin`` always exists, is an Administrator, enabled, sees every channel; it cannot be deleted or
  disabled, only its password changes.
* Password reset: delete the ``"password"`` line of ``admin`` (or the whole entry) and restart - the
  password becomes ``admin``. A password typed into the file as plain text is encrypted on the next start.
* Roles: Administrator (everything), Operator (watch + PTZ), Viewer (watch). Operator / Viewer may be
  limited to a list of channels (empty = all).

One file per data folder, shared by every device of a fleet (one lock, one in-memory copy per path).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from onvif_emu import secret
from onvif_emu.models import Role, UserRecord
from onvif_emu.util import atomic_write_text

log = logging.getLogger(__name__)

ADMIN = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"
_NAME = re.compile(r"^[A-Za-z0-9_.@\-]{1,64}$")


class UserError(ValueError):
    """A rejected account change; the message is shown to the user."""


class UserStore:
    _registry: dict[Path, UserStore] = {}
    _registry_lock = threading.Lock()

    @classmethod
    def for_path(cls, path: Path | str) -> UserStore:
        p = Path(path).resolve()
        with cls._registry_lock:
            store = cls._registry.get(p)
            if store is None:
                store = cls._registry[p] = cls(p)
            return store

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._users: list[UserRecord] = []
        self._loaded = False
        self.admin_default_password = False  # admin still has admin/admin: the GUI nags about it

    # ------------------------------------------------------------------ load / save
    def load(self, legacy: list[UserRecord] | None = None) -> None:
        """Read the file (once); on first run seed it from ``legacy`` accounts (old config / state.json)."""
        with self._lock:
            if self._loaded:
                return
            rewrite = False
            if self.path.is_file():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    self._users, rewrite = self._parse(raw)
                except (OSError, ValueError) as exc:
                    log.critical("users.json unreadable (%s): %s - keeping a copy, admin reset to admin/admin", self.path, exc)
                    try:
                        self.path.replace(self.path.with_suffix(".json.bad"))
                    except OSError:
                        pass
                    self._users, rewrite = [], True
            else:
                seen: set[str] = set()
                for u in legacy or []:
                    if u.username not in seen:
                        seen.add(u.username)
                        self._users.append(u.model_copy())
                if self._users:
                    log.warning("Accounts moved into %s (passwords encrypted)", self.path)
                rewrite = True
            rewrite |= self._ensure_admin()
            self._loaded = True
            if rewrite:
                self._save_unlocked()

    def _parse(self, raw: Any) -> tuple[list[UserRecord], bool]:
        items = raw.get("users", []) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValueError("users must be a list")
        out: list[UserRecord] = []
        rewrite = False
        for item in items or []:
            if not isinstance(item, dict) or not item.get("username"):
                continue
            pw = item.get("password")
            if pw is None:
                if item["username"] == ADMIN:
                    log.critical("users.json: admin has no password line - password reset to '%s'", DEFAULT_ADMIN_PASSWORD)
                    plain = DEFAULT_ADMIN_PASSWORD
                else:
                    log.warning("users.json: %s has no password - account disabled", item["username"])
                    plain, item["enabled"] = "", False
                rewrite = True
            elif secret.is_encrypted(str(pw)):
                try:
                    plain = secret.decrypt(str(pw))
                except ValueError:
                    log.critical("users.json: password of %s cannot be decrypted - %s", item["username"],
                                 "reset to 'admin'" if item["username"] == ADMIN else "account disabled")
                    plain = DEFAULT_ADMIN_PASSWORD if item["username"] == ADMIN else ""
                    if item["username"] != ADMIN:
                        item["enabled"] = False
                    rewrite = True
            else:
                plain = str(pw)  # typed in by hand: encrypt it on save
                rewrite = True
            try:
                out.append(UserRecord.model_validate({**item, "password": plain}))
            except ValueError as exc:
                log.warning("users.json: skipping %s: %s", item.get("username"), exc)
        return out, rewrite

    def _ensure_admin(self) -> bool:
        admin = next((u for u in self._users if u.username == ADMIN), None)
        changed = False
        if admin is None:
            log.critical("users.json: no admin account - created admin/%s", DEFAULT_ADMIN_PASSWORD)
            admin = UserRecord(username=ADMIN, password=DEFAULT_ADMIN_PASSWORD)
            self._users.insert(0, admin)
            changed = True
        if admin.role != Role.Administrator or not admin.enabled or admin.channels:
            admin.role, admin.enabled, admin.channels = Role.Administrator, True, []
            changed = True
        if not admin.password:
            admin.password = DEFAULT_ADMIN_PASSWORD
            changed = True
        self.admin_default_password = admin.password == DEFAULT_ADMIN_PASSWORD
        return changed

    def _save_unlocked(self) -> None:
        payload = {
            "version": 1,
            "note": "Passwords are encrypted. Reset admin: delete its \"password\" line and restart (password becomes admin).",
            "users": [
                {"username": u.username, "password": secret.encrypt(u.password) if u.password else None,
                 "role": u.role.value, "enabled": u.enabled, "channels": list(u.channels)}
                for u in self._users
            ],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            log.error("Cannot write %s: %s", self.path, exc)

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()

    # ------------------------------------------------------------------ queries
    def all(self) -> list[UserRecord]:
        with self._lock:
            return [u.model_copy() for u in self._users]

    def active(self) -> list[UserRecord]:
        """Accounts that may sign in (disabled ones are invisible to every protocol)."""
        with self._lock:
            return [u.model_copy() for u in self._users if u.enabled and u.password]

    def find(self, username: str) -> UserRecord | None:
        with self._lock:
            u = next((u for u in self._users if u.username == username), None)
            return u.model_copy() if u else None

    def check(self, username: str, password: str) -> UserRecord | None:
        import hmac

        u = self.find(username)
        if u and u.enabled and u.password and hmac.compare_digest(u.password.encode(), password.encode()):
            return u
        return None

    def public(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"username": u.username, "role": u.role.value, "enabled": u.enabled, "channels": list(u.channels),
                     "main": u.username == ADMIN} for u in self._users]

    # ------------------------------------------------------------------ changes
    def upsert(self, user: UserRecord, *, create: bool | None = None) -> UserRecord:
        """Add or replace an account. ``create`` True/False = must not / must exist already."""
        if not _NAME.match(user.username or ""):
            raise UserError("user name: 1-64 characters, letters, digits and . _ - @")
        with self._lock:
            idx = next((i for i, u in enumerate(self._users) if u.username == user.username), None)
            if create is True and idx is not None:
                raise UserError(f"user {user.username} already exists")
            if create is False and idx is None:
                raise UserError(f"no user {user.username}")
            if user.username == ADMIN:
                user = user.model_copy(update={"role": Role.Administrator, "enabled": True, "channels": []})
            if not user.password:
                raise UserError("password must not be empty")
            if user.role == Role.Administrator:
                user = user.model_copy(update={"channels": []})
            if idx is None:
                self._users.append(user)
            else:
                self._users[idx] = user
            self._ensure_admin()
            self._save_unlocked()
            log.info("Account %s saved role=%s enabled=%s channels=%s", user.username, user.role.value, user.enabled,
                     user.channels or "all")
            return user.model_copy()

    def update(self, username: str, **changes: Any) -> UserRecord:
        cur = self.find(username)
        if cur is None:
            raise UserError(f"no user {username}")
        if username == ADMIN:
            for key in ("role", "enabled", "channels"):
                if key in changes and changes[key] != getattr(cur, key):
                    raise UserError("admin is the main account: only its password can change")
        data = cur.model_dump()
        data.update({k: v for k, v in changes.items() if v is not None})
        return self.upsert(UserRecord.model_validate(data), create=False)

    def delete(self, username: str) -> None:
        if username == ADMIN:
            raise UserError("admin is the main account and cannot be deleted")
        with self._lock:
            before = len(self._users)
            self._users = [u for u in self._users if u.username != username]
            if len(self._users) == before:
                raise UserError(f"no user {username}")
            self._save_unlocked()
            log.info("Account %s deleted", username)
