"""Several emulated ONVIF devices in one process (a "fleet").

The root config (the ``onvif-emulator.conf`` file) is both device 1 and the shared template.
``fleet.count`` / ``fleet.onvif_ports`` / ``fleet.rtsp_ports`` say how many devices run and on which ports;
``fleet.devices[i]`` overrides any setting of device ``i + 2``. Event rules carry a ``device`` number
(0 = every device), so "tamper detector on all, analytics on one" is just two rules.

Editing a device in its own web GUI is written back: device 1 edits the shared root, the others
edit their override block. The saved file therefore reproduces the whole fleet on the next start.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from onvif_emu.config import merge_dict
from onvif_emu.models import AppConfig, FleetConfig

if TYPE_CHECKING:
    from onvif_emu.app import AppFacade

log = logging.getLogger(__name__)

PORT_STRIDE = 10  # MediaMTX API/HLS/WebRTC ports of device N sit N*10 above device 1's


def parse_ports(spec: str, base: int, count: int) -> list[int]:
    """'8080-8089' / '8080,8085,8090' / '' (=> base, base+1, ...) -> exactly ``count`` ports."""
    text = (spec or "").strip()
    ports: list[int] = []
    if text:
        for chunk in text.replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                lo_s, hi_s = chunk.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
                if hi < lo:
                    lo, hi = hi, lo
                if not 1 <= lo <= hi <= 65535:
                    raise ValueError(f"port range out of bounds: {chunk}")
                ports.extend(range(lo, min(hi + 1, lo + max(0, count - len(ports)))))
            else:
                ports.append(int(chunk))
    if not ports:
        ports = [base]
    while len(ports) < count:
        ports.append(ports[-1] + 1)
    bad = [p for p in ports[:count] if not 1 <= p <= 65535]
    if bad:
        raise ValueError(f"port out of range: {bad[0]}")
    return ports[:count]


def device_count(root: AppConfig) -> int:
    """Number of devices: the larger of fleet.count and the length of a port range."""
    f = root.fleet
    n = f.count
    for spec, base in ((f.onvif_ports, root.onvif_port), (f.rtsp_ports, root.network.rtsp_port)):
        if spec.strip():
            n = max(n, len(parse_ports(spec, base, 1)) if "," not in spec and "-" not in spec else _spec_len(spec))
    return min(n, 256)


def _spec_len(spec: str) -> int:
    total = 0
    for chunk in spec.replace(";", ",").split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            lo, hi = (int(x) for x in chunk.split("-", 1))
            total += abs(hi - lo) + 1
        elif chunk:
            total += 1
    return total


def _dump(cfg: AppConfig) -> dict[str, Any]:
    return json.loads(cfg.model_dump_json())


def derive_device(root: AppConfig, index: int, *, with_overrides: bool = True) -> AppConfig:
    """Config of device ``index`` (1-based) derived from the root template."""
    n = device_count(root)
    onvif_ports = parse_ports(root.fleet.onvif_ports, root.onvif_port, n)
    rtsp_ports = parse_ports(root.fleet.rtsp_ports, root.network.rtsp_port, n)
    d = _dump(root)
    i = index - 1
    d["fleet"] = _dump(AppConfig(fleet=FleetConfig()))["fleet"]  # children do not fleet again
    d["instance"] = index
    d["onvif_port"] = onvif_ports[i]
    d["network"]["rtsp_port"] = rtsp_ports[i]
    for key in ("api_port", "hls_port", "webrtc_port"):
        d["mediamtx"][key] = min(65535, d["mediamtx"][key] + i * PORT_STRIDE)
    if i:
        dev = d["device"]
        dev["serial"] = f"{root.device.serial}-{index}"
        dev["hardware_id"] = f"{root.device.hardware_id}-{index}"
        dev["hostname"] = f"{root.device.hostname}-{index}"
        dev["name"] = f"{root.device.name} {index}"
        if root.state_path:
            p = Path(root.state_path)
            d["state_path"] = str(p.with_name(f"{p.stem}_{index}{p.suffix}"))
        else:
            d["state_path"] = str(_state_dir() / f"state_{index}.json")
        d["scopes"] = []  # regenerate from this device's name/location
    rules = [r for r in d["diag"]["rules"] if r.get("device", 0) in (0, index)]
    d["diag"]["rules"] = rules
    if with_overrides and i and i - 1 < len(root.fleet.devices):
        over = copy.deepcopy(root.fleet.devices[i - 1])
        patches = over.pop("channels_patch", None)
        d = merge_dict(d, over)
        if patches:  # {"1": {"ptz_enabled": False}}: touch single channels without repeating the list
            for ch in d["channels"]:
                p = patches.get(str(ch["id"])) or patches.get(ch["id"])
                if p:
                    ch.update(merge_dict(ch, p))
        d["fleet"] = _dump(AppConfig(fleet=FleetConfig()))["fleet"]
        d["instance"] = index
    dev = AppConfig.model_validate(d)
    # one-shot account inputs (--password, --add-user, --reset-admin) are never dumped: hand them over as they are
    dev.users = [u.model_copy() for u in root.users]
    dev.auth.username, dev.auth.password = root.auth.username, root.auth.password
    return dev


def _state_dir() -> Path:
    from onvif_emu.config import beside_exe_dir

    p = beside_exe_dir() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_fleet(root: AppConfig) -> list[AppConfig]:
    return [derive_device(root, i) for i in range(1, device_count(root) + 1)]


def dict_diff(base: Any, new: Any) -> Any:
    """Recursive 'what is different in new': dicts by key, everything else whole. None = no difference."""
    if isinstance(base, dict) and isinstance(new, dict):
        out = {}
        for k, v in new.items():
            if k not in base:
                out[k] = v
                continue
            d = dict_diff(base[k], v)
            if d is not None:
                out[k] = d
        return out or None
    return None if base == new else new


class Fleet:
    """Owns the facades of all devices and the root config they were derived from."""

    def __init__(self, root: AppConfig, root_path: Path | None = None):
        self.root = root
        self.root_path = root_path
        self.facades: list[AppFacade] = []
        self._tasks: list[asyncio.Task] = []
        self._exit_codes: dict[int, int] = {}
        self.open_browser = False
        self.governor = None

    # --------------------------------------------------------------- lifecycle
    async def run(self, *, open_browser: bool = False, tray: bool = False) -> int:
        from onvif_emu.app import AppFacade

        self.open_browser = open_browser
        cfgs = build_fleet(self.root)
        log.info("Fleet: %s device(s)", len(cfgs))
        for cfg in cfgs:
            fac = AppFacade(cfg, self.root_path if cfg.instance == 1 else None)
            fac.runtime.save_hook = self._make_hook(cfg.instance)
            fac.runtime.fleet = self
            self.facades.append(fac)
        from onvif_emu.limits import Governor

        self.governor = Governor([f.runtime for f in self.facades])
        self.governor.start()
        self._tasks = [
            asyncio.create_task(
                f.run(open_browser=open_browser and f.cfg.instance == 1, tray=tray and f.cfg.instance == 1),
                name=f"device-{f.cfg.instance}",
            )
            for f in self.facades
        ]
        try:
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            raise
        finally:
            await self.governor.stop()
        code = 0
        for r in results:
            if isinstance(r, int) and r and not code:
                code = r
        return code

    def status(self) -> list[dict[str, Any]]:
        out = []
        for f in self.facades:
            rt = f.runtime
            out.append(
                {
                    "instance": f.cfg.instance,
                    "name": f.cfg.device.name,
                    "serial": f.cfg.device.serial,
                    "onvif_port": f.cfg.onvif_port,
                    "rtsp_port": f.cfg.network.rtsp_port,
                    "channels": len(f.cfg.channels),
                    "ptz_channels": len(rt.ptz_channels()),
                    "url": f"http://{rt.public_host()}:{f.cfg.onvif_port}/",
                    "alive": rt.health()["status"] == "ok",
                }
            )
        return out

    # ------------------------------------------------------------------ saving
    def _make_hook(self, index: int):
        def hook(cfg: AppConfig) -> None:
            self.absorb(index, cfg)
            self.save()

        return hook

    def absorb(self, index: int, cfg: AppConfig) -> None:
        """Fold a device's edited config back into the root template / its override block."""
        new = _dump(cfg)
        root = _dump(self.root)
        others = [r for r in root["diag"]["rules"] if r.get("device", 0) not in (0, index)]
        mine = new["diag"]["rules"]
        if index == 1:
            fleet_block = root["fleet"]
            new["fleet"] = fleet_block
            new["instance"] = 1
            new["diag"]["rules"] = others + mine
            self.root = AppConfig.model_validate(new)
            return
        # derive_device() sets a suffix on a few identity fields; diff against the un-overridden derivation
        base = _dump(derive_device(self.root, index, with_overrides=False))
        overlay = dict_diff(base, new) or {}
        for k in ("fleet", "instance"):
            overlay.pop(k, None)
        diag = overlay.get("diag")
        if isinstance(diag, dict):
            diag.pop("rules", None)
            if not diag:
                overlay.pop("diag")
        # rules created in this device's GUI belong to this device only
        for r in mine:
            if r.get("device", 0) == 0 and not any(o.get("id") == r.get("id") for o in root["diag"]["rules"]):
                r["device"] = index
        keep_shared = [r for r in root["diag"]["rules"] if r.get("device", 0) == 0 and any(m.get("id") == r["id"] for m in mine)]
        shared_removed = {r["id"] for r in root["diag"]["rules"] if r.get("device", 0) == 0} - {m["id"] for m in mine}
        root_rules = [r for r in root["diag"]["rules"] if r["id"] not in shared_removed and r.get("device", 0) != index]
        root_rules += [m for m in mine if m.get("device", 0) == index]
        del keep_shared
        root["diag"]["rules"] = root_rules
        devices = root["fleet"]["devices"]
        while len(devices) < index - 1:
            devices.append({})
        devices[index - 2] = overlay
        self.root = AppConfig.model_validate(root)

    def save(self) -> None:
        if not self.root_path:
            return
        try:
            _write_root(self.root, self.root_path)
        except OSError as exc:
            log.warning("Cannot save %s: %s", self.root_path, exc)


def _write_root(cfg: AppConfig, path: Path) -> None:
    if path.suffix.lower() == ".conf":
        from onvif_emu.confdump import write_conf

        write_conf(cfg, path, cfg.language)
    elif path.suffix.lower() == ".json":
        path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
    else:
        from onvif_emu.config import dump_yaml

        dump_yaml(cfg, path)
