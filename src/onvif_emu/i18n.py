"""GUI localization. CLI/log/--help stay English; only the web UI is translated."""

from __future__ import annotations

import json
from typing import Any

from onvif_emu.paths import package_dir

DEFAULT_LANG = "ru"
SUPPORTED_LANGS = ("ru", "en")
_CACHE: dict[str, dict[str, Any]] = {}


def locales_dir():
    return package_dir() / "locales"


def normalize_lang(lang: str | None) -> str:
    code = (lang or DEFAULT_LANG).strip().lower()[:2]
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


def _read(lang: str) -> dict[str, Any]:
    path = locales_dir() / f"{lang}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_locale(lang: str | None) -> dict[str, Any]:
    lang = normalize_lang(lang)
    cached = _CACHE.get(lang)
    if cached is not None:
        return cached
    ru = _read(DEFAULT_LANG)
    data = ru if lang == DEFAULT_LANG else _deep_merge(ru, _read(lang))
    _CACHE[lang] = data
    return data


def clear_cache() -> None:
    _CACHE.clear()


def flatten_keys(data: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, val in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            keys |= flatten_keys(val, path)
        else:
            keys.add(path)
    return keys


def t(lang: str | None, key: str, default: str = "") -> str:
    cur: Any = load_locale(lang)
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default or key
        cur = cur[part]
    return str(cur) if not isinstance(cur, dict) else default or key
