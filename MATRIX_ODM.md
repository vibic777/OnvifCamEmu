# MATRIX ODM — приёмка

Целевой клиент: ONVIF Device Manager.

| # | Проверка | Ожидание | OK |
|---|---|---|---|
| 1 | Discover | EMU-CAM-1, scopes Streaming | ☐ |
| 2 | Auth digest | подключение | ☐ |
| 3 | Auth basic | подключение | ☐ |
| 4 | Auth wsse | подключение | ☐ |
| 5 | Auth none | подключение | ☐ |
| 6 | Live main | RTSP chN | ☐ |
| 7 | Live sub | RTSP chN_sub | ☐ |
| 8 | Snapshot | JPEG | ☐ |
| 9 | PTZ move | стрелки/абс., GetStatus живой | ☐ |
| 10 | Presets | set/goto после рестарта | ☐ |
| 11 | Events | Motion + Tamper, корректный UTC | ☐ |
| 12 | OSD | иконки на кадре при move/event | ☐ |
| 13 | Multiplier | N профилей = N каналов | ☐ |
| 14 | Analytics | BoundingBox / modules | ☐ |
| 15 | GUI | все разделы п.2.12 без правки YAML | ☐ |

Подробности: [docs/ODM_TESTING.md](docs/ODM_TESTING.md).
