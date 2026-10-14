$ErrorActionPreference = 'Stop'
$envFile = 'docker-desktop-smoke/.env.docker'
$composeFile = 'docker-desktop-smoke/compose.yml'

if (-not (Test-Path $envFile)) {
    $password = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
    @"
PUBLIC_HOST=192.168.15.100
BIND_ADDRESS=0.0.0.0
ONVIF_PORT=18080
RTSP_PORT=18554
ONVIF_DISCOVERY_PORT=13702
HLS_PORT=18888
WEBRTC_PORT=18889
MEDIAMTX_API_PORT=9997
RTSP_RTP_UDP_PORT=18000
RTSP_RTCP_UDP_PORT=18001
WEBRTC_UDP_PORT=18189
CONFIG_HOST_DIR=./config
DATA_HOST_DIR=./data
MEDIA_HOST_DIR=./media
CONFIG_FILE=default.yaml
IMAGE_TAG=1.8.2
PYTHON_IMAGE=python:3.12-slim
MEDIAMTX_IMAGE=bluenviron/mediamtx:1.20.1
PUID=1000
PGID=1000
ONVIF_EMU_AUTH__MODE=digest
ONVIF_EMU_AUTH__USERNAME=admin
ONVIF_EMU_AUTH__PASSWORD=$password
"@ | Set-Content -Path $envFile -Encoding ascii
    Write-Host "Generated local test password in $envFile"
}

$env:VIDEO_HOST_DIR = 'V:/TestZone/OnvifEmu/video'
try {
    docker compose --env-file $envFile -f $composeFile up --build -d
    $composeExit = $LASTEXITCODE
} finally {
    Remove-Item Env:VIDEO_HOST_DIR -ErrorAction SilentlyContinue
}
if ($composeExit -ne 0) { throw 'Compose startup failed' }

$deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 3
    $containerId = docker compose --env-file $envFile -f $composeFile ps -q onvifcamemu
    if ($containerId) { $health = docker inspect --format '{{.State.Health.Status}}' $containerId 2>$null }
    if ($health -eq 'healthy') { break }
    if ((Get-Date) -gt $deadline) {
        docker compose --env-file $envFile -f $composeFile logs
        throw "Container did not become healthy (last state: $health)"
    }
} while ($true)

$onvifPort = [int]((Get-Content $envFile | Where-Object { $_ -like 'ONVIF_PORT=*' }) -replace '^ONVIF_PORT=', '')
$rtspPort = [int]((Get-Content $envFile | Where-Object { $_ -like 'RTSP_PORT=*' }) -replace '^RTSP_PORT=', '')
$username = (Get-Content $envFile | Where-Object { $_ -like 'ONVIF_EMU_AUTH__USERNAME=*' }) -replace '^ONVIF_EMU_AUTH__USERNAME=', ''
$publicHost = (Get-Content $envFile | Where-Object { $_ -like 'PUBLIC_HOST=*' }) -replace '^PUBLIC_HOST=', ''
$healthResponse = Invoke-RestMethod "http://127.0.0.1:$onvifPort/health"
if ($healthResponse.status -ne 'ok') { throw 'Unexpected /health result' }
Write-Host "Health OK; app $($healthResponse.version), MediaMTX $($healthResponse.mediamtx)"

$password = (Get-Content $envFile | Where-Object { $_ -like 'ONVIF_EMU_AUTH__PASSWORD=*' }) -replace '^ONVIF_EMU_AUTH__PASSWORD=', ''
$soap = '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><s:Body><tds:GetDeviceInformation/></s:Body></s:Envelope>'
$soapResponse = curl.exe --silent --show-error --digest --user "$username`:$password" -H 'Content-Type: application/soap+xml' --data-binary $soap "http://127.0.0.1:$onvifPort/onvif/device_service"
if ($LASTEXITCODE -ne 0 -or $soapResponse -notmatch 'GetDeviceInformationResponse') { throw 'ONVIF SOAP check failed' }
Write-Host 'ONVIF SOAP OK'

curl.exe --silent --show-error --fail --digest --user "$username`:$password" "http://127.0.0.1:$onvifPort/snapshot/1.jpg" --output docker-desktop-smoke/snapshot.jpg
if ($LASTEXITCODE -ne 0) { throw 'Snapshot request failed' }
if ((Get-Item docker-desktop-smoke/snapshot.jpg).Length -lt 1000) { throw 'Snapshot is empty or too small' }
$rtsp = "rtsp://${username}:$password@127.0.0.1:${rtspPort}/ch1"
docker compose --env-file $envFile -f $composeFile exec -T onvifcamemu ffprobe -v error -rtsp_transport tcp -select_streams v:0 -show_entries stream=codec_name,width,height -of default=noprint_wrappers=1 $rtsp
if ($LASTEXITCODE -ne 0) { throw 'RTSP stream verification failed' }
Write-Host "Snapshot and RTSP OK; GUI: http://127.0.0.1:$onvifPort/ (LAN: http://${publicHost}:$onvifPort/)"
docker compose --env-file $envFile -f $composeFile ps
docker compose --env-file $envFile -f $composeFile logs --tail 30
Write-Host 'To stop while preserving test data: docker compose --env-file docker-desktop-smoke/.env.docker -f docker-desktop-smoke/compose.yml down'
