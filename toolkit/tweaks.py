"""Toggleable tweaks. Each one can report whether it is applied, apply itself and undo itself.

Every value is backed up before the first change, so "Revert" puts back exactly what you had.
"""

from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass
from typing import Callable

from .winutils import (
    IS_WINDOWS, REG_DWORD, REG_SZ, STATE, reg_delete, reg_read, reg_write, require_windows, run,
)

Log = Callable[[str], None]


@dataclass
class RegSetting:
    hive: str
    path: str
    name: str
    value: int | str
    kind: int = REG_DWORD
    default: int | str | None = None  # Windows default, used if no backup exists; None = delete

    @property
    def key(self) -> str:
        return f"{self.hive}\\{self.path}\\{self.name}"


class Tweak:
    """Base class. `impact` is an honest label shown on the card."""

    def __init__(self, id: str, title: str, description: str, impact: str, *,
                 restart: bool = False, recommended: bool = False) -> None:
        self.id = id
        self.title = title
        self.description = description
        self.impact = impact
        self.restart = restart
        self.recommended = recommended

    def status(self) -> bool | None:
        return None

    def apply(self, log: Log) -> None:
        raise NotImplementedError

    def revert(self, log: Log) -> None:
        raise NotImplementedError


class RegistryTweak(Tweak):
    def __init__(self, *args, settings: list[RegSetting],
                 after: Callable[[], None] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.settings = settings
        self.after = after

    def status(self) -> bool | None:
        if not IS_WINDOWS:
            return None
        for s in self.settings:
            current = reg_read(s.hive, s.path, s.name)
            if current is None or str(current[0]) != str(s.value):
                return False
        return True

    def apply(self, log: Log) -> None:
        require_windows()
        backup = STATE.get("registry_backup", {})
        for s in self.settings:
            if s.key not in backup:
                current = reg_read(s.hive, s.path, s.name)
                backup[s.key] = _to_json(current)
        STATE.set("registry_backup", backup)
        for s in self.settings:
            reg_write(s.hive, s.path, s.name, s.value, s.kind)
            log(f"    {s.key} = {s.value}")
        if self.after:
            self.after()

    def revert(self, log: Log) -> None:
        require_windows()
        backup = STATE.get("registry_backup", {})
        for s in self.settings:
            if s.key in backup:
                saved = backup.pop(s.key)
                if saved is None:
                    reg_delete(s.hive, s.path, s.name)
                    log(f"    {s.key} removed (it did not exist before)")
                else:
                    reg_write(s.hive, s.path, s.name, _from_json(saved), saved["kind"])
                    log(f"    {s.key} restored to {saved['value']}")
            elif s.default is None:
                reg_delete(s.hive, s.path, s.name)
                log(f"    {s.key} reset to Windows default")
            else:
                reg_write(s.hive, s.path, s.name, s.default, s.kind)
                log(f"    {s.key} reset to Windows default ({s.default})")
        STATE.set("registry_backup", backup)
        if self.after:
            self.after()


def _to_json(current):
    if current is None:
        return None
    value, kind = current
    if isinstance(value, bytes):
        return {"hex": value.hex(), "kind": kind, "value": value.hex()}
    return {"value": value, "kind": kind}


def _from_json(saved):
    if "hex" in saved:
        return bytes.fromhex(saved["hex"])
    return saved["value"]


# --------------------------------------------------------------------------- power plan

ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-cf3e8b62e9a4"
BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
GUID_RE = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.IGNORECASE)


def active_power_scheme() -> str | None:
    _, out = run(["powercfg", "/getactivescheme"])
    match = GUID_RE.search(out)
    return match.group(0).lower() if match else None


