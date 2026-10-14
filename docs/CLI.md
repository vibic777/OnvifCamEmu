# CLI

Запуск: `onvif-emulator` или `python -m onvif_emu`. Оба Windows-exe принимают **одни и те же ключи**:

| Файл | Что это | Без ключей |
|---|---|---|
| `onvif-emulator-vX.Y.Z-win64.exe` | консольная («web») сборка | запускает сервер в этом окне, GUI на порту 8080 |
| `onvif-emulator-vX.Y.Z-win64-gui.exe` | оконная сборка без консоли | то же, что `--service --open-browser --tray --auto-port` |

Все настройки, которые есть в веб-GUI, можно задать ключом при запуске. Всё остальное — универсальным `--set PATH=VALUE`.

## Оконный exe (`*-gui.exe`) и ключи

- Из **cmd/PowerShell** exe подключается к консоли запустившего: `--help`, `--version`, `doctor`, `--print-config` печатают туда. Приглашение возвращается сразу (это GUI-приложение); для ожидания завершения: `start /wait onvif-emulator-…-gui.exe --check-config`.
- **Двойной клик / ярлык** (консоли нет): вывод `--help` / `--version` / `doctor` показывается окном (длинный — открывается в Блокноте); ошибка запуска (порт занят, неверный ключ) — окном с причиной и путём к логу.
- Ключи ярлыка: «Объект» → `...-gui.exe --onvif-port 18080 --log-level INFO,ERROR -L D:\logs\emu.log`.
- Умолчания GUI-сборки (`--service --open-browser --tray --auto-port`) добавляются, только если вы не указали их сами: `--no-open-browser`, `--no-tray`, `--no-auto-port`, `--headless` их отключают.
- Консольная сборка ничего не добавляет: работает ровно то, что указано. Значок в трее у неё тоже доступен — ключом `--tray` (по умолчанию выключен, у неё и так есть окно консоли в панели задач).
- **Значок в трее** (`--tray`, только Windows): камера в области уведомлений, пока процесс жив — единственный способ узнать, что GUI-сборка ещё работает, кроме диспетчера задач, ведь окна у неё нет. Правый клик → «Открыть веб-интерфейс» / «Выход» (мягкая остановка, как Ctrl+C). `--no-tray` превращает GUI-сборку в чистый фоновый сервис без каких-либо признаков в UI ОС, кроме диспетчера задач.

## Подкоманды

| Команда | Смысл |
|---|---|
| *(без подкоманды)* | Запуск эмулятора |
| `doctor` | Порты, FFmpeg, MediaMTX, UDP |
| `cheat-sheet [topic]` | Шпаргалки: `onvif`, `launch`, `events`, `exit`, `odm` |

## Служебные ключи

| Ключ | Смысл |
|---|---|
| `-h`, `--help` | Справка со всеми ключами |
| `--version` | Версия |
| `-c / --config ФАЙЛ` | YAML/JSON конфиг |
| `--no-conf` | Не читать и не создавать `onvif-emulator.conf` |
| `--check-config` | Проверить конфиг и выйти |
| `--print-config [yaml\|json]` | Показать **итоговый** конфиг (после всех ключей) и выйти; `.conf` не создаётся |
| `--list-settings` | Список всех путей для `--set` с типами и значениями по умолчанию |
| `--set PATH=VALUE` | Любая настройка по пути, повторяемый; значение — YAML (`true`, `8080`, `[a, b]`); `channels.K.PATH=…` — настройка канала K |
| `--export-config FILE` | Записать **полную** эффективную конфигурацию (каскад, правила, каналы) и выйти; формат по расширению `.conf` / `.yaml` / `.json` |

`--set` побеждает остальные ключи. Опечатка в пути даёт подсказку (`did you mean ...`) и код 5.

## Сервис и сеть

