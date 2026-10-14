# Пользователи, роли и вход / Users, roles and sign-in

Одни и те же учётные записи действуют везде: веб-интерфейс, ONVIF, RTSP, снимки (`/snapshot`), превью (`/preview`).

## Роли

| Роль | Веб-интерфейс | ONVIF (`UserLevel`) | RTSP / снимки / превью |
|---|---|---|---|
| **Администратор** (`Administrator`) | все разделы, настройки, пользователи | Administrator — все операции | все каналы |
| **Оператор** (`Operator`) | «Подключение», «Просмотр», PTZ, «Справка» | Operator — просмотр + PTZ | все или **выбранные** каналы |
| **Наблюдатель** (`Viewer`) | «Подключение», «Просмотр», «Справка» | User — только просмотр | все или **выбранные** каналы |

Ограничение каналами действует и в ONVIF: оператор/наблюдатель видит в `GetProfiles` только свои каналы, `GetStreamUri` /
`GetSnapshotUri` / PTZ для чужих каналов не работают; MediaMTX пускает его только к путям своих каналов (все три профиля).

## Учётные записи

GUI → **Пользователи**: добавить, удалить, сменить роль, каналы, пароль, отключить учётку (отключённая не входит ни по одному
протоколу). Каждый пользователь меняет свой пароль сам: меню пользователя справа вверху → «Сменить пароль».

**admin** — основная учётка: всегда есть, всегда администратор; её нельзя удалить или отключить, можно только сменить пароль.
Пока у admin пароль по умолчанию (`admin`), интерфейс показывает предупреждение.

## Где хранятся пароли

`data/users.json` рядом с программой — **в зашифрованном виде** (`"password": "enc1:…"`). В `onvif-emulator.conf` и
`data/state.json` паролей больше нет; старые файлы переносятся автоматически при первом запуске (пароли шифруются, из
конфига удаляются).

Ключ шифрования встроен в программу: файл можно перенести на другой компьютер, а пароль не прочитать, просто открыв файл. Это
защита от случайного просмотра, не от владельца программы — держите папку `data` закрытой.

**Сброс пароля admin:** удалите в `data/users.json` строку `"password": …` у admin (или всю запись admin) и перезапустите —
пароль станет `admin`. То же ключом `--reset-admin`. Пароль, вписанный в файл вручную открытым текстом, при следующем запуске
зашифруется.

## Вход в веб-интерфейс

Страница входа → cookie-сеанс (HttpOnly, SameSite=Strict): дальше все разделы без повторных запросов пароля. Неактивный сеанс
закрывается через 12 часов, любой — через 7 дней. 5 неверных паролей с одного адреса за 5 минут — пауза 1 минута. Смена пароля,
роли, каналов или отключение учётки завершают её сеансы. Живое превью идёт через эмулятор (`/preview/...`), поэтому отдельного
окна логина от MediaMTX больше нет. При `--auth none` вход не нужен (все — администраторы).

## CLI

```bash
onvif-emulator --password N3wPass                              # пароль admin
onvif-emulator --user op --password pw                         # добавить/изменить учётку (новая — администратор)
onvif-emulator --add-user "operator1:secret:Operator:1,2"      # имя:пароль:роль[:каналы]
onvif-emulator --add-user "guard:pass:Viewer"                  # все каналы
onvif-emulator --reset-admin                                   # admin / admin
```

Ключи применяются при запуске и сохраняются в `data/users.json` (зашифрованно), в конфиг не пишутся.

## REST (`/api/*`)

| Метод и путь | Кто | Назначение |
|---|---|---|
| `POST /api/login` `{"username","password"}` | все | вход, ставит cookie сеанса |
| `POST /api/logout` | вошедший | выход |
| `GET /api/me` | вошедший | имя, роль, каналы, признак пароля по умолчанию |
| `PUT /api/me/password` `{"old","new"}` | вошедший | сменить свой пароль |
| `GET /api/users` | администратор | список (без паролей), каналы, роли |
| `POST /api/users` `{"username","password","role","channels","enabled"}` | администратор | добавить |
| `PUT /api/users/<имя>` (любые из полей выше) | администратор | изменить |
| `DELETE /api/users/<имя>` | администратор | удалить (кроме admin) |

Скрипты могут вместо cookie слать Basic/Digest в каждом запросе. Права: `GET /api/status`, `GET /api/config` (только свои каналы),
`/snapshot/*`, `/preview/*` — любой вошедший; `POST /api/ptz` — оператор и администратор (свои каналы); всё остальное — администратор.

## English summary

Roles: **Administrator** (everything), **Operator** (watch all or chosen channels + PTZ), **Viewer** (watch only). The same
accounts and channel limits apply to the web UI, ONVIF (UserLevel Administrator / Operator / User, hidden channels) and RTSP
(MediaMTX path permissions). Accounts live encrypted in `data/users.json`; `admin` always exists and cannot be deleted or
disabled. Reset: delete admin's `"password"` line and restart (or `--reset-admin`) — it becomes `admin`. The web UI has a sign-in
page and cookie sessions; the live preview is proxied through the emulator, so MediaMTX never asks for a password.
