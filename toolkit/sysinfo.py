"""Read-only information about the PC for the Home page."""

from __future__ import annotations

import platform

import psutil

from .winutils import IS_WINDOWS, powershell, reg_read


def cpu_name() -> str:
    if IS_WINDOWS:
        found = reg_read("HKLM", r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                         "ProcessorNameString")
        if found:
            return " ".join(str(found[0]).split())
    return platform.processor() or "Unknown CPU"


def gpus() -> list[tuple[str, str]]:
    """[(name, driver version)] for each graphics adapter."""
    if not IS_WINDOWS:
        return []
    _, out = powershell(
        "Get-CimInstance Win32_VideoController | "
        "ForEach-Object { $_.Name + '|' + $_.DriverVersion }",
        timeout=30,
    )
    result = []
    for line in out.splitlines():
        if "|" in line:
            name, driver = line.split("|", 1)
            result.append((name.strip(), driver.strip()))
    return result


def ram_gb() -> int:
    return round(psutil.virtual_memory().total / 1024 ** 3)


def windows_version() -> str:
    if not IS_WINDOWS:
        return f"{platform.system()} {platform.release()} (preview mode)"
    key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    name = (reg_read("HKLM", key, "ProductName") or ("Windows", 0))[0]
    display = (reg_read("HKLM", key, "DisplayVersion") or ("", 0))[0]
    build = (reg_read("HKLM", key, "CurrentBuild") or ("0", 0))[0]
    if str(build).isdigit() and int(build) >= 22000:
        name = str(name).replace("Windows 10", "Windows 11")  # the registry still says 10
    return f"{name} {display} (build {build})".strip()


def connection_type() -> str:
    """Best guess at whether the PC is on a cable or Wi-Fi."""
    wifi = ethernet = False
    for name, stats in psutil.net_if_stats().items():
        if not stats.isup:
            continue
        lowered = name.lower()
        if any(word in lowered for word in ("wi-fi", "wifi", "wlan", "wireless")):
            wifi = True
        elif lowered.startswith("ethernet") or lowered.startswith("eth") or lowered.startswith("en"):
            ethernet = True
    if ethernet:
        return "Ethernet"
    if wifi:
        return "Wi-Fi"
    return "Unknown"