class PowerPlanTweak(Tweak):
    def status(self) -> bool | None:
        if not IS_WINDOWS:
            return None
        saved = STATE.get("power_plan")
        return bool(saved) and active_power_scheme() == saved["applied"]

    def _ultimate_plan(self, log: Log) -> str | None:
        _, listing = run(["powercfg", "/list"])
        known = STATE.get("ultimate_plan_guid")
        if known and known in listing.lower():
            return known
        rc, out = run(["powercfg", "-duplicatescheme", ULTIMATE])
        guids = [g.lower() for g in GUID_RE.findall(out) if g.lower() != ULTIMATE]
        if rc != 0 or not guids:
            log("    Ultimate Performance is not available on this PC, using High Performance")
            return None
        STATE.set("ultimate_plan_guid", guids[0])
        return guids[0]

    def apply(self, log: Log) -> None:
        previous = active_power_scheme()
        target = self._ultimate_plan(log) or HIGH_PERFORMANCE
        rc, out = run(["powercfg", "/setactive", target])
        if rc != 0 and target != HIGH_PERFORMANCE:
            target = HIGH_PERFORMANCE
            rc, out = run(["powercfg", "/setactive", target])
        if rc != 0:
            raise RuntimeError(out.strip() or "powercfg failed")
        saved = STATE.get("power_plan")
        STATE.set("power_plan", {
            "previous": saved["previous"] if saved else previous,
            "applied": target,
        })
        log(f"    active power plan: {target}")

    def revert(self, log: Log) -> None:
        saved = STATE.get("power_plan")
        target = (saved or {}).get("previous") or BALANCED
        rc, _ = run(["powercfg", "/setactive", target])
        if rc != 0:
            target = BALANCED
            run(["powercfg", "/setactive", target])
        STATE.pop("power_plan")
        log(f"    active power plan: {target}")


# --------------------------------------------------------------------------- mouse

MOUSE_PATH = r"Control Panel\Mouse"


def _push_mouse_settings() -> None:
    """Apply the registry mouse values right away instead of after sign-out."""
    def read(name: str, fallback: int) -> int:
        current = reg_read("HKCU", MOUSE_PATH, name)
        try:
            return int(current[0]) if current else fallback
        except ValueError:
            return fallback

    values = (ctypes.c_int * 3)(read("MouseThreshold1", 6), read("MouseThreshold2", 10),
                                read("MouseSpeed", 1))
    SPI_SETMOUSE, SPIF_UPDATE_AND_BROADCAST = 0x0004, 0x03
    ctypes.windll.user32.SystemParametersInfoW(SPI_SETMOUSE, 0, values, SPIF_UPDATE_AND_BROADCAST)


# --------------------------------------------------------------------------- the list

TWEAKS: list[Tweak] = [
    PowerPlanTweak(
        "power_plan", "Ultimate Performance power plan",
        "Stops the CPU from dropping into deep power-saving states between frames, "
        "which smooths out frame times. Revert puts your old plan back. "
        "On a laptop this uses more battery and runs warmer.",
        "Real gain", recommended=True,
    ),
    RegistryTweak(
        "game_mode", "Turn on Game Mode",
        "Windows gives the game priority and holds back Windows Update installs and "
        "notifications while you play. On by default on most PCs; this makes sure.",
        "Small gain", recommended=True,
        settings=[
            RegSetting("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1),
            RegSetting("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1),
        ],
    ),
    RegistryTweak(
        "game_dvr", "Turn off background game recording",
        "Stops Xbox Game Bar from constantly recording the last few minutes of gameplay "
        "in the background, which costs GPU encoder time and disk writes.",
        "Real gain", recommended=True,
        settings=[
            RegSetting("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", 0, default=1),
            RegSetting("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                       "AppCaptureEnabled", 0),
        ],
    ),
    RegistryTweak(
        "mouse_accel", "Turn off mouse acceleration",
        "Turns off 'Enhance pointer precision' so the same hand movement always turns "
        "your camera the same distance. Better muscle memory for aiming. "
        "Doesn't change FPS.",
        "Preference",
        settings=[
            RegSetting("HKCU", MOUSE_PATH, "MouseSpeed", "0", REG_SZ, default="1"),
            RegSetting("HKCU", MOUSE_PATH, "MouseThreshold1", "0", REG_SZ, default="6"),
            RegSetting("HKCU", MOUSE_PATH, "MouseThreshold2", "0", REG_SZ, default="10"),
        ],
        after=_push_mouse_settings,
    ),
    RegistryTweak(
        "hags", "GPU hardware scheduling",
        "(Hardware-accelerated GPU scheduling.) Lets the graphics card manage its own memory queue. Needed for DLSS Frame "
        "Generation; for everything else the difference is small and can go either "
        "way, so test it in your own game.",
        "Small gain", restart=True,
        settings=[
            RegSetting("HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                       "HwSchMode", 2),
        ],
    ),
]
