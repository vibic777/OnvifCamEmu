# Тестирование в ONVIF Device Manager

Инструмент: [ONVIF Device Manager](https://sourceforge.net/projects/onvifdm/). Сеть: эмулятор и ПК с ODM в одной L2/L3, UDP 3702 не режется файрволом.

Учётные данные по умолчанию: `admin` / `admin`. Режим `--auth digest` (как у большинства камер).

## Чек-лист

1. **Discovery.** ODM → Devices → Discover. Появляется `EMU-CAM-1` / имя из GUI. Scopes содержат `Profile/Streaming`.
2. **Подключение digest.** Add device, Digest. Live video не должно быть пустым (нужны FFmpeg+MediaMTX).
3. **Basic.** `--auth basic`, переподключить.
4. **WS-Security.** `--auth wsse`.
5. **Anonymous.** `--auth none`.
6. **Main / sub.** Два профиля на канал; оба URI `rtsp://…/ch1` и `ch1_sub`.
7. **Snapshot.** Вкладка snapshot — JPEG.
8. **PTZ.** Стрелки: ContinuousMove; ползунок: Absolute; позиция в GetStatus меняется плавно и не выходит за лимиты.
9. **Presets.** Set → Goto после «рестарта» эмулятора (persist `data/state.json`); на видео при Set/Goto/Remove/Home виден баннер с именем препозиции.
10. **Events.** Event Viewer: `tns1:VideoSource/MotionAlarm` и `Tampering`. Время UTC, согласованное с GetSystemDateAndTime.
11. **Time.** Если ODM ругается на время — timezone IANA (`Europe/Moscow`) и NTP flag.
12. **Множитель.** `--channels 4` → четыре профиля в ODM.
13. **Analytics.** Modules Motion/Line/Field; метаданные BoundingBox через GUI Analytics или GetMetadata.

Локальные галочки хранятся в GUI → Шпаргалки (не заменяют этот файл).

## Типичные URL

```
http://<ip>:8080/onvif/device_service
rtsp://admin:admin@<ip>:8554/ch1        # профиль 1 (profile_1)
rtsp://admin:admin@<ip>:8554/ch1_sub    # профиль 2 (profile_1_sub)
rtsp://admin:admin@<ip>:8554/ch1_third  # профиль 3 (profile_1_third), если включён
http://<ip>:8080/snapshot/1.jpg
http://<ip>:8080/health
http://<ip>:8080/info                   # все адреса без JavaScript
```

Точные адреса именно вашего запуска (порты, каналы, профили) — в веб-GUI, раздел **«Подключение»**.

## Дополнительно для диагностического стенда (версия 1.3)

- [ ] Профили: у канала три профиля (full / medium / low), у каждого работает Live View.
- [ ] PTZ-панель: только у устройств/каналов с PTZ; при нажатии стрелок/зума на видео подсвечиваются кнопки HUD.
- [ ] Event Viewer: приходят motion / tamper / relay / digital input / analytics; события каналов различаются по `VideoSourceConfigurationToken`.
- [ ] Device → Relay outputs: список реле, `SetRelayOutputState` переключает реле (светодиод R1 на видео краснеет, приходит событие).
- [ ] Каскад: Discover показывает все устройства, у каждого свой серийный номер и адрес.
- [ ] Строгая проверка протокола: `onvif_strict_probe.py` без ошибок (см. TROUBLESHOOTING).

## Дополнительно (версия 1.4)

- [ ] На видео при `SetPreset`/`GotoPreset`/`RemovePreset`/`GotoHomePosition`/`SetHomePosition` виден баннер сверху по центру с именем команды/препозиции.
- [ ] Веб-GUI → PTZ: сохранить препозицию с именем, перейти, удалить; Iris ±, Focus ±, «домой»/«задать дом» работают.
- [ ] Windows, оконная сборка (`*-gui.exe`): значок камеры в трее, правый клик → «Открыть веб-интерфейс» / «Выход»; `--no-tray` его убирает.
- [ ] Оба `.exe` показывают иконку-камеру (не стандартный значок Python) в свойствах файла / панели задач.

## Дополнительно (версия 1.5–1.6)

- [ ] ODM → устройство → **Web page**: открывается упрощённая страница (адрес ONVIF, таблица каналов/профилей/RTSP), без ошибки скрипта.
- [ ] Веб-GUI → **Подключение**: адрес ONVIF и RTSP-адреса совпадают с тем, что ODM получает в `GetStreamUri`; кнопки «Копировать» работают.
- [ ] Диагностика → правило «Аналитика», вид «Подсчёт объектов»: в Event Viewer приходит `RuleEngine/CountAggregation/Counter`, `Count` растёт.
- [ ] Два правила одного вида на одной камере (разные названия): в Event Viewer различаются по `Rule`.
- [ ] «Пересечение линии», «Праздношатание», «Движение в области» приходят с правильными топиками (см. [DIAGNOSTICS.md](DIAGNOSTICS.md)).
- [ ] Каналы → вариант 2 (путь к файлу AVI/WMV/MPG): ODM Live video показывает ролик, профиль H.264.
- [ ] Каналы → вариант 1: загрузка MP4 с кириллицей и запятой в имени, прогресс виден, канал переключился.

## Дополнительно (версия 1.7)

- [ ] OSD: штамп времени и даты виден в Live video всех профилей, формат и положение совпадают с настройками; фон 0 — прозрачный.
- [ ] OSD: логотип (PNG с прозрачностью и JPEG с «прозрачным цветом») в нужном углу и размере.
- [ ] Канал без цикла: по окончании ролика поток пропадает (stop) / застывает (freeze) / чернеет (black).
- [ ] Помехи «Вандал»: при тряске и закрытии объектива в Event Viewer приходит `Tampering` true → false.
- [ ] Помехи «Плохая сеть»: ODM показывает артефакты, рывки, замирания, поток пропадает и восстанавливается сам.

## Дополнительно (версия 1.8)

- [ ] Оператор с каналом 2: ODM после входа показывает только профили канала 2, PTZ работает, `GetUsers` у него запрещён.
- [ ] Наблюдатель: Live video работает, PTZ-команды отклоняются.
- [ ] ODM SetUser меняет пароль admin; DeleteUsers для admin отклоняется.
- [ ] RTSP: наблюдатель, ограниченный каналом 1, не открывает `ch2`.

