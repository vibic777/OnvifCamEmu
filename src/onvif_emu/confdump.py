"""Commented onvif-emulator.conf (YAML) next to the program.

CLI stays English. Comments follow GUI language (default ru).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from onvif_emu.i18n import normalize_lang
from onvif_emu.models import AppConfig
from onvif_emu.paths import executable_dir

CONF_NAME = "onvif-emulator.conf"

# Dotted key -> {ru, en}. Missing keys fall back to ru.
_COMMENTS: dict[str, dict[str, str]] = {
    "header": {
        "ru": "Полный конфиг ONVIF Camera Emulator. YAML. Правьте и перезапустите (или Сохранить в GUI).",
        "en": "Full ONVIF Camera Emulator config (YAML). Edit and restart (or Save in the GUI).",
    },
    "auto": {
        "ru": "Файл рядом с программой подхватывается сам. Не брать его: --no-conf. Другой файл: -c path",
        "en": "A file next to the program is loaded automatically. Skip it: --no-conf. Other file: -c path",
    },
    "lang": {
        "ru": "Язык GUI и этих комментариев: -l ru | -l en (CLI/--help всегда английский)",
        "en": "GUI language and these comments: -l ru | -l en (CLI/--help is always English)",
    },
    "version": {"ru": "Версия схемы конфига", "en": "Config schema version"},
    "host": {"ru": "Адрес bind HTTP/ONVIF/GUI (0.0.0.0 — все интерфейсы)", "en": "Bind address for HTTP/ONVIF/GUI"},
    "public_host": {"ru": "Хост в XAddr и RTSP URI; пусто = автоопределение", "en": "Host in XAddr/RTSP URIs; empty = autodetect"},
    "onvif_port": {"ru": "TCP порт ONVIF + веб-GUI (1..65535)", "en": "TCP port for ONVIF + web GUI (1..65535)"},
    "discovery": {"ru": "WS-Discovery UDP 3702 multicast", "en": "WS-Discovery UDP 3702 multicast"},
    "gui": {"ru": "Веб-интерфейс на том же порту. false = только SOAP/RTSP (как --headless)", "en": "Web UI on the same port. false = SOAP/RTSP only (--headless)"},
    "auto_port": {
        "ru": "Если порт занят/зарезервирован — взять следующий свободный. CLI: --auto-port",
        "en": "If the port is busy or reserved, take the next free one. CLI: --auto-port",
    },
    "snapshot": {"ru": "GetSnapshotUri / JPEG", "en": "GetSnapshotUri / JPEG"},
    "multicast_stream": {"ru": "Отдавать multicast RTSP URI", "en": "Advertise multicast RTSP URIs"},
    "language": {"ru": "Язык GUI: ru (по умолчанию) | en", "en": "GUI language: ru (default) | en"},
    "state_path": {"ru": "JSON живого состояния PTZ/users; пусто = data/state.json рядом", "en": "Live PTZ/users JSON; empty = data/state.json beside the program"},
    "config_dir_choice": {"ru": "где хранить state: beside | appdata", "en": "Where to store state: beside | appdata"},
    "video_dir": {"ru": "Папка роликов (пусто = video/ рядом с программой). CLI: --video-dir", "en": "Video folder (empty = video/ next to the program). CLI: --video-dir"},
    "auth": {"ru": "Учётка ONVIF и RTSP", "en": "ONVIF and RTSP credentials"},
    "auth.mode": {"ru": "digest | basic | wsse | none", "en": "digest | basic | wsse | none"},
    "auth.accounts": {
        "ru": "Пользователи и пароли (зашифрованы) — в data/users.json; сброс admin: удалить его строку password",
        "en": "Accounts and passwords (encrypted) are in data/users.json; reset admin: delete its password line",
    },
    "auth.embed_rtsp_credentials": {
      "ru": "Вшивать user:pass в GetStreamUri (по умолчанию нет)",
      "en": "Embed user:pass in GetStreamUri (default off)",
    },
    "device": {"ru": "GetDeviceInformation / hostname / scopes", "en": "GetDeviceInformation / hostname / scopes"},
    "device.manufacturer": {"ru": "Производитель (ODM)", "en": "Manufacturer (ODM)"},
    "device.model": {"ru": "Модель", "en": "Model"},
    "device.firmware": {"ru": "Прошивка", "en": "Firmware"},
    "device.serial": {"ru": "Серийный номер", "en": "Serial"},
    "device.hardware_id": {"ru": "HardwareId", "en": "HardwareId"},
    "device.hostname": {"ru": "GetHostname", "en": "GetHostname"},
    "device.name": {"ru": "Имя в scopes", "en": "Name in scopes"},
    "device.location": {"ru": "Локация в scopes", "en": "Location in scopes"},
    "device.timezone": {"ru": "IANA TZ (UTC, Europe/Moscow)", "en": "IANA TZ (UTC, Europe/Moscow)"},
    "device.use_ntp": {"ru": "DateTimeType NTP", "en": "DateTimeType NTP"},
    "device.daylight_savings": {"ru": "Флаг DST в GetSystemDateAndTime", "en": "DST flag in GetSystemDateAndTime"},
    "network": {"ru": "Порты протоколов", "en": "Protocol ports"},
    "network.http_port": {"ru": "Дублирует onvif_port", "en": "Mirrors onvif_port"},
    "network.rtsp_port": {"ru": "MediaMTX RTSP", "en": "MediaMTX RTSP"},
    "network.https_port": {"ru": "HTTPS (зарезервирован)", "en": "HTTPS (reserved)"},
    "network.rtsp_enabled": {"ru": "Объявлять RTSP в GetNetworkProtocols", "en": "Advertise RTSP in GetNetworkProtocols"},
    "network.http_enabled": {"ru": "Объявлять HTTP", "en": "Advertise HTTP"},
    "ntp": {"ru": "NTP (GetNTP / SetNTP)", "en": "NTP (GetNTP / SetNTP)"},
    "ntp.enabled": {"ru": "Включить NTP", "en": "Enable NTP"},
    "ntp.servers": {"ru": "Серверы NTP", "en": "NTP servers"},
    "users": {"ru": "Список ONVIF-пользователей (те же для RTSP)", "en": "ONVIF users (same for RTSP)"},
    "channels": {"ru": "Каналы 1..64: профиль ONVIF + путь MediaMTX", "en": "Channels 1..64: ONVIF profile + MediaMTX path"},
    "ptz": {"ru": "Виртуальный PTZ, 20 Гц, без перелёта лимитов", "en": "Virtual PTZ, 20 Hz, no limit overshoot"},
    "ptz.pan_min": {"ru": "Pan min, градусы (−180..180)", "en": "Pan min, degrees (−180..180)"},
    "ptz.pan_max": {"ru": "Pan max", "en": "Pan max"},
    "ptz.tilt_min": {"ru": "Tilt min (−90..90)", "en": "Tilt min (−90..90)"},
    "ptz.tilt_max": {"ru": "Tilt max", "en": "Tilt max"},
    "ptz.zoom_min": {"ru": "Zoom min 0..1", "en": "Zoom min 0..1"},
    "ptz.zoom_max": {"ru": "Zoom max 0..1", "en": "Zoom max 0..1"},
    "ptz.pan_speed": {"ru": "Скорость pan, °/с", "en": "Pan speed, deg/s"},
    "ptz.tilt_speed": {"ru": "Скорость tilt, °/с", "en": "Tilt speed, deg/s"},
    "ptz.zoom_speed": {"ru": "Скорость zoom, 1/с", "en": "Zoom speed, 1/s"},
    "ptz.iris_min": {"ru": "Iris min 0..1", "en": "Iris min 0..1"},
    "ptz.iris_max": {"ru": "Iris max", "en": "Iris max"},
    "ptz.focus_min": {"ru": "Focus min", "en": "Focus min"},
    "ptz.focus_max": {"ru": "Focus max", "en": "Focus max"},
    "ptz.automove": {"ru": "Автодвижение (демо)", "en": "Auto-move (demo)"},
    "ptz.flip": {"ru": "Инверсия осей", "en": "Flip axes"},
    "ptz.tours_enabled": {"ru": "Туры пресетов", "en": "Preset tours"},
    "imaging": {"ru": "ImagingSettings → фильтр FFmpeg eq (яркость/контраст)", "en": "ImagingSettings → FFmpeg eq (brightness/contrast)"},
    "imaging.brightness": {"ru": "0..100, 50 = без коррекции", "en": "0..100, 50 = identity"},
    "imaging.contrast": {"ru": "0..100", "en": "0..100"},
    "imaging.saturation": {"ru": "0..100", "en": "0..100"},
    "imaging.sharpness": {"ru": "0..100", "en": "0..100"},
    "imaging.backlight": {"ru": "Компенсация засветки", "en": "Backlight compensation"},
    "imaging.backlight_level": {"ru": "0..100", "en": "0..100"},
    "imaging.white_balance": {"ru": "auto | manual", "en": "auto | manual"},
    "imaging.wb_cr": {"ru": "WB Cr", "en": "WB Cr"},
    "imaging.wb_cb": {"ru": "WB Cb", "en": "WB Cb"},
    "imaging.exposure_mode": {"ru": "auto | manual", "en": "auto | manual"},
    "imaging.iris": {"ru": "Диафрагма 0..1 (виртуальная)", "en": "Iris 0..1 (virtual)"},
    "imaging.focus_mode": {"ru": "auto | manual", "en": "auto | manual"},
    "imaging.focus": {"ru": "Фокус 0..1", "en": "Focus 0..1"},
    "osd": {"ru": "OSD drawtext поверх потока", "en": "OSD drawtext overlay"},
    "osd.enabled": {"ru": "Рисовать оверлей", "en": "Draw overlay"},
    "osd.preset": {"ru": "Имя стиля", "en": "Style name"},
    "osd.style.color": {"ru": "Цвет #RRGGBB", "en": "Color #RRGGBB"},
    "osd.style.opacity": {"ru": "0..1", "en": "0..1"},
    "osd.style.size": {"ru": "Размер шрифта 8..96", "en": "Font size 8..96"},
    "osd.style.position": {"ru": "top-left | top-right | bottom-left | bottom-right | center", "en": "top-left | top-right | bottom-left | bottom-right | center"},
    "osd.style.event_position": {"ru": "Отдельное место сообщений событий; PTZ HUD не перемещает", "en": "Event message position; does not move the PTZ HUD"},
    "osd.style.font": {"ru": "Имя шрифта FFmpeg", "en": "FFmpeg font name"},
    "osd.style.duration_ms": {"ru": "Сколько держать иконку, мс", "en": "Icon hold time, ms"},
    "osd.style.show_text_status": {"ru": "Текст статуса", "en": "Status text"},
    "osd.style.show_arrows": {"ru": "Стрелки PTZ", "en": "PTZ arrows"},
    "osd.style.show_zoom": {"ru": "Иконка zoom", "en": "Zoom icon"},
    "osd.style.show_iris": {"ru": "Иконка iris", "en": "Iris icon"},
    "osd.style.show_focus": {"ru": "Иконка focus", "en": "Focus icon"},
    "osd.style.show_events": {"ru": "Иконки событий", "en": "Event icons"},
    "events": {"ru": "WS-PullPoint: motion / tamper / analytics", "en": "WS-PullPoint: motion / tamper / analytics"},
    "events.mode": {"ru": "timer | motion | manual", "en": "timer | motion | manual"},
    "events.motion_interval": {"ru": "Интервал MotionAlarm, с", "en": "MotionAlarm interval, s"},
    "events.tamper_interval": {"ru": "Интервал Tamper, с", "en": "Tamper interval, s"},
    "events.analytics_interval": {"ru": "Интервал аналитики, с", "en": "Analytics interval, s"},
    "events.digital_input": {"ru": "DigitalInput топик", "en": "DigitalInput topic"},
    "events.randomize": {"ru": "Джиттер интервалов", "en": "Jitter intervals"},
    "events.hold_s": {"ru": "Длительность State=true, с", "en": "State=true hold, s"},
    "analytics": {"ru": "Симулятор bounding box (бильярд)", "en": "Bounding-box simulator (billiard)"},
    "analytics.enabled": {"ru": "Включить метаданные", "en": "Enable metadata"},
    "analytics.mode": {"ru": "simulator | external", "en": "simulator | external"},
    "analytics.motion_module": {"ru": "Модуль motion", "en": "Motion module"},
    "analytics.line_cross_module": {"ru": "Line crossing", "en": "Line crossing"},
    "analytics.field_detector_module": {"ru": "Field detector", "en": "Field detector"},
    "analytics.object_classes": {"ru": "Классы объектов", "en": "Object classes"},
    "analytics.max_objects": {"ru": "Макс. объектов 0..32", "en": "Max objects 0..32"},
    "analytics.box_speed": {"ru": "Скорость рамки 0..1", "en": "Box speed 0..1"},
    "logging": {"ru": "Журнал (консоль всегда английский)", "en": "Logging (console is always English)"},
    "logging.level": {
        "ru": "Устаревшее одиночное значение; предпочтите levels",
        "en": "Legacy single value; prefer levels",
    },
    "logging.levels": {
        "ru": "Независимый мультивыбор: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL или ALL",
        "en": "Independent multi-select: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL or ALL",
    },
    "logging.file": {
        "ru": "Путь к лог-файлу. Пусто = onvif-emulator.log рядом с программой. CLI: -L / --log-file",
        "en": "Log file path. Empty = onvif-emulator.log next to the program. CLI: -L / --log-file",
    },
    "logging.dir": {
        "ru": "Каталог для лога (если file пуст). CLI: --log-dir",
        "en": "Directory for the log file when file is empty. CLI: --log-dir",
    },
    "logging.console": {
        "ru": "Дублировать выбранные уровни в консоль. CLI: --log-console / --no-log-console",
        "en": "Mirror the selected levels to the console. CLI: --log-console / --no-log-console",
    },
    "logging.max_mb": {
        "ru": "Размер файла лога до ротации, МБ. CLI: --log-max-mb",
        "en": "Log file size before rotation, MB. CLI: --log-max-mb",
    },
    "logging.backups": {
        "ru": "Сколько ротированных логов хранить. CLI: --log-backups",
        "en": "Rotated log files to keep. CLI: --log-backups",
    },
    "mediamtx": {"ru": "Встроенный или внешний MediaMTX", "en": "Embedded or external MediaMTX"},
    "mediamtx.embedded": {"ru": "Запускать mediamtx как subprocess", "en": "Spawn mediamtx as a subprocess"},
    "mediamtx.publish_host": {
      "ru": "Хост для publish FFmpeg (127.0.0.1 или mediamtx в Docker)",
      "en": "Host FFmpeg publishes to (127.0.0.1 or mediamtx in Docker)",
    },
    "mediamtx.binary": {"ru": "Путь к бинарнику; пусто = PATH / vendor", "en": "Binary path; empty = PATH / vendor"},
    "mediamtx.webrtc": {"ru": "WebRTC", "en": "WebRTC"},
    "mediamtx.hls": {"ru": "HLS предпросмотр", "en": "HLS preview"},
    "mediamtx.api": {"ru": "API MediaMTX", "en": "MediaMTX API"},
    "mediamtx.api_port": {"ru": "Порт API", "en": "API port"},
    "mediamtx.hls_port": {"ru": "Порт HLS", "en": "HLS port"},
    "mediamtx.webrtc_port": {"ru": "Порт WebRTC", "en": "WebRTC port"},
    "ffmpeg": {"ru": "Пайплайны и snapshot", "en": "Pipelines and snapshots"},
    "ffmpeg.binary": {"ru": "ffmpeg", "en": "ffmpeg"},
    "ffmpeg.ffprobe": {"ru": "ffprobe", "en": "ffprobe"},
    "ffmpeg.extra_args": {"ru": "Доп. аргументы ffmpeg", "en": "Extra ffmpeg args"},
    "scopes": {"ru": "ONVIF ProbeMatch / GetScopes", "en": "ONVIF ProbeMatch / GetScopes"},
    "ch.id": {"ru": "1..64", "en": "1..64"},
    "ch.name": {"ru": "Имя профиля", "en": "Profile name"},
    "ch.enabled": {"ru": "Канал включён", "en": "Channel enabled"},
    "ch.rtsp_path": {"ru": "Путь MediaMTX (ch1)", "en": "MediaMTX path (ch1)"},
    "ch.source.type": {"ru": "file | rtsp | http | hls | dash | udp | v4l2 | x11grab | testsrc", "en": "file | rtsp | http | hls | dash | udp | v4l2 | x11grab | testsrc"},
    "ch.source.uri": {"ru": "Файл или URL; пусто + testsrc = цветные полосы", "en": "File or URL; empty + testsrc = color bars"},
    "ch.source.loop": {"ru": "Зациклить файл", "en": "Loop file"},
    "ch.source.buffer_ms": {"ru": "Буфер, мс", "en": "Buffer, ms"},
    "ch.source.transcode": {"ru": "false = copy (без OSD); true = encode", "en": "false = copy (no OSD); true = encode"},
    "ch.source.reconnect": {"ru": "Переподключать источник", "en": "Reconnect source"},
    "ch.source.reconnect_initial_s": {"ru": "Стартовая пауза reconnect, с", "en": "Initial reconnect delay, s"},
    "ch.source.reconnect_max_s": {"ru": "Макс. пауза reconnect, с", "en": "Max reconnect delay, s"},
    "ch.encoder.codec": {"ru": "h264 | h265 | mjpeg", "en": "h264 | h265 | mjpeg"},
    "ch.encoder.width": {"ru": "Ширина 160..7680", "en": "Width 160..7680"},
    "ch.encoder.height": {"ru": "Высота 120..4320", "en": "Height 120..4320"},
    "ch.encoder.fps": {"ru": "Кадры/с 1..120", "en": "FPS 1..120"},
    "ch.encoder.bitrate": {"ru": "кбит/с", "en": "kbit/s"},
    "ch.encoder.gop": {"ru": "GOP", "en": "GOP"},
    "ch.encoder.quality": {"ru": "Качество 1..100 (mjpeg)", "en": "Quality 1..100 (mjpeg)"},
    "ch.sub": {"ru": "Второй поток (substream, профиль medium)", "en": "Substream (profile medium)"},
    "ch.third": {"ru": "Третий поток (профиль low), по умолчанию выключен", "en": "Third stream (profile low), off by default"},
    "ch.ptz_enabled": {"ru": "false = у канала нет PTZ", "en": "false = this channel has no PTZ"},
    "ch.rtsp_port": {"ru": "Свой RTSP-порт канала (отдельный MediaMTX)", "en": "Own RTSP port of this channel (own MediaMTX)"},
    "ch.ptz": {"ru": "Свои пределы/скорости PTZ этого канала", "en": "PTZ limits/speeds of this channel only"},
    "limits": {
        "ru": "Защита ресурсов: лимиты устройств/каналов/потоков/событий и авто-сброс нагрузки",
        "en": "Resource protection: limits for devices/channels/streams/events and automatic load shedding",
    },
    "diag": {
        "ru": "Диагностика: реле, сухие контакты, правила событий (таймеры, ручной запуск), веб-хуки",
        "en": "Diagnostics: relays, dry contacts, event rules (timers, manual firing), webhooks",
    },
    "fleet": {
        "ru": "Каскад: сколько устройств, диапазоны портов ONVIF/RTSP, переопределения по устройствам",
        "en": "Fleet: how many devices, ONVIF/RTSP port ranges, per-device overrides",
    },
}


def beside_conf_path() -> Path:
    return executable_dir() / CONF_NAME


def comment(lang: str, key: str) -> str:
    lang = normalize_lang(lang)
    row = _COMMENTS.get(key) or {}
    return row.get(lang) or row.get("ru") or ""


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if value == "" or any(ch in value for ch in ":#{}[]&*?|>!%@`'\"\t\n"):
            return json.dumps(value, ensure_ascii=False)
        return value
    return json.dumps(value, ensure_ascii=False)


def _line(lines: list[str], indent: int, key: str, value: Any, lang: str, ckey: str) -> None:
    pad = "  " * indent
    note = comment(lang, ckey)
    if isinstance(value, list):
        if note:
            lines.append(f"{pad}# {note}")
        if not value:
            lines.append(f"{pad}{key}: []")
            return
        lines.append(f"{pad}{key}:")
        for item in value:
            if isinstance(item, dict):
                continue
            lines.append(f"{pad}  - {_yaml_scalar(item)}")
        return
    suffix = f"  # {note}" if note else ""
    lines.append(f"{pad}{key}: {_yaml_scalar(value)}{suffix}")


def _section(lines: list[str], lang: str, key: str, title_key: str) -> None:
    note = comment(lang, title_key)
    lines.append("")
    if note:
        lines.append(f"# {note}")
    lines.append(f"{key}:")


def render_conf(cfg: AppConfig, lang: str | None = None) -> str:
    lang = normalize_lang(lang or cfg.language)
    data = json.loads(cfg.model_dump_json())
    lines: list[str] = [
        f"# {comment(lang, 'header')}",
        f"# {comment(lang, 'auto')}",
        f"# {comment(lang, 'lang')}",
        "",
    ]
    for key in (
        "version",
        "host",
        "public_host",
        "onvif_port",
        "auto_port",
        "discovery",
        "gui",
        "snapshot",
        "multicast_stream",
        "language",
        "state_path",
        "config_dir_choice",
        "video_dir",
    ):
        _line(lines, 0, key, data[key], lang, key)

    _section(lines, lang, "auth", "auth")
    for key in ("mode", "embed_rtsp_credentials"):
        _line(lines, 1, key, data["auth"][key], lang, f"auth.{key}")
    lines.append(f"  # {comment(lang, 'auth.accounts')}")

    _section(lines, lang, "device", "device")
    for key in (
        "manufacturer",
        "model",
        "firmware",
        "serial",
        "hardware_id",
        "hostname",
        "name",
        "location",
        "timezone",
        "use_ntp",
        "daylight_savings",
    ):
        _line(lines, 1, key, data["device"][key], lang, f"device.{key}")

    _section(lines, lang, "network", "network")
    for key in ("http_port", "rtsp_port", "https_port", "rtsp_enabled", "http_enabled"):
        _line(lines, 1, key, data["network"][key], lang, f"network.{key}")

    _section(lines, lang, "ntp", "ntp")
    _line(lines, 1, "enabled", data["ntp"]["enabled"], lang, "ntp.enabled")
    _line(lines, 1, "servers", data["ntp"]["servers"], lang, "ntp.servers")

    _section(lines, lang, "channels", "channels")
    for ch in data["channels"]:
        lines.append(f"  - id: {ch['id']}  # {comment(lang, 'ch.id')}")
        _line(lines, 2, "name", ch["name"], lang, "ch.name")
        _line(lines, 2, "enabled", ch["enabled"], lang, "ch.enabled")
        _line(lines, 2, "rtsp_path", ch["rtsp_path"], lang, "ch.rtsp_path")
        if ch.get("rtsp_port"):
            _line(lines, 2, "rtsp_port", ch["rtsp_port"], lang, "ch.rtsp_port")
        _line(lines, 2, "ptz_enabled", ch.get("ptz_enabled", True), lang, "ch.ptz_enabled")
        lines.append("    source:")
        src = ch["source"]
        for key in (
            "type",
            "uri",
            "loop",
            "buffer_ms",
            "transcode",
            "reconnect",
            "reconnect_initial_s",
            "reconnect_max_s",
        ):
            _line(lines, 3, key, src[key], lang, f"ch.source.{key}")
        lines.append("    encoder:")
        enc = ch["encoder"]
        for key in ("codec", "width", "height", "fps", "bitrate", "gop", "quality"):
            _line(lines, 3, key, enc[key], lang, f"ch.encoder.{key}")
        lines.append(f"    sub:  # {comment(lang, 'ch.sub')}")
        sub = ch["sub"]
        for key in ("enabled", "codec", "width", "height", "fps", "bitrate", "gop"):
            _line(lines, 3, key, sub[key], lang, f"ch.encoder.{key}" if key != "enabled" else "ch.enabled")
        third = ch.get("third")
        if third:
            lines.append(f"    third:  # {comment(lang, 'ch.third')}")
            for key in ("enabled", "codec", "width", "height", "fps", "bitrate", "gop"):
                _line(lines, 3, key, third[key], lang, f"ch.encoder.{key}" if key != "enabled" else "ch.enabled")
        if ch.get("ptz"):
            lines.append(f"    ptz:  # {comment(lang, 'ch.ptz')}")
            for key, val in ch["ptz"].items():
                _line(lines, 3, key, val, lang, f"ptz.{key}")

    _section(lines, lang, "ptz", "ptz")
    for key in (
        "pan_min",
        "pan_max",
        "tilt_min",
        "tilt_max",
        "zoom_min",
        "zoom_max",
        "pan_speed",
        "tilt_speed",
        "zoom_speed",
        "iris_min",
        "iris_max",
        "focus_min",
        "focus_max",
        "automove",
        "flip",
        "tours_enabled",
    ):
        _line(lines, 1, key, data["ptz"][key], lang, f"ptz.{key}")

    _section(lines, lang, "imaging", "imaging")
    for key in (
        "brightness",
        "contrast",
        "saturation",
        "sharpness",
        "backlight",
        "backlight_level",
        "white_balance",
        "wb_cr",
        "wb_cb",
        "exposure_mode",
        "iris",
        "focus_mode",
        "focus",
    ):
        _line(lines, 1, key, data["imaging"][key], lang, f"imaging.{key}")

    _section(lines, lang, "osd", "osd")
    _line(lines, 1, "enabled", data["osd"]["enabled"], lang, "osd.enabled")
    _line(lines, 1, "preset", data["osd"]["preset"], lang, "osd.preset")
    lines.append("  style:")
    st = data["osd"]["style"]
    for key in (
        "color",
        "opacity",
        "size",
        "position",
        "event_position",
        "font",
        "duration_ms",
        "show_text_status",
        "show_arrows",
        "show_zoom",
        "show_iris",
        "show_focus",
        "show_events",
        "show_io",
        "boxes",
    ):
        _line(lines, 2, key, st[key], lang, f"osd.style.{key}")

    _section(lines, lang, "events", "events")
    for key in (
        "mode",
        "motion_interval",
        "tamper_interval",
        "analytics_interval",
        "digital_input",
        "randomize",
        "hold_s",
    ):
        _line(lines, 1, key, data["events"][key], lang, f"events.{key}")

    _section(lines, lang, "analytics", "analytics")
    for key in (
        "enabled",
        "mode",
        "motion_module",
        "line_cross_module",
        "field_detector_module",
        "object_classes",
        "max_objects",
        "box_speed",
    ):
        _line(lines, 1, key, data["analytics"][key], lang, f"analytics.{key}")

    _section(lines, lang, "logging", "logging")
    _line(lines, 1, "levels", data["logging"]["levels"], lang, "logging.levels")
    _line(lines, 1, "level", data["logging"]["level"], lang, "logging.level")
    _line(lines, 1, "file", data["logging"]["file"], lang, "logging.file")
    _line(lines, 1, "dir", data["logging"]["dir"], lang, "logging.dir")
    _line(lines, 1, "console", data["logging"]["console"], lang, "logging.console")
    _line(lines, 1, "max_mb", data["logging"]["max_mb"], lang, "logging.max_mb")
    _line(lines, 1, "backups", data["logging"]["backups"], lang, "logging.backups")

    _section(lines, lang, "mediamtx", "mediamtx")
    for key in ("embedded", "publish_host", "binary", "webrtc", "hls", "api", "api_port", "hls_port", "webrtc_port"):
        _line(lines, 1, key, data["mediamtx"][key], lang, f"mediamtx.{key}")

    _section(lines, lang, "ffmpeg", "ffmpeg")
    _line(lines, 1, "binary", data["ffmpeg"]["binary"], lang, "ffmpeg.binary")
    _line(lines, 1, "ffprobe", data["ffmpeg"]["ffprobe"], lang, "ffmpeg.ffprobe")
    _line(lines, 1, "extra_args", data["ffmpeg"]["extra_args"], lang, "ffmpeg.extra_args")

    _line(lines, 0, "scopes", data["scopes"], lang, "scopes")
    for name in ("limits", "diag", "fleet"):
        lines.append("")
        lines.append(f"# {comment(lang, name)}")
        lines.append(yaml.safe_dump({name: data[name]}, sort_keys=False, allow_unicode=True).rstrip())
    lines.append("")
    return "\n".join(lines)


def write_conf(cfg: AppConfig, path: Path | str | None = None, lang: str | None = None) -> Path:
    dest = Path(path) if path else beside_conf_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = render_conf(cfg, lang)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return dest
