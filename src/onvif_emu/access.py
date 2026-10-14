"""Who is asking: the account of the current ONVIF / web request and what it may see.

``dispatch`` (ONVIF) sets ``CURRENT_USER`` for the duration of one request; ``Runtime.enabled_channels()``
then hides channels an Operator / Viewer is not allowed to watch, so GetProfiles, GetStreamUri, snapshots,
PTZ nodes and imaging all agree without every handler checking. Outside a request nobody is set and
everything is visible (pipelines, health, the internal code).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from onvif_emu.models import Role, UserRecord

CURRENT_USER: ContextVar[UserRecord | None] = ContextVar("onvif_emu_user", default=None)


def current_user() -> UserRecord | None:
    return CURRENT_USER.get()


def sees(channel_id: int, user: UserRecord | None = None) -> bool:
    u = user if user is not None else CURRENT_USER.get()
    return u is None or u.sees(channel_id)


def may_ptz(user: UserRecord | None) -> bool:
    return user is None or user.role in (Role.Administrator, Role.Operator)


def is_admin(user: UserRecord | None) -> bool:
    return user is None or user.role == Role.Administrator


@contextmanager
def acting_as(user: UserRecord | None) -> Iterator[None]:
    token = CURRENT_USER.set(user)
    try:
        yield
    finally:
        CURRENT_USER.reset(token)