| Ключ | Default | Диапазон / значения | GUI |
|---|---|---|---|
| `--host` | `0.0.0.0` | IP интерфейса | Система |
| `--onvif-port` | 8080 | 1..65535 **или диапазон** `8080-8089` (по устройству на порт) | Система / Мастер |
| `--auto-port` / `--no-auto-port` | off (в GUI-exe on) | при занятом/зарезервированном порту взять следующий свободный (до 50 попыток) | Система |
| `--rtsp-port` | 8554 | 1..65535 **или диапазон** `554-563` | Каналы |
| `--devices N` | 1 | 1..256 | Каскад |
| `--https-port` | 8443 | только значение в `GetNetworkProtocols`, слушателя нет | — |
| `--public-host` | авто | hostname/IP для XAddr и RTSP URI | Система |
| `--discovery` / `--no-discovery` | on | WS-Discovery UDP 3702 | — |
| `--multicast-stream` / `--no-…` | off | multicast RTSP URI | — |
| `--gui` / `--no-gui` / `--headless` | GUI on | `--headless` выключает веб-UI | — |
| `--service` | off | долгоживущий сервер, GUI **включён** (не путать с `--headless`) | — |
| `--open-browser` / `--no-open-browser` | off (в GUI-exe on) | открыть GUI в браузере | — |
| `--tray` / `--no-tray` | off (в GUI-exe on) | значок в области уведомлений Windows (открыть GUI / выход); `--no-tray` — чистый фоновый сервис | — |
| `--video-dir` | `video/` рядом | папка роликов | Система |
| `--state-path` | `data/state.json` рядом | файл живого состояния PTZ/users | — |
| `--config-dir-choice` | beside | `beside`, `appdata` | — |
| `-l / --lang` | ru | ru, en | шапка GUI |

### Смена порта во время работы (веб)

GUI → Система → порт ONVIF → Сохранить: процесс **сразу** начинает слушать новый порт, старый закрывается, страница сама переходит на новый адрес. Если порт занят — остаётся старый, в журнале GUI причина, конфиг откатывается. Порт RTSP меняется так же с перезапуском MediaMTX.

## Конструктор (каскад, профили, события из консоли)

Подробно — [FLEET.md](FLEET.md) и [DIAGNOSTICS.md](DIAGNOSTICS.md).

