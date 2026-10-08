"""One-shot actions: cleanup, repair, diagnostics and safety."""

from __future__ import annotations

import ctypes
import os
import shutil
import socket
import statistics
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import sysinfo
from .winutils import (
    APP_DIR, REG_DWORD, open_target, powershell, reg_delete, reg_read, reg_write,
    require_windows, run,
)

Log = Callable[[str], None]


@dataclass
class Action:
    id: str
    title: str
    description: str
    impact: str
    button: str
    func: Callable[[Log], str | None]
    confirm: str | None = None
    restart: bool = False


# --------------------------------------------------------------------------- helpers

def _delete_contents(folder: Path) -> tuple[int, int]:
    """Delete everything inside a folder. Returns (bytes freed, items skipped because in use)."""
    freed = skipped = 0
    if not folder.is_dir():
        return 0, 0
    for entry in os.scandir(folder):
        path = Path(entry.path)
        try:
            if entry.is_dir(follow_symlinks=False):
                size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                shutil.rmtree(path)
            else:
                size = entry.stat(follow_symlinks=False).st_size
                path.unlink()
            freed += size
        except OSError:
            skipped += 1
    return freed, skipped


def _mb(size: int) -> str:
    return f"{size / 1024 ** 2:,.0f} MB"


def _opener(target: str) -> Callable[[Log], None]:
    def go(log: Log) -> None:
        open_target(target)
        log(f"    opened {target}")
    return go


def _link(url: str) -> Callable[[Log], None]:
    def go(log: Log) -> None:
        webbrowser.open(url)
        log(f"    opened {url}")
    return go


# --------------------------------------------------------------------------- network

def connection_test(log: Log) -> str:
    """Time TCP handshakes to big public servers: shows ping, jitter and drops."""
    summary = []
    for label, host in (("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8")):
        times, failed = [], 0
        for _ in range(20):
            start = time.perf_counter()
            try:
                with socket.create_connection((host, 443), timeout=2):
                    pass
                times.append((time.perf_counter() - start) * 1000)
            except OSError:
                failed += 1
            time.sleep(0.1)
        if not times:
            log(f"    {label}: no response")
            summary.append(f"{label}: no response")
            continue
        avg = statistics.mean(times)
        jitter = statistics.mean(abs(a - b) for a, b in zip(times, times[1:])) if len(times) > 1 else 0
        log(f"    {label}: {avg:.0f} ms average, {jitter:.1f} ms jitter, {failed}/20 failed")
        summary.append(f"{label}: {avg:.0f} ms · jitter {jitter:.0f} ms · {failed}/20 lost")

    kind = sysinfo.connection_type()
    log(f"    connection type: {kind}")
    if kind == "Wi-Fi":
        log("    tip: an Ethernet cable is the single best fix for lag spikes and packet loss")
    log("    for your real ping to Fortnite's servers, turn on 'Net Debug Stats' in the game's HUD settings")
    return "\n".join(summary + [f"Connection: {kind}"])


def flush_dns(log: Log) -> None:
    rc, out = run(["ipconfig", "/flushdns"])
    if rc != 0:
        raise RuntimeError(out.strip())
    log("    DNS cache cleared")


def reset_network(log: Log) -> None:
    for args in (["netsh", "winsock", "reset"], ["netsh", "int", "ip", "reset"]):
        rc, out = run(args)
        log(f"    {' '.join(args)}: {'ok' if rc == 0 else 'failed'}")
        if rc != 0:
            log("    " + out.strip().replace("\n", "\n    "))


# --------------------------------------------------------------------------- cleanup

def clean_temp(log: Log) -> str:
    require_windows()
    folders = {Path(p).resolve() for p in (
        os.environ.get("TEMP"), os.environ.get("TMP"),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp",
    ) if p}
    total = skipped_total = 0
    for folder in sorted(folders):
        freed, skipped = _delete_contents(folder)
        total += freed
        skipped_total += skipped
        log(f"    {folder}: freed {_mb(freed)}" + (f", {skipped} in use (skipped)" if skipped else ""))
    log(f"    total freed: {_mb(total)}")
    return _mb(total)


