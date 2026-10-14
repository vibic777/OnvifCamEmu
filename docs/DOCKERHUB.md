# Docker Hub description / Описание Docker Hub

This is the bilingual description for the public repository [vibic777/onvif-emulator](https://hub.docker.com/r/vibic777/onvif-emulator). Keep the image tags and release links in sync with the published releases.

---

# ONVIF Camera Emulator

**Русский ниже · English below**

An ONVIF IP-camera emulator with a web interface and optional video streaming. Run it with Docker Compose on Linux, Windows (Docker Desktop), or macOS. The container includes the emulator, FFmpeg, and MediaMTX.

The currently built and tested image targets `linux/amd64`; other CPU architectures have not yet been verified.

## Features

- ONVIF Device, Media, PTZ, Imaging, Events, and Analytics services.
- WS-Discovery over UDP multicast for compatible networks.
- Browser-based management interface and event/analytics overlays with Russian and English text labels.
- Video sources supported by FFmpeg; RTSP output through the bundled MediaMTX server.
- Optional HLS and WebRTC streaming endpoints.
- Persistent configuration, users, runtime state, logs, and uploaded video.
- Runs as an unprivileged user in the container.

## Quick start

1. Download the Compose file and environment template from the project release, or clone the repository.
2. Copy `.env.docker.example` to `.env.docker`.
3. Set `PUBLIC_HOST` to the IP address clients use to reach the Docker host. Set a unique, strong `ONVIF_EMU_AUTH__PASSWORD`.
4. Create the host folders for configuration, state, and video, then start the service:

```bash
mkdir -p config data video media
docker compose --env-file .env.docker up -d
```

For a source checkout, build locally with `docker compose --env-file .env.docker up --build -d`.

Open `http://<PUBLIC_HOST>:8080/`. The Compose container is named `OnvifCamEmu`. In ONVIF Device Manager, add the device manually at `http://<PUBLIC_HOST>:8080/onvif/device_service` if multicast discovery is unavailable. Sign in with username `admin` and the password configured in `.env.docker`.

## Docker Hub image

The published image is `vibic777/onvif-emulator`. Pull and run the current release with:

```bash
docker pull vibic777/onvif-emulator:1.8.2
docker compose --env-file .env.docker pull
docker compose --env-file .env.docker up -d
```

Use the version tag for repeatable deployments. `latest` points to the most recent stable release.

## Ports

| Host port (default) | Protocol | Purpose |
|---|---|---|
| `8080` | TCP | ONVIF services and web interface |
| `8554` | TCP | RTSP streaming |
| `3702` | UDP | WS-Discovery multicast |
| `8888` | TCP | HLS |
| `8889` | TCP | WebRTC HTTP/signaling |
| `8000` | UDP | RTSP RTP media |
| `8001` | UDP | RTSP RTCP |
| `8189` | UDP | WebRTC media |

Ports are configurable in `.env.docker`. Keep the host's firewall and router rules aligned with the ports you publish. Additional per-channel RTSP ports require matching Compose port mappings.

## Persistent folders and video

The default Compose file binds these host folders:

| Host folder | Container path | Use |
|---|---|---|
| `./config` | `/app/config` | YAML configuration |
| `./data` | `/app/data` | Users, state, logs, and MediaMTX runtime files |
| `./video` | `/app/video` | Videos uploaded through the web interface |
| `./media` | `/media` (read-only) | Existing source clips |

To use a file in `./media`, set the channel source to its container path, for example `/media/camera.mp4`. Host paths are configured with `CONFIG_HOST_DIR`, `DATA_HOST_DIR`, `VIDEO_HOST_DIR`, and `MEDIA_HOST_DIR`. The service cannot read arbitrary host paths unless they are mounted into the container.

## Configuration

Copy `.env.docker.example` to `.env.docker` and edit it before starting. Common settings:

| Variable | Purpose |
|---|---|
| `PUBLIC_HOST` | Address advertised to ONVIF clients; set to the reachable Docker host address |
| `BIND_ADDRESS` | Host interface used for published ports; defaults to all interfaces |
| `ONVIF_EMU_AUTH__USERNAME` / `ONVIF_EMU_AUTH__PASSWORD` | ONVIF and web-interface credentials |
| `ONVIF_EMU_AUTH__MODE` | Authentication mode; the template defaults to Digest |
| `ONVIF_PORT`, `RTSP_PORT`, `ONVIF_DISCOVERY_PORT` | ONVIF/GUI, RTSP, and discovery host ports |
| `HLS_PORT`, `WEBRTC_PORT`, `RTSP_RTP_UDP_PORT`, `RTSP_RTCP_UDP_PORT`, `WEBRTC_UDP_PORT` | Streaming host ports |
| `CONFIG_HOST_DIR`, `DATA_HOST_DIR`, `VIDEO_HOST_DIR`, `MEDIA_HOST_DIR` | Host folders mounted into the container |
| `CONFIG_FILE` | YAML configuration file name under `/app/config` |
| `PUID`, `PGID` | Container process user/group IDs for host file permissions |
| `ONVIF_EMU_*` | Other supported application settings; nested YAML sections use `__`, e.g. `ONVIF_EMU_DEVICE__NAME` |

See the [configuration guide](CONFIG.md) and [installation guide](INSTALL.md) for all details.

## Docker Desktop and WS-Discovery

The ONVIF and streaming services are available through published ports. WS-Discovery uses UDP multicast, and multicast forwarding through Docker bridge networking—especially Docker Desktop on Windows and macOS—depends on the host network setup and may not work. Add the ONVIF service URL manually when automatic discovery does not find the device. UDP port `3702` must also be free on the host; another ONVIF tool bound to that port can prevent Docker from starting with the default mapping.

## Security

- Change the example password before starting and protect `.env.docker`; it contains credentials.
- The default `BIND_ADDRESS=0.0.0.0` publishes ports on every host interface. Use a specific address or firewall rules to restrict access when appropriate.
- Authentication is enabled by default. Avoid `ONVIF_EMU_AUTH__MODE=none` outside an isolated test network.
- HTTP/RTSP are not encrypted by default. Use a trusted network, VPN, or a properly configured TLS reverse proxy where traffic crosses an untrusted network.
- Back up `DATA_HOST_DIR` and any configuration or video folders you need to retain.

## Links

- Source and releases: [github.com/vibic777/onvif-emulator](https://github.com/vibic777/onvif-emulator)
- Documentation: [project documentation](https://github.com/vibic777/onvif-emulator/tree/main/docs)
- License: [MIT](https://github.com/vibic777/onvif-emulator/blob/main/LICENSE)

---

# Эмулятор ONVIF-камеры

Эмулятор IP-камеры с ONVIF, веб-интерфейсом и видеопотоками. Запускается через Docker Compose в Linux, Windows (Docker Desktop) и macOS. В контейнер включены приложение, FFmpeg и MediaMTX.

Текущий собранный и проверенный образ предназначен для `linux/amd64`; другие архитектуры пока не проверялись.

## Возможности

- Сервисы ONVIF Device, Media, PTZ, Imaging, Events и Analytics.
- Обнаружение WS-Discovery по UDP multicast в сетях, где multicast доступен.
- Веб-интерфейс управления на русском и английском языках.
- Источники видео, поддерживаемые FFmpeg; RTSP-трансляция через MediaMTX.
- Дополнительные точки потоков HLS и WebRTC.
- Сохранение конфигурации, пользователей, состояния, журналов и загруженных роликов.
- Контейнер запускает приложение от непривилегированного пользователя.

## Быстрый запуск

1. Скачайте Compose-файл и шаблон переменных окружения из релиза проекта либо клонируйте репозиторий.
2. Скопируйте `.env.docker.example` в `.env.docker`.
3. Укажите в `PUBLIC_HOST` IP-адрес Docker-хоста, доступный клиентам. Задайте уникальный сложный пароль в `ONVIF_EMU_AUTH__PASSWORD`.
4. Создайте папки конфигурации, состояния и видео на хосте и запустите:

```bash
mkdir -p config data video media
docker compose --env-file .env.docker up -d
```

В клоне исходного репозитория соберите образ локально командой `docker compose --env-file .env.docker up --build -d`.

Откройте `http://<PUBLIC_HOST>:8080/`. Контейнер Compose называется `OnvifCamEmu`. Если ONVIF Device Manager не обнаруживает устройство по multicast, добавьте его вручную по адресу `http://<PUBLIC_HOST>:8080/onvif/device_service`. Имя пользователя — `admin`, пароль задаётся в `.env.docker`.

## Образ Docker Hub

Опубликованный образ находится в `vibic777/onvif-emulator`. Скачайте текущий релиз и запустите его:

```bash
docker pull vibic777/onvif-emulator:1.8.2
docker compose --env-file .env.docker pull
docker compose --env-file .env.docker up -d
```

Для повторяемого развёртывания указывайте тег версии. `latest` указывает на последний стабильный релиз.

Для повторяемого развертывания используйте тег версии. Если публикуется тег `latest`, он соответствует последнему стабильному выпуску.

## Порты

| Порт хоста (по умолчанию) | Протокол | Назначение |
|---|---|---|
| `8080` | TCP | ONVIF и веб-интерфейс |
| `8554` | TCP | RTSP-поток |
| `3702` | UDP | multicast WS-Discovery |
| `8888` | TCP | HLS |
| `8889` | TCP | HTTP/signaling WebRTC |
| `8000` | UDP | RTP-медиа RTSP |
| `8001` | UDP | RTCP |
| `8189` | UDP | медиа WebRTC |

Порты меняются в `.env.docker`. Настройте правила брандмауэра и маршрутизатора для опубликованных портов. Для дополнительных RTSP-портов отдельных каналов добавьте соответствующие публикации в Compose.

## Постоянные данные и видео

Compose подключает следующие каталоги хоста:

| Каталог хоста | Путь в контейнере | Назначение |
|---|---|---|
| `./config` | `/app/config` | YAML-конфигурация |
| `./data` | `/app/data` | Пользователи, состояние, журналы и файлы MediaMTX |
| `./video` | `/app/video` | Ролики, загруженные через веб-интерфейс |
| `./media` | `/media` (только чтение) | Существующие исходные ролики |

Чтобы использовать файл из `./media`, укажите в источнике канала контейнерный путь, например `/media/camera.mp4`. Каталоги хоста задаются через `CONFIG_HOST_DIR`, `DATA_HOST_DIR`, `VIDEO_HOST_DIR` и `MEDIA_HOST_DIR`. Контейнер не получает доступ к произвольным путям хоста, если они не подключены как том.

## Настройки

Скопируйте `.env.docker.example` в `.env.docker` и измените значения до первого запуска. Основные параметры:

| Переменная | Назначение |
|---|---|
| `PUBLIC_HOST` | Адрес Docker-хоста, который объявляется ONVIF-клиентам |
| `BIND_ADDRESS` | Сетевой интерфейс хоста для публикации портов; по умолчанию все интерфейсы |
| `ONVIF_EMU_AUTH__USERNAME` / `ONVIF_EMU_AUTH__PASSWORD` | Учётные данные ONVIF и веб-интерфейса |
| `ONVIF_EMU_AUTH__MODE` | Режим аутентификации; шаблон использует Digest |
| `ONVIF_PORT`, `RTSP_PORT`, `ONVIF_DISCOVERY_PORT` | Порты ONVIF/GUI, RTSP и discovery на хосте |
| `HLS_PORT`, `WEBRTC_PORT`, `RTSP_RTP_UDP_PORT`, `RTSP_RTCP_UDP_PORT`, `WEBRTC_UDP_PORT` | Порты потокового видео на хосте |
| `CONFIG_HOST_DIR`, `DATA_HOST_DIR`, `VIDEO_HOST_DIR`, `MEDIA_HOST_DIR` | Каталоги хоста, подключаемые в контейнер |
| `CONFIG_FILE` | Имя YAML-файла внутри `/app/config` |
| `PUID`, `PGID` | UID/GID пользователя контейнера для прав на файлы хоста |
| `ONVIF_EMU_*` | Остальные поддерживаемые параметры приложения; вложенность задаётся `__`, например `ONVIF_EMU_DEVICE__NAME` |

Подробности приведены в [справке по конфигурации](CONFIG.md) и [руководстве по установке](INSTALL.md).

## Docker Desktop и WS-Discovery

ONVIF и видеопотоки доступны через опубликованные порты. WS-Discovery использует UDP multicast; его передача через bridge-сеть Docker, особенно в Docker Desktop для Windows и macOS, зависит от сетевой конфигурации хоста и может быть недоступна. Если устройство не находится автоматически, добавьте URL сервиса ONVIF вручную. Порт UDP `3702` должен быть свободен на хосте: другая ONVIF-программа, занявшая этот порт, помешает стандартной публикации Docker.

## Безопасность

- Замените пароль из шаблона до запуска и защитите `.env.docker`: в нём хранятся учётные данные.
- `BIND_ADDRESS=0.0.0.0` публикует порты на всех интерфейсах хоста. При необходимости ограничьте адрес привязки и доступ правилами брандмауэра.
- Аутентификация включена по умолчанию. Не задавайте `ONVIF_EMU_AUTH__MODE=none` вне изолированной тестовой сети.
- HTTP/RTSP по умолчанию не шифруются. Для недоверенной сети используйте VPN, доверенную сеть либо корректно настроенный TLS reverse proxy.
- Делайте резервные копии `DATA_HOST_DIR` и тех каталогов конфигурации и видео, которые нужно сохранить.

## Ссылки

- Исходный код и релизы: [github.com/vibic777/onvif-emulator](https://github.com/vibic777/onvif-emulator)
- Документация: [документация проекта](https://github.com/vibic777/onvif-emulator/tree/main/docs)
- Лицензия: [MIT](https://github.com/vibic777/onvif-emulator/blob/main/LICENSE)