| Ключ | Смысл |
|---|---|
| `--devices N` | N эмулируемых устройств в одном процессе (порты — по возрастанию или по диапазонам) |
| `--streams 1\|2\|3` | профилей на канал: 1 full, 2 = + medium, 3 = + low (лесенка качества строится сама) |
| `--source URI` | одно видео на все каналы (файл, rtsp://, http://, `testsrc`) |
| `--rule SPEC` | добавить правило события, повторяемый: `kind=tamper,every=30s` · `kind=contact,random=5-200s` · `kind=relay,target=relay_1,random=1-100m` · `kind=analytics,topic=line,device=2,every=1m,ch=1` · `kind=custom,topic=tns1:X/Y,data.k=rand:1-9,every=2h` |
| `--device-set N:PATH=VALUE` | `--set` только для устройства N (`channels.K.PATH=…` разрешён) |
| `--relays N`, `--inputs N` | число реле и сухих контактов |
| `--webhook URL` | отправлять каждое событие JSON-ом POST (повторяется) |
| `--diag-notify` / `--no-diag-notify` | WS-Notify подписчикам `Subscribe` |

Поля `--rule`: `kind` (motion, tamper, analytics, relay, contact, metadata, custom), `every=30s|5m|2h`, `random=5-200s|1-100m|1-3h`, `hold=3s`,
`delay=10s`, `repeat=N`, `ch=N|all`, `device=N|all`, `action=pulse|toggle|on|off`, `target=relay_1|di_2`, `topic=…`, `name=…`, `push=URL`,
`data.KEY=VALUE`, `source.KEY=VALUE`, `enabled=false`, `manual` (только вручную).

## Ресурсы (защита от перегрузки)

Подробно — [LIMITS.md](LIMITS.md).

| Ключ | Default | Смысл |
|---|---|---|
| `--limits off\|warn\|clamp\|strict` | clamp | режим защиты |
| `--max-devices` / `--max-channels` / `--max-streams` | 32 / 64 / 48 | жёсткие пределы |
| `--max-load` | 85 | % CPU, доступный кодированию |
| `--max-events-per-sec` / `--max-rules` / `--min-timer-interval` | 200 / 500 / 0.2 | события и таймеры |
| `--cpu-high` / `--shed` / `--stagger-ms` / `--low-priority` | 92 / on / 250 / on | поведение под нагрузкой |

`--check-config` печатает оценку нагрузки (`load: devices=… streams=… encode=… Mpx/s …`) и то, что было бы уменьшено.

## Безопасность и устройство

| Ключ | Default | Значения | GUI |
|---|---|---|---|
| `--auth` | digest | digest, basic, wsse, none | Безопасность |
| `--user` / `--password` | admin | строка | пароль учётки (по умолчанию admin); новая учётка — администратор. Сохраняется зашифрованно в `data/users.json` |
| `--add-user ИМЯ:ПАРОЛЬ:РОЛЬ[:КАНАЛЫ]` | | повторяемый | добавить/изменить учётку: Administrator, Operator, Viewer; каналы через запятую |
| `--reset-admin` | | | пароль admin снова `admin` |
| `--embed-rtsp-credentials` | off | `user:pass@` в GetStreamUri | Безопасность |
| `--device-manufacturer` `--device-model` `--device-firmware` `--device-serial` | Vibic / EMU-CAM-1 / 1.0.0 / EMU00001 | строка | Устройство |
| `--device-name` `--device-location` `--device-hostname` `--device-hardware-id` | | строка | Устройство |
| `--device-timezone` | UTC | IANA (`Europe/Moscow`) | Устройство |
| `--dst` / `--no-dst` | off | флаг DST | — |
| `--ntp` / `--ntp-server ХОСТ` | off / pool.ntp.org | `--ntp-server` повторяемый | — |
| `--scopes` | Profile/Streaming … | через пробел | Устройство |

## Каналы

| Ключ | Смысл |
|---|---|
| `--channels N` | N каналов testsrc (1..64) |
| `--channel SPEC` | канал, повторяемый: `N:source=<uri>,encoder=h264,width=1920,height=1080,fps=25,bitrate=4000,path=ch1,name=Front,loop=true,transcode=false,enabled=true` |
| `--snapshot on\|off` | GetSnapshotUri |
| `--channel N:...,end=stop\|freeze\|black` | ролик без цикла и что показать по окончании |
| `--impair N:SPEC` | помехи канала N (0 = все), повторяемый: `rain=0.6,shake=0.4,shake_every=15s,shake_for=3s,cover=black,loss=3,stutter=10,freeze=on,bitrate=vbr_jumps,cap=300,outage=1-5m,outage_len=5-20s` — [OSD_IMPAIR.md](OSD_IMPAIR.md) |

## PTZ, изображение, OSD

Штампы и картинка: `--osd-time`, `--osd-date` (+ `-format`, `-position`, `-size`, `-color`, `-bg`, `-bg-opacity`),
`--osd-image ПУТЬ` (включает картинку), `--osd-image-position`, `--osd-image-size`, `--osd-image-opacity`, `--osd-image-key #FFFFFF`,
`--osd-image-bg`, `--osd-image-bg-opacity`, `--no-osd-image-show`. Подробно — [OSD_IMPAIR.md](OSD_IMPAIR.md).

| Ключ | Default | Диапазон |
|---|---|---|
| `--ptz-pan-limit` / `--ptz-tilt-limit` | 180 / 90 | градусы, применяются ± |
| `--ptz-zoom-limit` | 1 | 0..1 |
| `--ptz-pan-speed` / `--ptz-tilt-speed` / `--ptz-zoom-speed` | 30 / 20 / 0.25 | °/с, 1/с |
| `--ptz-automove`, `--ptz-flip`, `--ptz-tours` (+ `--no-…`) | off | флаги |
| `--imaging-brightness` `--imaging-contrast` `--imaging-saturation` `--imaging-sharpness` | 50 | 0..100 |
| `--osd on\|off`, `--osd-style ПРЕСЕТ` | on, default | |
| `--osd-color` `--osd-opacity` `--osd-size` `--osd-position` `--osd-duration-ms` | `#00FF88` 0.85 28 top-left 1500 | см. `--help` |

## События и аналитика

| Ключ | Default | Диапазон |
|---|---|---|
| `--events` | timer | timer, motion, manual |
| `--event-motion-interval` / `--event-tamper-interval` / `--event-analytics-interval` | 15 / 60 / 20 | секунды |
| `--event-hold` | 3 | 0.2..60 с |
| `--event-randomize`, `--digital-input` (+ `--no-…`) | on / off | |
| `--analytics` / `--no-analytics` | on | |
| `--analytics-mode` | simulator | simulator, external |
| `--analytics-max-objects` / `--analytics-box-speed` | 4 / 0.08 | 0..32 / 0..1 |

Отдельные правила аналитики (сколько угодно, у каждого свой таймер) — `--rule "kind=analytics,topic=<вид>,..."`, виды:
`line`, `field`, `cellmotion`, `motionregion`, `counting`, `loitering` (см. [DIAGNOSTICS.md](DIAGNOSTICS.md)).

## Медиа-плоскость

| Ключ | Default | Смысл |
|---|---|---|
| `--mediamtx` / `--no-mediamtx` | on | встроенный MediaMTX |
| `--mediamtx-binary ПУТЬ` | пакетный / PATH | внешний бинарник |
| `--mediamtx-publish-host` | 127.0.0.1 | куда публикует FFmpeg |
| `--mediamtx-api` / `--mediamtx-api-port` | on / 9997 | |
| `--hls` / `--hls-port` | on / 8888 | |
| `--webrtc` / `--webrtc-port` | on / 8889 | |
| `--ffmpeg ПУТЬ` / `--ffprobe ПУТЬ` | пакетные / PATH | |
| `--ffmpeg-arg=АРГ` | — | доп. аргумент FFmpeg, повторяемый (со знаком `=`: `--ffmpeg-arg=-threads --ffmpeg-arg=2`) |

## Журнал

Уровни выбираются **независимо** (мультивыбор), не порогом. По умолчанию — только `CRITICAL`.

| Ключ | Default | Смысл |
|---|---|---|
| `--log-level` / `--log-levels` / `--loglevel` | CRITICAL | `TRACE,DEBUG,INFO,WARNING,ERROR,CRITICAL` через запятую или `ALL`; алиасы `WARN`, `FATAL` |
| `-v` / `--verbose` | off | все уровни (= `--log-level ALL`) |
| `-q` / `--quiet` | off | `ERROR,CRITICAL` |
| `-L` / `--log-file ПУТЬ` | `onvif-emulator.log` рядом с программой | файл; если это папка — файл создаётся в ней |
| `--log-dir ПАПКА` | | папка для `onvif-emulator.log`, если `-L` не задан |
| `--log-console` / `--no-log-console` | on | дублировать выбранные уровни в stderr (в GUI-exe консоли нет) |
| `--log-max-mb N` | 5 | размер файла до ротации, МБ |
| `--log-backups N` | 3 | сколько ротированных файлов хранить |

Приоритет пути: `-L` → `logging.file` → `--log-dir` / `logging.dir` → рядом с программой (если писать нельзя — `%APPDATA%\onvif-emulator`). Падения дополнительно пишутся в `onvif-emulator-crash.log` рядом с exe.

## Примеры

```bash
onvif-emulator --no-gui --channel 1:source=./video.mp4,encoder=h264
onvif-emulator --channel "1:source=D:\Видео\old cam.avi"   # любой формат FFmpeg; не-H.264 перекодируется сам
onvif-emulator --channel 1:source=rtsp://192.168.1.20/stream1,transcode=false
onvif-emulator --channels 8 --auth digest --password N3wPass --add-user "guard:pass:Viewer:1"
onvif-emulator --headless --auth none --no-discovery --onvif-port 8080
onvif-emulator --service --devices 3 --onvif-port 8080-8082 --rtsp-port 554-556 --streams 3 --rule "kind=tamper,every=30s"
onvif-emulator --no-conf --export-config stend.conf --devices 10 --onvif-port 8080-8089 --rtsp-port 554-563
onvif-emulator -c stend.conf                     # поднять ранее сохранённый стенд
onvif-emulator --service --open-browser
onvif-emulator --service --onvif-port 18080 --rtsp-port 18554 --auto-port
onvif-emulator --log-level INFO,ERROR,CRITICAL -L D:\logs\emu.log --log-max-mb 20 --log-backups 5
onvif-emulator -v --no-log-console --log-dir D:\logs
onvif-emulator --set ptz.pan_speed=45 --set ntp.servers=[a,b] --print-config
onvif-emulator --list-settings
onvif-emulator --video-dir D:\clips --service
onvif-emulator --check-config -c ./config/default.yaml
onvif-emulator doctor
onvif-emulator cheat-sheet launch
```

## Приоритет конфигурации

1. `--set`, затем остальные флаги CLI  
2. `-c path.yml`  
3. `onvif-emulator.conf` рядом с программой (первый запуск создаёт файл; `--no-conf` пропускает)  
4. Встроенный `src/onvif_emu/data/default.yaml`  
5. `ONVIF_EMU_*` (например `ONVIF_EMU_AUTH__MODE=none`; накладывается на файл, CLI побеждает)

## Коды выхода

| Код | Значение |
|---|---|
| 0 | OK |
| 1 | Ошибка конфига |
| 2 | Порт занят / запрещён (см. `--auto-port`) |
| 3 | Нет FFmpeg/MediaMTX (только `doctor`; запуск всё равно идёт) |
| 4 | Runtime |
| 5 | Usage: неизвестный ключ, плохое значение, неверный путь `--set` |

Ошибки аргументов печатают подсказку `See --help`.
