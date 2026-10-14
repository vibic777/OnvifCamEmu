# Конфигурация YAML

Файл по умолчанию: `config/default.yaml` и встроенная копия `src/onvif_emu/data/default.yaml`.

Типы проверяет Pydantic: выход с кодом 1, если значение вне диапазона.

| Путь | Тип | Default | Диапазон | Описание |
|---|---|---|---|---|
| `host` | str | `0.0.0.0` | IP | Bind HTTP |
| `public_host` | str | `""` (авто) | hostname | XAddr и RTSP host |
| `onvif_port` | int | 8080 | 1..65535 | HTTP ONVIF+GUI. Меняется на лету из GUI (Система) |
| `auto_port` | bool | false | | занят порт → следующий свободный. CLI `--auto-port` |
| `discovery` | bool | true | | WS-Discovery |
| `gui` | bool | true | | Веб-интерфейс |
| `snapshot` | bool | true | | GetSnapshotUri |
| `multicast_stream` | bool | false | | RTP multicast URI |
| `language` | ru/en | ru | | GUI |
| `state_path` | str | `""` | путь | JSON PTZ/users |
| `config_dir_choice` | beside/appdata | beside | | где хранить state |
| `video_dir` | str | `""` | путь | папка роликов; пусто = `video/` рядом с программой. CLI `--video-dir`, GUI Система |
| `auth.mode` | enum | digest | digest,basic,wsse,none | |
| `auth.embed_rtsp_credentials` | bool | false | | вшивать `user:pass@` в GetStreamUri (legacy VMS) |
| `device.manufacturer` | str | Vibic | | GetDeviceInformation |
| `device.model` | str | EMU-CAM-1 | | |
| `device.firmware` | str | 1.0.0 | | |
| `device.serial` | str | EMU00001 | | |
| `device.hardware_id` | str | HW-EMU-1 | | |
| `device.hostname` | str | onvif-emu | | GetHostname |
| `device.name` | str | ONVIF Emulator | | scope name |
| `device.location` | str | Lab | | scope location |
| `device.timezone` | str | UTC | IANA | GetSystemDateAndTime |
| `device.use_ntp` | bool | false | | DateTimeType NTP/Manual |
| `device.daylight_savings` | bool | false | | DST flag |
| `network.http_port` | int | = onvif_port | | GetNetworkProtocols |
| `network.rtsp_port` | int | 8554 | 1..65535 | |
| `ntp.enabled` | bool | false | | |
| `ntp.servers` | list[str] | pool.ntp.org | | |
| *(учётные записи)* | | | | не в конфиге: `data/users.json`, пароли зашифрованы — [USERS.md](USERS.md). Старые `auth.username`/`auth.password`/`users` читаются один раз, переносятся туда и удаляются из файла |
| `channels[].id` | int | | 1..64 | |
| `channels[].name` | str | | | |
| `channels[].enabled` | bool | true | | без рестарта процесса (watchdog) |
| `channels[].rtsp_path` | str | `ch{id}` | | MediaMTX path |
| `channels[].rtsp_port` | int | пусто = `network.rtsp_port` | 1..65535 | свой RTSP-порт канала (отдельный MediaMTX) |
| `channels[].ptz_enabled` | bool | true | | false = у канала нет PTZ (нет узла/конфигурации, `NoPTZProfile`) |
| `channels[].ptz` | блок | пусто | как `ptz.*` | свои пределы/скорости PTZ канала |
| `channels[].streams` | 1..3 | — | | **только на входе**: строит `sub` (medium) и `third` (low) из основного кодировщика |
| `channels[].third.*` | | выкл, 320x180@10 256k | | третий поток (профиль low), пути `ch{id}_third` |
| `channels[].source.type` | enum | testsrc | file,rtsp,http,hls,dash,udp,v4l2,x11grab,testsrc | |
| `channels[].source.uri` | str | | | путь/URL |
| `channels[].source.loop` | bool | true | | файлы: крутить по кругу |
| `channels[].source.end_action` | enum | stop | stop, freeze, black | без цикла: остановить поток / застыть / чёрный экран |
| `channels[].source.buffer_ms` | int | 500 | 0..30000 | |
| `channels[].source.transcode` | bool | false | | принудительно перекодировать; для файлов без этого флага кодек проверяется ffprobe: H.264/H.265, совпадающий с профилем, идёт без перекодирования, остальное перекодируется |
| `channels[].source.reconnect*` | float | 1 / 30 | | backoff |
| `channels[].encoder.codec` | enum | h264 | h264,h265,mjpeg | |
| `channels[].encoder.width/height` | int | 1920/1080 | 160..7680 / 120..4320 | |
| `channels[].encoder.fps` | int | 25 | 1..120 | |
| `channels[].encoder.bitrate` | int | 4000 | 64..100000 кбит | |
| `channels[].encoder.gop` | int | 50 | 1..300 | |
| `channels[].sub.*` | | 640x360@15 512k | | второй профиль |
| `ptz.pan_min/max` | float | -180/180 | -180..180 | градусы |
| `ptz.tilt_min/max` | float | -90/90 | -90..90 | |
| `ptz.zoom_min/max` | float | 0/1 | 0..1 | |
| `ptz.pan_speed` | float | 30 | 0.1..180 °/s | |
| `ptz.tilt_speed` | float | 20 | 0.1..90 | |
| `ptz.zoom_speed` | float | 0.25 | 0.01..1 1/s | |
| `imaging.brightness` | int | 50 | 0..100 | 50 = без коррекции |
| `imaging.contrast` | int | 50 | 0..100 | |
| `imaging.saturation` | int | 50 | 0..100 | |
| `imaging.sharpness` | int | 50 | 0..100 | |
| `imaging.iris` / `focus` | float | 0.5 | 0..1 | виртуальные оси |
| `osd.enabled` | bool | true | | |
| `osd.style.color` | str | `#00FF88` | #RGB | |
| `osd.style.opacity` | float | 0.85 | 0..1 | |
| `osd.style.size` | int | 28 | 8..96 | |
| `osd.style.position` | enum | top-left | top/bottom + left/right, center | |
| `osd.style.event_position` | enum | top-right | 9 screen positions | отдельное положение сообщений тревог, аналитики, реле и входов; PTZ HUD не перемещает |
| `osd.style.duration_ms` | int | 1500 | 100..30000 | сколько подсвечивается команда PTZ |
| `osd.style.show_io` | bool | true | | светодиоды реле / сухих контактов |
| `osd.style.boxes` | enum | injected | injected, all, off | рамки объектов аналитики |
| `osd.time.*`, `osd.date.*` | | | | штампы: `enabled`, `format` (strftime; дата `%d.%m.%Y`, время `%H:%M:%S`), `position` (top/middle/bottom-left/center/right, center, custom), `x`, `y` (%, для custom), `size` (px при 1080p), `font` (sans, mono), `color`, `opacity`, `bg_color`, `bg_opacity` (0 = прозрачный), `outline` |
| `osd.image.*` | | | | картинка: `enabled`, `path`, `position`, `x`, `y`, `size` (% ширины), `opacity`, `transparent_color`, `key_similarity`, `bg_color`, `bg_opacity` |
| `channels[].impair.*` | | | | помехи, см. [OSD_IMPAIR.md](OSD_IMPAIR.md): `enabled`, `rain`, `noise`, `pixelate`, `shake`, `shake_burst`, `cover`, `cover_burst`, `tamper_events`, `loss`, `stutter`, `freeze`, `freeze_burst`, `bitrate`, `bitrate_burst`, `bitrate_cap`, `outage`, `outage_unit`, `outage_every_min/max`, `outage_len_min/max`; `*_burst` = `{mode: bursts\|constant, period_s, duration_s}` |
| `events.mode` | enum | timer | timer,motion,manual | |
| `events.motion_interval` | float | 15 | 1..3600 с | |
| `events.tamper_interval` | float | 60 | 1..3600 | |
| `events.hold_s` | float | 3 | 0.2..60 | длительность State=true |
| `analytics.max_objects` | int | 4 | 0..32 | |
| `analytics.box_speed` | float | 0.08 | 0..1 | |
| `diag.relays` / `diag.inputs` | int | 2 / 2 | 0..32 | число реле и сухих контактов |
| `diag.rules[]` | список | пусто | | правила событий, см. [DIAGNOSTICS.md](DIAGNOSTICS.md) |
| `diag.webhooks` | list[str] | пусто | | URL, куда POST-ится каждое событие |
| `diag.notify` | bool | true | | WS-Notify подписчикам `Subscribe` |
| `diag.log_size` | int | 300 | 10..100000 | размер живого журнала событий |
| `fleet.count` | int | 1 | 1..256 | число устройств |
| `fleet.onvif_ports` / `fleet.rtsp_ports` | str | пусто | `8080-8089`, `554,600` | диапазоны портов устройств |
| `fleet.devices[]` | список | пусто | | переопределения устройств 2, 3, … (любые ключи; `channels_patch: {"1": {…}}`), см. [FLEET.md](FLEET.md) |
| `limits.*` | блок | | | защита ресурсов, см. [LIMITS.md](LIMITS.md) |
| `limits.max_upload_mb` | int | 2048 | 1..65536 | максимальный размер одного видеофайла, загружаемого через веб-интерфейс; потоковая загрузка, частичный файл удаляется при превышении |
| `logging.levels` | list[str] | [CRITICAL] | независимый мультивыбор TRACE…CRITICAL / ALL; CLI `--log-level TRACE,INFO,ERROR` / `-v`=ALL / `-q` | |
| `logging.level` | str | CRITICAL | устаревшее зеркало первого из `levels` | |
| `logging.file` | str | пусто → `onvif-emulator.log` рядом с программой (если нельзя писать — `%APPDATA%\onvif-emulator`) | путь; CLI `-L` | |
| `logging.dir` | str | пусто | каталог для лога, если `file` пуст; CLI `--log-dir` | |
| `logging.console` | bool | true | дублировать уровни в stderr; CLI `--log-console` | |
| `logging.max_mb` | float | 5 | 0.1..1024, размер до ротации; CLI `--log-max-mb` | |
| `logging.backups` | int | 3 | 0..100, ротированных файлов; CLI `--log-backups` | |
| `mediamtx.embedded` | bool | true | | subprocess |
| `mediamtx.publish_host` | str | 127.0.0.1 | | хост для FFmpeg publish (`mediamtx` в Docker Compose) |
| `mediamtx.*_port` | int | 9997/8888/8889 | | |
| `ffmpeg.binary` | str | ffmpeg | | |
| `scopes` | list[str] | Profile/Streaming, hardware, name, location, ptz | | GetScopes |

Экспорт/импорт в GUI: Ctrl+E / Ctrl+I (JSON/YAML).

Любой путь из таблицы задаётся при запуске: `--set PATH=VALUE` (например `--set events.hold_s=5`); список — `--list-settings`, итог — `--print-config`.
