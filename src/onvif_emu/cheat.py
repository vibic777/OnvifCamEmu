from __future__ import annotations

CHEATS = {
    "onvif": """ONVIF services ↔ GUI ↔ CLI
Device     GUI: Device          --device-manufacturer --device-model --device-hostname --device-timezone --scopes
Media      GUI: Channels        --channels --channel N:source=... --rtsp-port
PTZ        GUI: PTZ             --ptz-pan-limit --ptz-tilt-limit --ptz-zoom-limit --ptz-pan-speed
Imaging    GUI: Imaging         --imaging-brightness --imaging-contrast ...
OSD        GUI: OSD             --osd on|off --osd-style --osd-color --osd-position
Events     GUI: Events          --events timer|motion|manual --event-hold
Analytics  GUI: Analytics       --analytics --analytics-max-objects
Security   GUI: Security        --auth digest|basic|wsse|none --user --password
System     GUI: System          --host --onvif-port --auto-port --log-level -L --log-dir --video-dir
Anything   (any GUI/config path) --set PATH=VALUE   (list: --list-settings, result: --print-config)
""",
    "launch": """Typical launch commands (copy-paste)

# 1 channel from a file
onvif-emulator --no-gui --channel 1:source=./clip.mp4,encoder=h264 --auth none

# restream an existing RTSP camera
onvif-emulator --channel 1:source=rtsp://192.168.1.20:554/stream1,transcode=false

# 8 channels of test pattern
onvif-emulator --channels 8 --auth digest --user admin --password admin

# long-lived server + web GUI (configure in the browser)
onvif-emulator --service --open-browser

# anonymous, no web GUI
onvif-emulator --headless --auth none --onvif-port 8080 --rtsp-port 8554

# no discovery (lab with blocked multicast)
onvif-emulator --no-discovery --no-gui

# other port, next free one if it is busy or reserved by Windows
onvif-emulator --service --onvif-port 18080 --auto-port

# 3 devices x 3 profiles, timers, analytics on device 2 only (see docs/FLEET.md)
onvif-emulator --service --devices 3 --onvif-port 8080-8082 --rtsp-port 554-556 --streams 3 --rule "kind=tamper,every=30s" --rule "kind=analytics,device=2,every=1m"

# events: fixed period, random range (units s/m/h), relay and dry contact
onvif-emulator --rule "kind=motion,every=10s" --rule "kind=relay,target=relay_1,random=1-100m" --rule "kind=contact,random=5-200s"

# logging: pick levels, file and rotation
onvif-emulator --log-level INFO,ERROR,CRITICAL -L ./emu.log --log-max-mb 20 --log-backups 5
""",
    "events": """Event topics
tns1:VideoSource/MotionAlarm              GUI button «Motion» / --events timer
tns1:VideoSource/Tampering                GUI button «Tamper»
tns1:RuleEngine/FieldDetector/ObjectsInside  analytics simulator
tns1:Device/Trigger/DigitalInput          optional
""",
    "exit": """CLI exit codes
0  OK
1  Config error (validation / missing file)
2  Port in use
3  Missing dependency (FFmpeg / MediaMTX) — doctor only; run still starts
4  Runtime error
5  Usage error
""",
    "odm": """ODM test matrix
[ ] Discovery finds EMU-CAM-1
[ ] Connect digest / basic / wsse / none
[ ] Live view main + sub
[ ] Snapshot
[ ] PTZ panel + GetStatus updates
[ ] Presets set/goto
[ ] Event Viewer motion + tamper
[ ] Analytics modules listed
""",
}


def print_cheat(topic: str | None = None) -> str:
    if topic and topic in CHEATS:
        return CHEATS[topic].strip() + "\n"
    if topic:
        return f"Unknown cheat-sheet topic {topic!r}. Topics: {', '.join(CHEATS)}\n"
    return "\n\n".join(v.strip() for v in CHEATS.values()) + "\n"
