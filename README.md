# ONVIF Camera Emulator
[Русский](#русский) · [English](#english)

**GitHub About / English:** An ONVIF camera emulator for checking recorders and debugging metadata integration. Built from years of security-systems practice, because a complete diagnostic tool was not there.
**Описание GitHub / Русский:** Эмулятор ONVIF-камеры для проверки видеорегистраторов и отладки интеграции метаданных. Сделан из практики систем безопасности: полноценного инструмента диагностики не нашлось.

Эмулятор IP-камеры **ONVIF Profile S** (части T/G): SOAP, WS-Discovery, PTZ, OSD, события, аналитика, рестример. Целевой клиент — [ONVIF Device Manager](https://sourceforge.net/projects/onvifdm/).

Author: **vibic777** `<vibic@yandex.ru>` · version **1.8.2** · license [MIT](LICENSE)

---

<a id="русский"></a>

## Русский

### Откуда

Семнадцать лет как интегратор комплексов охраны и ещё два с половиной — в разработке интеграционной системы. Это лишь часть той практики, но как раз та, которой не хватало инструмента: проверить видеорегистратор и отладить интеграцию метаданных было не на чем. Полноценного эмулятора ONVIF не нашлось, поэтому появился свой. Инструмент диагностики и интеграции, оставленный открытым — для тех, кому он нужен в работе.

### Что это

Один процесс = ONVIF + веб-GUI + (опционально) RTSP через MediaMTX. CLI всегда на английском; GUI по умолчанию русский, переключатель EN в шапке.

| | |
|---|---|
| ONVIF | Device, Media, PTZ, Imaging, Events, Analytics |
| Обнаружение | WS-Discovery `239.255.255.250:3702` |
| Аутентификация | Digest, Basic, WS-Security UsernameToken, none |
| Источники | файл (цикл), RTSP, HTTP/HLS/DASH, UDP, testsrc, v4l2, x11grab |
| Поток | MediaMTX `rtsp://host:8554/chN` |
| GUI | `http://IP:8080/` (логин **admin** / **admin**) |
| Сборка | один файл: Windows `.exe`, Linux ELF (внутри FFmpeg + MediaMTX) |

**`--headless` выключает веб-интерфейс.** Чтобы настраивать в браузере, запускайте без него (или с `--service`).

### Быстрый старт

Нужны Python **3.11+**. FFmpeg и MediaMTX — для живого видео; без них SOAP, Discovery, GUI и `/health` всё равно работают.

```powershell
# Windows
.\scripts\venv.ps1 -Recreate
.\.venv\Scripts\Activate.ps1
onvif-emulator --service --open-browser
```

```bash
# Linux
./scripts/venv.sh --recreate
source .venv/bin/activate
./scripts/run.sh --service --open-browser
```

Окно **не закрывайте** — это сервер. GUI: `http://127.0.0.1:8080/`. В консоли строка `Log file: ...` — путь к журналу.

| Порт | Назначение |
|---|---|
| TCP 8080 | ONVIF + веб-GUI |
| TCP 8554 | RTSP (MediaMTX) |
| UDP 3702 | WS-Discovery |

ODM: Discover → **EMU-CAM-1**, либо вручную `http://IP:8080/onvif/device_service`.

Docker: скопируйте `.env.docker.example` в `.env.docker`, задайте IP этой машины и новый пароль, затем выполните `docker compose --env-file .env.docker up --build -d`. FFmpeg, MediaMTX и эмулятор запускаются в одном контейнере. Ролики сохраняются в `./video`, состояние и пользователи — в `./data`; [настройка Docker и Linux-сборки](docs/INSTALL.md).

### Запуск как служба (GUI остаётся)

```powershell
# Windows (администратор)
.\scripts\install-service.ps1
```

```bash
# Linux
sudo ./scripts/install.sh --systemd
```

Не добавляйте `--headless`, если нужна настройка в браузере.

### Свой ролик вместо тестовой картинки

Каналы → «Источник видео», два варианта:

1. **Загрузить из браузера** — файл копируется в `video/` рядом с программой (папка создаётся сама; путь: `--video-dir` или
   GUI → Система → «Папка видео»), канал сразу переключается на него. Виден прогресс; одноимённый файл не перезаписывается.
2. **Файл на компьютере эмулятора** — указать полный путь (`D:\Видео\камера.mp4`) или выбрать через «Обзор»: без копирования.
   Из консоли: `--channel 1:source=D:\Видео\камера.mp4`.

Форматы — всё, что читает FFmpeg: MP4, M4V, MOV, MKV, WebM, AVI, WMV, FLV, MPEG/MPG, VOB, TS/M2TS/MTS, 3GP, OGV, MXF и др.
H.264 (для профиля H.265 — H.265) идёт без перекодирования, остальное (MPEG-2, Xvid/DivX, WMV, VP9, ProRes…) автоматически
перекодируется в H.264. Ролик крутится по кругу.

### Портативный exe / ELF

```powershell
pip install -e ".[packaging]"
.\packaging\build.ps1 win          # dist\onvif-emulator-v1.8.2-win64.exe (+ *-gui.exe)
```

```bash
./packaging/build.sh linux         # dist/onvif-emulator-v1.8.2-linux-x86_64
```

ELF собирается **на Linux** (или CI). На Windows `python packaging/build.py linux` даёт tar.gz-билдер, не готовый ELF.

Два exe, **одинаковые ключи запуска**:

| Файл | Назначение |
|---|---|
| `onvif-emulator-v1.8.2-win64.exe` | консольная (web) сборка: вывод и журнал в окне, запуск из cmd/PowerShell/службы |
| `onvif-emulator-v1.8.2-win64-gui.exe` | оконная сборка: двойной клик → сервер + браузер; при занятом порту берёт свободный; ошибки и `--help` показывает окном |

Обе сборки — со значком камеры; у оконной по умолчанию есть иконка в трее (правый клик → открыть GUI / выход), у неё же и только у неё
пропадает окно и запись в панели задач, поэтому без трея кроме диспетчера задач её было не найти. Отключается ключом `--no-tray`.

### Примеры CLI

```bash
onvif-emulator --service --open-browser
onvif-emulator --video-dir D:\clips --service
onvif-emulator --channel 1:source=./clip.mp4,encoder=h264 --auth none
onvif-emulator --channel 1:source=rtsp://192.168.1.20:554/stream1,transcode=false
onvif-emulator --channels 8 --auth digest --password N3wPass --add-user "operator1:secret:Operator:1,2"
onvif-emulator --headless --auth none --no-discovery   # без GUI
onvif-emulator --service --onvif-port 18080 --rtsp-port 18554 --auto-port
onvif-emulator --log-level INFO,ERROR,CRITICAL -L D:\logs\emu.log --log-max-mb 20 --log-backups 5
onvif-emulator --set ptz.pan_speed=45 --print-config    # любая настройка по пути; итог без запуска
onvif-emulator doctor
onvif-emulator cheat-sheet launch
```

Всё, что настраивается в веб-GUI, есть и ключом запуска (полный список — [docs/CLI.md](docs/CLI.md), `--help`, `--list-settings`); что не вынесено отдельным ключом, задаётся `--set PATH=VALUE`. Порт сервиса можно сменить и в работающем GUI (Система → порт) — без перезапуска.

Приоритет: флаги CLI (`--set` последним) → `-c файл` → `onvif-emulator.conf` рядом с программой → встроенный YAML → `ONVIF_EMU_*`. Первый запуск пишет `.conf`. Пропуск: `--no-conf`.

### Диагностический стенд (версия 1.8)

Эмулятор заменяет **любое ONVIF-устройство**, а не только «камеру с картинкой»:

* **Многоканальность:** у каждого канала своё видео, свои события, PTZ (есть/нет, свои пределы), свой RTSP-порт; до трёх профилей **full / medium / low** на канал (`--streams 3`, качество выстраивается автоматически).
* **Каскад:** N устройств в одном процессе с диапазонами портов — `--devices 10 --onvif-port 8080-8089 --rtsp-port 554-563`, у каждого свои серийник, MAC, Discovery, настройки.
* **События:** любые, вручную (кнопка/REST) и по таймеру — постоянному или **случайному в диапазоне** (секунды/минуты/часы). Типы: движение, тампер, аналитика, **реле**, **сухой контакт**, метаданные, свои топики. Добавляются и удаляются в GUI и консоли (`--rule`).
* **Виды аналитики:** пересечение линии, вторжение в зону, движение в ячейках/области, **подсчёт объектов** (счётчик `Count`), праздношатание — выбираются списком, на одну камеру сколько угодно правил, каждое со своим таймером. Только события/метаданные, без анализа видео.
* **Пуш:** PullPoint, WS-Notify (`Subscribe`), webhook, поток метаданных, живой журнал.
* **Оверлей на видео:** PTZ HUD и события настраиваются независимо; сообщения тревог, аналитики, метаданных, реле и сухих контактов выводятся в выбранном месте и на языке приложения, плюс рамки объектов и индикация каналов ввода/вывода.
* **PTZ-препозиции в GUI:** сохранить текущую позицию с именем, перейти, удалить — плюс Iris/Focus ± и «домой» на той же вкладке.
* **Конструктор из консоли и готовая конфигурация:** `onvif-emulator.conf` рядом с программой подхватывается сам, экспорт/импорт целиком (`--export-config`, GUI).
* **Пользователи и роли:** Администратор / Оператор (просмотр + PTZ) / Наблюдатель, ограничение каналами — одинаково для
  веб-интерфейса, ONVIF и RTSP; пароли зашифрованы в `data/users.json`; страница входа и сеанс без повторных запросов пароля;
  раздел «Просмотр» с живым видео через эмулятор — [docs/USERS.md](docs/USERS.md).
* **Интерфейс:** новый дизайн, полностью на русском и английском (переключатель языка, справка и шпаргалки тоже).
* **OSD на видео:** штампы времени и даты (формат, положение, размер, цвет, фон с прозрачностью) и картинка-логотип
  (загрузить или путь на компьютере эмулятора; положение, размер, прозрачный цвет, подложка) — [docs/OSD_IMPAIR.md](docs/OSD_IMPAIR.md).
* **Помехи:** дождь, шум, пикселизация, тряска и закрытие объектива (с событием Tampering), потеря пакетов, рывки, замирания,
  CBR/скачки VBR, узкий канал, обрывы связи по случайному расписанию — на каждый канал, пресеты, `--impair`.
* **Ролик без цикла:** по окончании поток останавливается, застывает на последнем кадре или показывает чёрный экран.
* **Раздел «Подключение»** (открывается первым): адрес ONVIF, режим авторизации, RTSP-адрес и ONVIF-токен каждого канала и профиля, снимки, превью, пошагово для ODM и VLC. То же без JavaScript — `/info`; эту страницу получает и встроенный браузер ODM («Web page»), которому современный GUI не по силам.
* **Защита ресурсов:** оценка нагрузки, лимиты, режимы `off|warn|clamp|strict`, автосброс потоков при перегрузе — компьютер не «зависнет» от неудачных настроек.

```bash
# 3 устройства × 3 профиля, тампер у всех каждые 30 с, сухой контакт случайно 5…200 с, аналитика только у второго
onvif-emulator --service --devices 3 --onvif-port 8080-8082 --rtsp-port 554-556 --streams 3 \
  --rule "kind=tamper,every=30s" --rule "kind=contact,random=5-200s" --rule "kind=analytics,topic=line,device=2,every=1m"
```

Проверка — ONVIF Device Manager 2.2.250; протокол дополнительно проверяется строгим клиентом (порядок XML-элементов по схеме ONVIF).
Подробно: [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md), [docs/FLEET.md](docs/FLEET.md), [docs/LIMITS.md](docs/LIMITS.md).

### Документация

| Файл | Содержание |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Windows / Linux / Docker, FFmpeg, MediaMTX, службы |
| [docs/CLI.md](docs/CLI.md) | Ключи, коды выхода, примеры |
| [docs/CONFIG.md](docs/CONFIG.md) | Поля YAML |
| [docs/FAQ.md](docs/FAQ.md) | Частые вопросы |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Discovery, поток, логи, MediaMTX |
| [docs/ONVIF_MATRIX.md](docs/ONVIF_MATRIX.md) | Матрица ONVIF-операций |
| [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md) | События, таймеры, реле, метаданные, оверлей, REST |
| [docs/OSD_IMPAIR.md](docs/OSD_IMPAIR.md) | OSD (время, дата, картинка), ролик без цикла, помехи |
| [docs/USERS.md](docs/USERS.md) | Пользователи, роли, вход, хранение и сброс паролей |
| [docs/FLEET.md](docs/FLEET.md) | Каскад устройств, конструктор, готовая конфигурация |
| [docs/LIMITS.md](docs/LIMITS.md) | Защита ресурсов |
| [docs/ODM_TESTING.md](docs/ODM_TESTING.md) | Чек-лист ODM |
| [MATRIX_ODM.md](MATRIX_ODM.md) | Краткая приёмка |

В приложении: `--help`, `doctor`, `cheat-sheet`, в GUI клавиша **F1**.

### Тесты и очистка

```bash
python -m pytest
.\scripts\clean.ps1          # Windows; Linux: ./scripts/clean.sh
```

`.venv` в git не входит. Снос вместе с venv: `.\scripts\clean.ps1 -All`.

### Важно для лаборатории

Учётка по умолчанию **admin / admin** — только для стенда: интерфейс напоминает сменить пароль. Пароли хранятся
зашифрованными в `data/users.json`; сброс admin — удалить его строку `password` (или `--reset-admin`), см. [docs/USERS.md](docs/USERS.md). Не коммитьте `.env`, `onvif-emulator.conf` и ролики из `video/`.

---

<a id="english"></a>

## English

Seventeen years integrating physical-security systems, and another two and a half building an integration platform. This is one piece of that work, and a necessary one: there was no complete ONVIF emulator for checking recorders and debugging metadata. So this tool exists. It is a diagnostic and integration instrument, published for anyone who needs it on the job.

ONVIF **Profile S** IP-camera emulator (with T/G bits): SOAP services, WS-Discovery, live PTZ, OSD, events, analytics, restreamer and channel multiplier. Target client: [ONVIF Device Manager](https://sourceforge.net/projects/onvifdm/).

One process = ONVIF + web GUI + optional RTSP via MediaMTX. The CLI is always English. The GUI defaults to Russian, with an EN switcher in the header.

| | |
|---|---|
| ONVIF | Device, Media, PTZ, Imaging, Events, Analytics |
| Discovery | WS-Discovery `239.255.255.250:3702` |
| Auth | HTTP Digest, Basic, WS-Security UsernameToken, or none |
| Sources | file (loop), RTSP, HTTP/HLS/DASH, UDP, testsrc, v4l2, x11grab |
| Streaming | MediaMTX `rtsp://host:8554/chN` |
| GUI | `http://IP:8080/` (login **admin** / **admin**) |
| Packaging | one-file Windows `.exe` and Linux ELF (FFmpeg + MediaMTX inside) |

**`--headless` disables the web UI.** To configure in the browser, run without it (or with `--service`).

### Quick start

Python **3.11+**. FFmpeg and MediaMTX are needed for live video; SOAP, Discovery, the GUI and `/health` still start without them.

```powershell
# Windows
.\scripts\venv.ps1 -Recreate
.\.venv\Scripts\Activate.ps1
onvif-emulator --service --open-browser
```

```bash
# Linux
./scripts/venv.sh --recreate
source .venv/bin/activate
./scripts/run.sh --service --open-browser
```

Leave the process running. GUI: `http://127.0.0.1:8080/`. The console prints `Log file: ...` — that is the journal path.

| Port | Role |
|---|---|
| TCP 8080 | ONVIF + web GUI |
| TCP 8554 | RTSP (MediaMTX) |
| UDP 3702 | WS-Discovery |

Docker: copy `.env.docker.example` to `.env.docker`, set this machine's LAN IP and a new password, then run `docker compose --env-file .env.docker up --build -d`. FFmpeg, MediaMTX and the emulator run in one container. Clips persist in `./video`, state and users in `./data`; see the [Linux and Docker setup guide](docs/INSTALL.md).

In ODM: Discover → **EMU-CAM-1**, or add `http://IP:8080/onvif/device_service` by hand.

### Run as a service (GUI stays on)

```powershell
# Windows (Administrator)
.\scripts\install-service.ps1
```

```bash
# Linux
sudo ./scripts/install.sh --systemd
```

Do not pass `--headless` if you want to configure in the browser.

### Your own clip instead of the test pattern

Channels → "Video source", two options:

1. **Upload from the browser** — the file is copied into `video/` next to the program (created automatically; override with
   `--video-dir` or GUI → System → Video folder) and the channel switches to it. Progress is shown; a same-name file is never overwritten.
2. **A file on the emulator's machine** — enter the full path (`D:\Video\camera.mp4`) or pick it with Browse: no copy.
   From the console: `--channel 1:source=D:\Video\camera.mp4`.

Formats: anything FFmpeg reads — MP4, M4V, MOV, MKV, WebM, AVI, WMV, FLV, MPEG/MPG, VOB, TS/M2TS/MTS, 3GP, OGV, MXF, ...
H.264 (H.265 for an H.265 profile) is streamed as-is; the rest (MPEG-2, Xvid/DivX, WMV, VP9, ProRes…) is re-encoded to H.264
automatically. The clip loops.

### Portable one-file build

```powershell
pip install -e ".[packaging]"
.\packaging\build.ps1 win          # dist\onvif-emulator-v1.8.2-win64.exe (+ *-gui.exe)
```

```bash
./packaging/build.sh linux         # dist/onvif-emulator-v1.8.2-linux-x86_64
```

The Linux ELF must be linked **on Linux** (or CI). On Windows, `python packaging/build.py linux` writes a tar.gz builder, not the ELF.

Two exes, **identical launch flags**:

| File | Purpose |
|---|---|
| `onvif-emulator-v1.8.2-win64.exe` | console (web) build: output and log in the window; for cmd/PowerShell/services |
| `onvif-emulator-v1.8.2-win64-gui.exe` | windowed build: double-click → server + browser; picks a free port if 8080 is taken; shows errors and `--help` in a window |

Both exes carry a camera icon; the windowed one also gets a tray icon by default (right-click → open the GUI / exit) — it has no window and no
taskbar entry, so without the tray icon Task Manager was the only way to tell it was running. Turn it off with `--no-tray`.

### CLI examples

```bash
onvif-emulator --service --open-browser
onvif-emulator --video-dir D:\clips --service
onvif-emulator --channel 1:source=./clip.mp4,encoder=h264 --auth none
onvif-emulator --channel 1:source=rtsp://192.168.1.20:554/stream1,transcode=false
onvif-emulator --channels 8 --auth digest --password N3wPass --add-user "operator1:secret:Operator:1,2"
onvif-emulator --headless --auth none --no-discovery   # no web UI
onvif-emulator --service --onvif-port 18080 --rtsp-port 18554 --auto-port
onvif-emulator --log-level INFO,ERROR,CRITICAL -L D:\logs\emu.log --log-max-mb 20 --log-backups 5
onvif-emulator --set ptz.pan_speed=45 --print-config    # any setting by path; show the result, don't start
onvif-emulator doctor
onvif-emulator cheat-sheet launch
```

Everything configurable in the web GUI also has a launch flag (full list: [docs/CLI.md](docs/CLI.md), `--help`, `--list-settings`); anything without a dedicated flag is set with `--set PATH=VALUE`. The service port can also be changed in the running GUI (System → port) with no restart.

Config order: CLI flags (`--set` last) → `-c file` → `onvif-emulator.conf` beside the program → packaged YAML → `ONVIF_EMU_*`. First run writes the `.conf`. Skip with `--no-conf`.

### Diagnostic bench (version 1.8)

The emulator stands in for **any ONVIF device**, not just "a camera with a picture":

* **Multi-channel:** every channel has its own video, events, PTZ (yes/no, own limits) and RTSP port; up to three **full / medium / low** profiles per channel (`--streams 3`, quality ladder is built automatically).
* **Fleet:** N devices in one process on port ranges — `--devices 10 --onvif-port 8080-8089 --rtsp-port 554-563`; each has its own serial, MAC, Discovery identity and settings.
* **Events:** any event, fired by hand (button/REST) or by a timer — fixed or **random within a range** (seconds/minutes/hours). Kinds: motion, tamper, analytics, **relay**, **dry contact**, metadata, custom topics. Add and remove them in the GUI or from the console (`--rule`).
* **Analytics kinds:** line crossing, field intrusion, cell/region motion, **object counting** (a `Count` counter), loitering — picked from a list, any number of rules per camera, each with its own timer. Events/metadata only, no real video analysis.
* **Push:** PullPoint, WS-Notify (`Subscribe`), webhooks, a metadata stream, a live log.
* **Video overlay:** PTZ HUD and event messages have independent positions; alarms, analytics, metadata, relay and dry-contact transitions appear at the selected screen position in the app language, alongside object boxes and I/O indicators.
* **PTZ presets in the GUI:** save the current position under a name, go to it, remove it — plus Iris/Focus ± and "home" on the same tab.
* **Console constructor and ready-made config:** `onvif-emulator.conf` next to the program is picked up automatically; export/import of the whole thing (`--export-config`, GUI).
* **Users and roles:** Administrator / Operator (watch + PTZ) / Viewer, channel limits — the same for the web UI, ONVIF and
  RTSP; passwords encrypted in `data/users.json`; a sign-in page and a session without repeated password prompts; a Live view
  page with video through the emulator — [docs/USERS.md](docs/USERS.md).
* **UI:** new design, fully in Russian and English (language switch; help and cheat sheets too).
* **OSD on the video:** date and time stamps (format, position, size, colour, background incl. transparent) and a logo picture
  (upload or a path on the emulator's machine; position, size, transparent colour, plate) — [docs/OSD_IMPAIR.md](docs/OSD_IMPAIR.md).
* **Trouble simulator:** rain, noise, pixelation, shaking and covered lens (with Tampering events), packet loss, stutter, freezes,
  CBR / VBR spikes, narrow link, link outages on a random schedule — per channel, presets, `--impair`.
* **Clip without loop:** at the end the stream stops, freezes on the last frame or shows black.
* **Connect page** (opens first): ONVIF address, auth mode, RTSP URI and ONVIF token of every channel and profile, snapshots, preview, ODM and VLC steps. The same without JavaScript at `/info`; ODM's embedded browser ("Web page"), which cannot run the modern GUI, gets that page automatically.
* **Resource protection:** load estimate, limits, `off|warn|clamp|strict`, automatic stream shedding on overload — a bad setting will not freeze the PC.

```bash
# 3 devices x 3 profiles, tamper on all every 30 s, dry contact at random 5..200 s, analytics on the second only
onvif-emulator --service --devices 3 --onvif-port 8080-8082 --rtsp-port 554-556 --streams 3 \
  --rule "kind=tamper,every=30s" --rule "kind=contact,random=5-200s" --rule "kind=analytics,topic=line,device=2,every=1m"
```

Acceptance client: ONVIF Device Manager 2.2.250; the protocol is also checked with a strict client (XML element order per the ONVIF schema).
Details: [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md), [docs/FLEET.md](docs/FLEET.md), [docs/LIMITS.md](docs/LIMITS.md).

### Documentation

| File | Contents |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Windows / Linux / Docker, FFmpeg, MediaMTX, services |
| [docs/CLI.md](docs/CLI.md) | Flags, exit codes, examples |
| [docs/CONFIG.md](docs/CONFIG.md) | YAML fields |
| [docs/FAQ.md](docs/FAQ.md) | FAQ |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Discovery, streams, logs, MediaMTX |
| [docs/ONVIF_MATRIX.md](docs/ONVIF_MATRIX.md) | ONVIF action matrix |
| [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md) | Events, timers, relays, metadata, overlay, REST |
| [docs/OSD_IMPAIR.md](docs/OSD_IMPAIR.md) | OSD (time, date, picture), clip without loop, trouble simulator |
| [docs/USERS.md](docs/USERS.md) | Users, roles, sign-in, password storage and reset |
| [docs/FLEET.md](docs/FLEET.md) | Fleet, constructor, ready-made config |
| [docs/LIMITS.md](docs/LIMITS.md) | Resource protection |
| [docs/ODM_TESTING.md](docs/ODM_TESTING.md) | ODM checklist |
| [MATRIX_ODM.md](MATRIX_ODM.md) | Short acceptance matrix |

In-app: `--help`, `doctor`, `cheat-sheet`, GUI **F1**.

### Tests and cleanup

```bash
python -m pytest
./scripts/clean.sh               # Windows: .\scripts\clean.ps1
```

`.venv` is not in git. Wipe it with `./scripts/clean.sh --all`.

### Lab credentials

Default **admin / admin** is for a bench only; the UI reminds you to change it. Passwords are stored encrypted in
`data/users.json`; reset admin by deleting its `password` line (or `--reset-admin`), see [docs/USERS.md](docs/USERS.md). Do not commit `.env`, `onvif-emulator.conf`, or clips under `video/`.

