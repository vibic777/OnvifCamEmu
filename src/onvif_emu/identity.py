"""Identity ownership: users, scopes, hostname (authoritative for ONVIF + Admin API)."""

from __future__ import annotations

import logging

from onvif_emu.models import AppConfig, UserRecord
from onvif_emu.state import StateStore

log = logging.getLogger(__name__)


class IdentityStore:
    """Thin façade over PersistentState identity fields."""

    def __init__(self, state: StateStore):
        self._state = state

    @property
    def state(self) -> StateStore:
        return self._state

    @property
    def users(self) -> list[UserRecord]:
        """Accounts that may sign in (ONVIF, RTSP, web)."""
        return self._state.active_users()

    def find_user(self, username: str) -> UserRecord | None:
        return self._state.find_user(username)

    def upsert_user(self, user: UserRecord) -> None:
        log.info("Identity upsert user=%s role=%s", user.username, user.role)
        self._state.upsert_user(user)

    def delete_user(self, username: str) -> bool:
        ok = self._state.delete_user(username)
        log.info("Identity delete user=%s ok=%s", username, ok)
        return ok

    def has_admin(self) -> bool:
        return self._state.has_admin()

    def users_public(self) -> list[dict[str, str]]:
        return self._state.users_public()

    def effective_scopes(self, cfg: AppConfig) -> list[str]:
        return list(self._state.data.scopes or cfg.scopes)

    @property
    def hostname(self) -> str:
        return self._state.data.hostname

    def set_hostname(self, hostname: str) -> None:
        log.info("Identity hostname=%s", hostname)
        self._state.data.hostname = hostname
        self._state.mark_dirty()

    def set_scopes(self, scopes: list[str]) -> None:
        log.info("Identity scopes count=%s", len(scopes))
        self._state.data.scopes = list(scopes)
        self._state.mark_dirty()

    def sync_from_config(self, cfg: AppConfig) -> None:
        log.debug("Identity sync from config users=%s", len(cfg.users))
        self._state.sync_identity_from_config(cfg)

    def save(self) -> None:
        self._state.save()