def empty_recycle_bin(log: Log) -> None:
    require_windows()
    no_confirm_no_ui_no_sound = 0x07
    ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, no_confirm_no_ui_no_sound)
    log("    recycle bin emptied")


def clear_shader_cache(log: Log) -> None:
    require_windows()
    local = Path(os.environ["LOCALAPPDATA"])
    folders = [
        local / "D3DSCache",
        local / "NVIDIA" / "DXCache", local / "NVIDIA" / "GLCache",
        local / "AMD" / "DxCache", local / "AMD" / "DxcCache", local / "AMD" / "GLCache",
    ]
    total = 0
    for folder in folders:
        if folder.is_dir():
            freed, skipped = _delete_contents(folder)
            total += freed
            log(f"    {folder}: freed {_mb(freed)}" + (f", {skipped} in use" if skipped else ""))
    log(f"    total freed: {_mb(total)} — first match may hitch briefly while shaders rebuild")


def repair_windows(log: Log) -> None:
    log("    step 1/2: DISM /RestoreHealth (can take 10+ minutes)…")
    rc, out = run(["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], timeout=3600)
    last = [line.strip() for line in out.splitlines() if line.strip()]
    log("    " + (last[-1] if last else f"DISM exit code {rc}"))
    log("    step 2/2: sfc /scannow (can take 10+ minutes)…")
    rc, out = run(["sfc", "/scannow"], timeout=3600)
    last = [line.strip() for line in out.splitlines() if line.strip() and "%" not in line]
    log("    " + (last[-1] if last else f"sfc exit code {rc}"))


# --------------------------------------------------------------------------- safety

RESTORE_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"
RESTORE_FREQ = "SystemRestorePointCreationFrequency"


def create_restore_point(log: Log) -> None:
    require_windows()
    # Windows only allows one restore point per 24h unless this is 0; set it just for this call.
    previous = reg_read("HKLM", RESTORE_KEY, RESTORE_FREQ)
    reg_write("HKLM", RESTORE_KEY, RESTORE_FREQ, 0, REG_DWORD)
    try:
        rc, out = powershell(
            "$ErrorActionPreference = 'Stop'; "
            "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\"; "
            "Checkpoint-Computer -Description 'FPS Toolkit' -RestorePointType 'MODIFY_SETTINGS'",
            timeout=600,
        )
    finally:
        if previous is None:
            reg_delete("HKLM", RESTORE_KEY, RESTORE_FREQ)
        else:
            reg_write("HKLM", RESTORE_KEY, RESTORE_FREQ, previous[0], previous[1])
    if rc != 0:
        raise RuntimeError(out.strip() or "Checkpoint-Computer failed")
    log("    restore point 'FPS Toolkit' created")


def open_logs(log: Log) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    open_target(str(APP_DIR))
    log(f"    opened {APP_DIR}")


# --------------------------------------------------------------------------- the lists

CONNECTION_TEST = Action(
    "conn_test", "Connection quality test",
    "Measures ping, jitter and dropped connections to Cloudflare and Google, and checks "
    "whether you're on Wi-Fi or a cable. Changes nothing.",
    "Diagnostic", "Run test", connection_test,
)

RESTORE_POINT = Action(
    "restore_point", "Create a restore point",
    "A Windows snapshot of system settings you can roll back to from System Restore "
    "if anything ever goes wrong. Do this before applying tweaks.",
    "Safety", "Create", create_restore_point,
)

NETWORK_ACTIONS = [
    CONNECTION_TEST,
    Action("flush_dns", "Flush DNS cache",
           "Clears saved website lookups. Fixes 'can't connect' errors after network "
           "changes. Doesn't change ping.",
           "Maintenance", "Flush", flush_dns),
    Action("net_reset", "Reset network stack",
           "Repair tool for a broken connection (Windows network settings reset to "
           "defaults). Also clears any static IP or custom DNS you set on your adapter.",
           "Repair", "Reset", reset_network, restart=True,
           confirm="This resets Windows networking to defaults, including any static IP or "
                   "custom DNS on your adapters. You'll need to restart. Continue?"),
]

CLEANUP_ACTIONS = [
    Action("clean_temp", "Clean temporary files",
           "Deletes leftover installer and app temp files from your user and Windows temp "
           "folders. Files that are in use are skipped.",
           "Maintenance", "Clean", clean_temp),
    Action("recycle_bin", "Empty Recycle Bin",
           "Permanently deletes everything in the Recycle Bin.",
           "Maintenance", "Empty", empty_recycle_bin,
           confirm="Permanently delete everything in the Recycle Bin?"),
    Action("startup_apps", "Startup apps",
           "Opens Windows' startup list. Turn off launchers and apps you don't need "
           "running in the background (keep audio, GPU and mouse/keyboard software).",
           "Small gain", "Open", _opener("ms-settings:startupapps")),
    Action("installed_apps", "Uninstall apps",
           "Opens the installed apps list to remove programs you don't use.",
           "Maintenance", "Open", _opener("ms-settings:appsfeatures")),
    Action("disk_cleanup", "Disk Cleanup",
           "Windows' own cleaner for update leftovers, thumbnails and old logs.",
           "Maintenance", "Open", _opener("cleanmgr.exe")),
    Action("repair", "Repair Windows files",
           "Runs DISM and SFC to find and fix corrupted system files. Useful if you get "
           "random crashes. Takes 10–30 minutes.",
           "Repair", "Run", repair_windows,
           confirm="This runs DISM and SFC and can take 10–30 minutes. Continue?"),
]

GPU_ACTIONS = [
    Action("shader_cache", "Clear GPU shader cache",
           "Deletes saved compiled shaders (DirectX, NVIDIA, AMD). Helps after a driver "
           "update or if a game started stuttering. They rebuild automatically.",
           "Maintenance", "Clear", clear_shader_cache),
    Action("graphics_settings", "Per-game GPU preference",
           "Opens Windows Graphics settings. Add FortniteClient-Win64-Shipping.exe and set "
           "it to 'High performance' (matters most on laptops).",
           "Small gain", "Open", _opener("ms-settings:display-advancedgraphics")),
    Action("nvidia_driver", "NVIDIA drivers",
           "Official NVIDIA driver download page. New drivers often include game fixes.",
           "Maintenance", "Open site", _link("https://www.nvidia.com/en-us/drivers/")),
    Action("amd_driver", "AMD drivers",
           "Official AMD driver download page.",
           "Maintenance", "Open site", _link("https://www.amd.com/en/support/download/drivers.html")),
    Action("ddu", "Display Driver Uninstaller (DDU)",
           "Free tool for a fully clean driver reinstall if you have driver crashes. "
           "Run it in Safe Mode, then install the fresh driver.",
           "Repair", "Open site", _link("https://www.wagnardsoft.com/display-driver-uninstaller-ddu-")),
]

WINDOWS_ACTIONS = [
    Action("perf_options", "Visual effects",
           "Opens Performance Options. 'Adjust for best performance' makes menus snappier "
           "on slow PCs; on a gaming PC it makes no FPS difference.",
           "Preference", "Open", _opener("SystemPropertiesPerformance.exe")),
    Action("display_hz", "Monitor refresh rate",
           "Opens advanced display settings. Make sure your refresh rate is set to the "
           "maximum your monitor supports (e.g. 144/240 Hz). Many people are stuck on 60 Hz.",
           "Real gain", "Open", _opener("ms-settings:display-advanced")),
]

SAFETY_ACTIONS = [
    RESTORE_POINT,
    Action("system_restore", "Open System Restore",
           "Roll your PC back to an earlier restore point.",
           "Safety", "Open", _opener("rstrui.exe")),
    Action("logs", "Open log folder",
           "Every change this app makes is written to a log file and backed up in "
           "state.json so it can be reverted.",
           "Safety", "Open", open_logs),
]
