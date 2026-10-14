# Диагностическое устройство: события, таймеры, реле, метаданные

Эмулятор — не только «камера с картинкой», а **стенд для проверки клиентов**: любое событие можно запустить
вручную или по таймеру, у каждого канала свои события, есть реле и сухие контакты, метаданные аналитики и живой
оверлей поверх видео. Раздел GUI: **Диагностика**. Из консоли — ключи `--rule`, `--relays`, `--inputs`, `--webhook`.

## Правила событий

Правило = независимый источник событий со своим таймером. Добавляйте и удаляйте сколько нужно (лимит `limits.max_rules`),
в том числе несколько правил одного вида на одну камеру (например, две линии и счётчик на канале 1): у каждого свой таймер
и своё название — оно уходит в ONVIF как `Rule` в `Source` события. Обычная тревога (детектор движения) — `kind=motion`.

| Поле | Значения | Смысл |
|---|---|---|
| `kind` | `motion`, `tamper`, `analytics`, `relay`, `contact`, `metadata`, `custom` | что генерировать |
| `channel` | 0 = все каналы, N | для видео-событий (у каждого канала свой источник `vsc_N`) |
| `device` | 0 = все устройства, N | в каскаде: на каком устройстве работает правило |
| `schedule` | `manual`, `interval`, `random` | только вручную / постоянный период / случайная пауза в диапазоне |
| `unit` | `seconds`, `minutes`, `hours` | единицы времени периода и диапазона |
| `interval` | число | период для `interval` |
| `range_min`, `range_max` | числа | диапазон для `random` (каждый раз новая пауза): «1–100 мин», «5–200 с» |
| `action` | `pulse`, `toggle`, `on`, `off` | импульс (вкл→пауза `hold_s`→выкл), переключение, включить, выключить |
| `hold_s` | секунды | длительность импульса |
| `repeat` | 0 = бесконечно | сколько раз сработать |
| `start_delay_s` | секунды | задержка перед первым срабатыванием |
| `target` | `relay_1`, `di_2` | какое реле / вход (для `relay` / `contact`) |
| `topic` | `line`, `field`, `cellmotion`, `motionregion`, `counting`, `loitering` или полный топик | вид аналитики (в GUI — список «Вид аналитики»); для `custom` — любой топик |
| `source`, `data` | ключ→значение | свои `SimpleItem` в `Source` / `Data` события |
| `push_url` | URL | дополнительно отправить это событие POST-ом |

Из консоли:

```bash
onvif-emulator --rule "kind=tamper,every=30s"                       # тревожный детектор каждые 30 с
onvif-emulator --rule "kind=contact,random=5-200s"                  # сухой контакт, случайно 5…200 с
onvif-emulator --rule "kind=relay,target=relay_1,random=1-100m"     # реле, случайно 1…100 минут
onvif-emulator --rule "kind=analytics,topic=line,device=2,every=1m,ch=1"
onvif-emulator --rule "kind=analytics,topic=counting,name=Door,random=1-5m,ch=2"   # подсчёт: +1 каждые 1…5 минут
onvif-emulator --rule "kind=analytics,topic=loitering,name=Yard,every=90s,hold=10s"
onvif-emulator --rule "kind=custom,topic=tns1:Door/Open,every=2h,data.level=rand:1-9,hold=5s"
```

Формат правила: `key=value` через запятую; время — `30s`, `5m`, `2h`; диапазон — `5-200s`, `1-100m`.

### Шаблоны значений (`data` / `source`)

`rand:1-100` (целое), `randf:0-1` (дробное), `choice:a|b|c`, `seq` (счётчик срабатываний), `now` (время UTC),
`uuid`, `channel` (номер канала). Остальной текст передаётся как есть (кавычки и `<>` экранируются).

## Что происходит при срабатывании

* **Motion / Tamper** — `tns1:VideoSource/MotionAlarm`, `tns1:VideoSource/Tampering`, свойство `State`.
* **Analytics** — вид задаёт `topic`:

  | `topic` | GUI | ONVIF-топик | Данные |
  |---|---|---|---|
  | `line` | Пересечение линии | `RuleEngine/LineDetector/Crossed` | `ObjectId` (обычное событие без `PropertyOperation`) |
  | `field` | Вторжение в зону | `RuleEngine/FieldDetector/ObjectsInside` | `IsInside` |
  | `cellmotion` | Движение в ячейках | `RuleEngine/CellMotionDetector/Motion` | `IsMotion` |
  | `motionregion` | Движение в области | `RuleEngine/MotionRegionDetector/Motion` | `State` |
  | `counting` | Подсчёт объектов | `RuleEngine/CountAggregation/Counter` | `Count` — нарастающий итог, +1 за срабатывание; в `data` `Step=5` меняет шаг, `Count=rand:0-50` задаёт значение |
  | `loitering` | Праздношатание | `RuleEngine/LoiteringDetector/ObjectIsLoitering` | `IsLoitering` |

  В `Source` всегда `VideoSourceConfigurationToken`, `VideoAnalyticsConfigurationToken` и `Rule` (название правила).
  Все виды объявлены в `GetEventProperties` и в `GetRules` / `GetAnalyticsModuleOptions`, клиент видит их заранее.
  Настоящего анализа видео нет — это генератор событий для проверки клиента.
