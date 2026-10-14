# Установка

## Требования

- Python **3.11+**
- FFmpeg (в `PATH` или путь в `ffmpeg.binary`) — для пайплайнов и snapshot
- MediaMTX (в `PATH` или `vendor/win64` / `vendor/linux64`) — для RTSP/HLS/WebRTC
- По умолчанию: TCP **8080** (ONVIF+GUI), TCP **8554** (RTSP), UDP **3702** (WS-Discovery)

Без FFmpeg/MediaMTX эмулятор стартует: SOAP, Discovery, GUI и `/health` работают, URI отдаются, пайплайны простаивают. Проверка: `onvif-emulator doctor`.

## Windows

1. Установите [Python 3.12](https://www.python.org/downloads/) (галочка «Add to PATH»).
2. Установите [FFmpeg](https://www.gyan.dev/ffmpeg/builds/) и распакуйте `ffmpeg.exe` в PATH.
3. Скачайте [MediaMTX](https://github.com/bluenviron/mediamtx/releases) (`mediamtx.exe`) в PATH или `vendor/win64/`.
4. В каталоге проекта:

```powershell
.\scripts\venv.ps1 -Recreate
.\.venv\Scripts\Activate.ps1
onvif-emulator doctor
onvif-emulator --service --open-browser
```

Окно/процесс не закрывайте: это и есть сервер. Веб-GUI: `http://127.0.0.1:8080/`. Фоном как служба Windows (администратор): `.\scripts\install-service.ps1`.

## Linux

Установите Python, инструменты виртуального окружения, FFmpeg и системные библиотеки GUI не нужны для запуска без окна:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg
./scripts/venv.sh --recreate
source .venv/bin/activate
onvif-emulator doctor
./scripts/run.sh --service
```

MediaMTX скачивать и настраивать отдельно не нужно: сборщик подтягивает его при сборке; обычный исходный запуск можно настроить на внешний MediaMTX через `mediamtx.binary` / `mediamtx.publish_host`.

Системная установка:

```bash
sudo ./scripts/install.sh
sudo ./scripts/install.sh --systemd
sudo ./scripts/uninstall.sh
```

Юнит systemd запускает веб-GUI. Откройте `http://<IP>:8080/`.

### Сборка Linux ELF

ELF нужно собирать на Linux той архитектуры, для которой создаётся файл (обычно x86_64):

```bash
sudo apt-get install -y python3-dev build-essential patchelf
python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[packaging]"
python packaging/build.py linux
./dist/onvif-emulator-v1.8.2-linux-x86_64 --service
```

Сборщик включает Python, GUI, FFmpeg, ffprobe и MediaMTX в переносимый файл. Linux-библиотеки FFmpeg всё равно могут зависеть от glibc целевой системы. На Windows команда `python packaging/build.py linux` создаёт архив исходников с vendor-бинарниками и инструкцией сборки, но не ELF.

## Docker

В образ входят FFmpeg, MediaMTX и приложение. Состояние, конфигурация, журнал и ролики хранятся на хосте; контейнер работает от обычного пользователя с UID/GID из `.env.docker`.

```bash
cp .env.docker.example .env.docker
mkdir -p data video media
# Измените ONVIF_EMU_AUTH__PASSWORD и PUBLIC_HOST в .env.docker.
# На Linux сверьте PUID/PGID с владельцем созданных папок: id -u ; id -g
docker compose --env-file .env.docker up --build -d
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f
```

Откройте `http://<PUBLIC_HOST>:8080/`. Добавляйте камеру в ODM вручную по адресу `http://<PUBLIC_HOST>:8080/onvif/device_service`, затем используйте Digest-учётку из `.env.docker`. При bridge-сети Docker обнаружение WS-Discovery multicast может быть недоступно клиентам в LAN; для такого случая добавьте камеру вручную или настройте Linux `network_mode: host`.

Ролики, загруженные из GUI, появляются в `./video` на хосте и внутри контейнера доступны как `/app/video`. Выберите файл оттуда в GUI или укажите в YAML путь `/app/video/clip.mp4`. Существующие исходники можно положить в `./media`; эта папка примонтирована только для чтения как `/media`, поэтому указывайте `/media/clip.mp4`. Если задаёте другой путь через `VIDEO_HOST_DIR` или `MEDIA_HOST_DIR`, передайте Compose `.env.docker` и обновите владельца папки под PUID/PGID.

`./config` подключена для чтения и записи: можно отредактировать `default.yaml` или положить туда другой файл и задать `CONFIG_FILE=имя.yaml`. Укажите в конфиге пути роликов **внутри контейнера**, например `/media/input.mp4`; локальный путь хоста контейнеру неизвестен. `DATA_HOST_DIR` хранит `state.json`, `users.json`, настройки MediaMTX и журналы. Не удаляйте эту папку, если нужно сохранить учётные записи/PTZ и загруженные настройки.

Основные параметры вынесены в `.env.docker.example`: bind-адрес, объявляемый адрес камеры, порты, host-папки, файл конфигурации, версии базовых образов, UID/GID и учётные данные. Произвольные поддерживаемые настройки приложения передаются переменными `ONVIF_EMU_*` или через YAML в `CONFIG_HOST_DIR`; внутренние пути контейнера (`/app/config`, `/app/data`, `/app/video`, `/media`) остаются стандартными точками монтирования. `ONVIF_PORT`, `RTSP_PORT`, `HLS_PORT`, `WEBRTC_PORT` задают host-публикацию и порт сервиса; `RTSP_RTP_UDP_PORT`, `RTSP_RTCP_UDP_PORT`, `WEBRTC_UDP_PORT` меняют host-сторону UDP-публикаций, тогда как MediaMTX слушает внутренние 8000/8001/8189. `MEDIAMTX_API_PORT` меняет внутренний API-порт, который наружу не публикуется. `ONVIF_DISCOVERY_PORT` меняет host-порт публикации; для WS-Discovery стандартный destination внутри приложения всегда UDP 3702. На Windows этот host-порт может быть занят другим ONVIF-клиентом и тогда Docker не сможет его опубликовать. Для дополнительных RTSP-портов каналов добавьте публикацию каждого порта в Compose; их номера задаются `channels[].rtsp_port`.

Управление:

```bash
docker compose --env-file .env.docker down       # остановить, данные оставить
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker down -v    # удалить также Docker volumes (bind-папки не удаляются)
```

## Portable build Windows

```powershell
python -m pip install -e ".[packaging]"
python packaging/build.py win
```

Создаются `dist/onvif-emulator-v1.8.2-win64.exe` и `dist/onvif-emulator-v1.8.2-win64-gui.exe`.

## Переменные окружения

Префикс `ONVIF_EMU_`, вложенность через `__`: `ONVIF_EMU_ONVIF_PORT=8081`, `ONVIF_EMU_AUTH__MODE=none`, `ONVIF_EMU_DEVICE__MODEL=LAB-CAM`. Переменные среды накладываются на YAML; аргументы CLI имеют последний приоритет. Поля конфигурации: [CONFIG.md](CONFIG.md).
