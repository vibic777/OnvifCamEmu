# Docker Desktop smoke deployment (Windows)

This disposable deployment runs on Windows through Docker Desktop and appears there as **OnvifCamEmu**. It publishes HTTP and RTSP on all host interfaces so ONVIF Device Manager can reach both localhost and the LAN IP, and publishes WS-Discovery on its required standard UDP port 3702. It writes persistent test state under this directory. Windows Firewall may still need to allow the application/container traffic for LAN discovery.

From the repository root in PowerShell:

```powershell
.\docker-desktop-smoke\test.ps1
```

The script creates an ignored `.env.docker` with a random password, builds/starts the image, waits for health, then checks `/health`, Digest-authenticated ONVIF SOAP, a JPEG snapshot, and the H.264 RTSP stream. Image build is cached after its first run.

The GUI is at `http://127.0.0.1:18080/` and `http://192.168.15.100:18080/`. The user is `admin`; the generated password is stored locally in `docker-desktop-smoke/.env.docker`. For the current Windows test, `V:\TestZone\OnvifEmu\video` is mounted read/write at `/app/video`, so its clips appear in the channel file dropdown and uploads can be saved there. This path is passed only to the current Compose process and is not stored in `.env.docker`. The LAN address is likewise passed only for this run. Optional source clips in `docker-desktop-smoke/media` are mounted read-only at `/media`.

Ports, bind address, advertised host, mounted config/data/media folders, config filename, image tag/base images and UID/GID are controlled by `.env.docker`. Set host ports there; the container-side MediaMTX UDP ports remain fixed at 8000/8001/8189. The required WS-Discovery destination inside the service is UDP 3702; `ONVIF_DISCOVERY_PORT` changes only the published Windows host port. Forwarding 3702 is required for multicast discovery, but Windows cannot bind that port while another process (such as ODM itself) owns it. Other application settings can be passed using supported `ONVIF_EMU_*` variables in `.env.docker`.

Inspect with:

```powershell
docker compose --env-file docker-desktop-smoke/.env.docker -f docker-desktop-smoke/compose.yml ps
docker compose --env-file docker-desktop-smoke/.env.docker -f docker-desktop-smoke/compose.yml logs -f
```

Stop the container and network while preserving test data:

```powershell
docker compose --env-file docker-desktop-smoke/.env.docker -f docker-desktop-smoke/compose.yml down
```

To start the already-built release archive instead of rebuilding, load it first with `docker load -i dist/onvif-emulator-v1.8.2-docker-linux-amd64.tar`, then remove the `build:` block from `compose.yml` for that run.