* **Relay** — реле переключается по-настоящему (`GetRelayOutputs`, `SetRelayOutputState`, режимы Bistable/Monostable с возвратом
  через `DelayTime`), событие `tns1:Device/Trigger/Relay` (`LogicalState`).
* **Contact** — сухой контакт (вход) меняет состояние, событие `tns1:Device/Trigger/DigitalInput` (`LogicalState`).
* **Metadata** — в симулятор канала добавляются объекты (`count`, `class`, `ttl_s`, `speed` — через `data`), уходит кадр метаданных.
* **Custom** — любой топик и любые данные.

Реле и контакты также переключаются кнопками в GUI и через `POST /api/diag/relay/<token>` / `/api/diag/input/<token>`.
Число реле/входов — `--relays N`, `--inputs N` (`diag.relays`, `diag.inputs`).

## Доставка (пуш)

| Канал | Как включить |
|---|---|
| PullPoint | ODM Event Viewer, любой ONVIF-клиент (`CreatePullPointSubscription`) |
| WS-BaseNotification | клиент вызывает `Subscribe` с `ConsumerReference` — эмулятор шлёт `Notify` на его адрес (`diag.notify`) |
| Webhook | `--webhook URL` (повторяется) или `diag.webhooks`: каждое событие уходит JSON-ом POST |
| Webhook правила | поле `push_url` у самого правила |
| Живой журнал | GUI → Диагностика, `GET /api/diag/log` |
| Метаданные | `GET /api/metadata/<канал>` (XML `MetadataStream`), поток `GET /api/metadata/stream/<канал>` (~5 кадров/с), webhook |

Очередь PullPoint ограничена 128 сообщениями, частота событий — `limits.max_events_per_s` (лишнее отбрасывается и считается).
`GetEventProperties` отдаёт `TopicSet` с описанием каждого топика (`MessageDescription`), включая топики ваших `custom`-правил.

## Оверлей поверх видео

Слой рисуется вживую (≈8 кадров/с) и накладывается FFmpeg-ом на все потоки канала (full/medium/low):

| Элемент | Когда виден |
|---|---|
| Стрелки ▲▼◀▶, STOP, ZOOM ±, IRIS ±, FOCUS ± | пока идёт PTZ/imaging-команда и `osd.style.duration_ms` после неё; внизу значения PAN/TILT/ZOOM |
| Баннер команды (`SET PRESET: имя`, `GOTO PRESET: имя`, `DEL PRESET: имя`, `HOME`, `SET HOME`, `AUX: ...`) | сверху по центру, 3 с — на `SetPreset` / `GotoPreset` / `RemovePreset` / `GotoHomePosition` / `SetHomePosition` / `SendAuxiliaryCommand` (ONVIF PTZ или веб-GUI) |
| Сообщения тревог и аналитики, META xN, реле ВКЛ/ВЫКЛ, входы АКТИВЕН/СБРОС и имя своего события | рядом с выбранной позицией `osd.style.event_position`; язык берётся из `language` |
| Рамки объектов (класс, %, id) | для объектов из правил `metadata` (`osd.style.boxes`: `injected` / `all` / `off`) |
| Светодиоды IN1…, R1… | всегда (`osd.style.show_io`): зелёный — покой, красный — активен |
| `LOAD SHED L1/L2` | губернатор отключил часть потоков ([LIMITS.md](LIMITS.md)) |

Настройки: GUI → OSD (цвет, прозрачность, положение PTZ HUD и отдельное положение сообщений событий, длительность, элементы), ключи `--osd-*`. Оверлей рисуется без шрифтов и
библиотек (встроенный пиксельный шрифт 5×7), требует FFmpeg; при `--osd off` его нет и потоки кодируются легче.

## REST API (`/api/*`, доступ как у GUI: пользователи ONVIF)

| Метод и путь | Назначение |
|---|---|
| `GET /api/diag` | правила, состояние реле/входов, счётчики, оценка нагрузки |
| `PUT /api/diag` | заменить блок целиком |
| `POST /api/diag/rules`, `PUT/DELETE /api/diag/rules/<id>` | добавить / изменить / удалить правило |
| `POST /api/diag/fire` | `{"rule_id": "…"}` или `{"rule": {…}}` — запустить один раз (ответ сразу; `?wait=1` — дождаться) |
| `POST /api/diag/relay/<token>`, `/input/<token>` | `{"active": true}` |
| `GET /api/diag/log?limit=N` | последние события |
| `GET /api/metadata/<ch>`, `/api/metadata/stream/<ch>` | метаданные аналитики |
| `GET /api/limits` | нагрузка и губернатор |
