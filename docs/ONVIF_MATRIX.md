# ONVIF function matrix

Namespaces: Device `ver10/device/wsdl`, Media `ver10/media/wsdl`, PTZ `ver20/ptz/wsdl`, Imaging `ver20/imaging/wsdl`, Events `ver10/events/wsdl`, Analytics `ver20/analytics/wsdl`. SOAP 1.2 + SOAP 1.1 envelopes.

| Service | Action | Status | Notes |
|---|---|---|---|
| Device | GetSystemDateAndTime | yes | always anonymous; Local+UTC+TZ+DST |
| Device | SetSystemDateAndTime | yes | TZ / NTP flag |
| Device | GetDeviceInformation | yes | GUI fields |
| Device | GetCapabilities / GetServices | yes | all XAddr |
| Device | GetScopes / SetScopes | yes | persistent |
| Device | GetHostname / SetHostname | yes | |
| Device | GetNetworkProtocols / Set | yes | HTTP, RTSP, HTTPS |
| Device | GetNTP / SetNTP | yes | |
| Device | GetUsers / CreateUsers / DeleteUsers / SetUser | yes | UserLevel Administrator / Operator / User; admin cannot be deleted; accounts in data/users.json (encrypted) |
| Device | SystemReboot | yes | 3 s then stop |
| Device | GetNetworkInterfaces | stub | static IPv4 |
| Media | GetProfiles / GetProfile | yes | one profile per channel + sub |
| Media | GetVideoSources / Configurations | yes | |
| Media | GetVideoEncoderConfigurations | yes | H264/H265/JPEG |
| Media | GetStreamUri | yes | `rtsp://host:8554/chN` |
| Media | GetSnapshotUri | yes | `/snapshot/{id}.jpg` |
| Media | GetAudioSources | stub | empty |
| PTZ | GetNodes / GetNode | yes | ranges from config |
| PTZ | AbsoluteMove / RelativeMove / ContinuousMove / Stop | yes | live servo |
| PTZ | GetStatus | yes | pan/tilt degrees, zoom 0..1 |
| PTZ | GetPresets / SetPreset / RemovePreset / GotoPreset | yes | JSON persist |
| PTZ | Preset tours | stub | |
| Imaging | Get/SetImagingSettings / GetOptions | yes | brightness/contrast → FFmpeg eq |
| Imaging | Move / GetStatus (focus) | yes | virtual |
| Events | CreatePullPointSubscription | yes | |
| Events | PullMessages / Renew / Unsubscribe | yes | bounded deque |
| Events | SetSynchronizationPoint | yes | Initialized properties |
| Events | GetEventProperties | yes | Motion, Tamper, Field, DI |
| Analytics | GetVideoAnalyticsConfigurations | yes | |
| Analytics | GetAnalyticsModules / Options | yes | motion, line, field; options also MotionRegion, CountAggregation, Loitering |
| Analytics | GetRules / GetRuleOptions | yes | MotionAlarm, LineCross, Intrusion, MotionRegion, Counting, Loitering |
| Analytics | GetMetadata | extra | BoundingBox list |
| Discovery | Probe / ProbeMatch | yes | UDP 3702 |

Auth modes apply to all services except GetSystemDateAndTime. RTSP uses the same user store (URI may embed `user:pass@`).
