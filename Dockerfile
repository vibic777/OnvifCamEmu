# Build the pinned MediaMTX release separately, then copy only its executable into
# the smaller application image. Override MEDIAMTX_IMAGE to update the server.
ARG MEDIAMTX_IMAGE=bluenviron/mediamtx:1.20.1
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${MEDIAMTX_IMAGE} AS mediamtx

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid app --create-home --shell /usr/sbin/nologin app \
    && install -d -o app -g app /app/config /app/data /app/video

COPY --from=mediamtx --chmod=0755 /mediamtx /usr/local/bin/mediamtx

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV ONVIF_EMU_HOST=0.0.0.0 \
    ONVIF_EMU_STATE_PATH=/app/data/state.json \
    ONVIF_EMU_VIDEO_DIR=/app/video \
    ONVIF_EMU_LOGGING__DIR=/app/data/logs

EXPOSE 8080/tcp 8554/tcp 8888/tcp 8889/tcp \
    3702/udp 8000/udp 8001/udp 8189/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; p=os.getenv('ONVIF_EMU_ONVIF_PORT','8080'); urllib.request.urlopen(f'http://127.0.0.1:{p}/health',timeout=4)" || exit 1

USER app
ENTRYPOINT ["/bin/sh", "-c", "umask 077; exec onvif-emulator \"$@\"", "--"]
CMD ["--service", "--host", "0.0.0.0"]
