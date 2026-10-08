# FPS Toolkit

A free, open-source Windows tweaking panel for smoother frames and lower input delay in
games like Fortnite. It's a transparent alternative to paid "optimization panels": every button
says exactly what it does, how much it really helps, and can be undone.

## Run it

1. Install [Python 3.10+](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"**.
2. Double-click **`run.bat`**. It installs the two requirements (`customtkinter`, `psutil`) and
   starts the app.
3. Accept the Windows UAC prompt. Administrator rights are needed for system-wide settings
   (power plan, GPU scheduling, restore points).

Or from a terminal: `pip install -r requirements.txt` then `python fps_toolkit.py`.

## What's inside

| Page | What it does |
| --- | --- |
| Home | Your PC specs, live CPU/RAM graphs, one-click recommended tweaks, connection test |
| Windows | Ultimate Performance power plan, Game Mode, turn off background game recording, mouse acceleration off, GPU hardware scheduling, refresh-rate check |
| Network | Ping / jitter / packet-loss test, Wi-Fi vs Ethernet check, DNS flush, network reset |
| Cleanup | Temp files, Recycle Bin, startup apps, uninstall apps, Disk Cleanup, DISM + SFC repair |
| GPU | Shader cache clear, per-game GPU preference, official driver links, DDU |
| Fortnite guide | The in-game, BIOS and network settings that make the biggest real difference |
| Safety | Restore point, System Restore, revert all tweaks, log folder |

Each card has an honest label: **Real gain**, **Small gain**, **Preference**, **Maintenance**,
**Diagnostic**, **Repair** or **Safety**.

## Safety

- Before a setting is changed for the first time, its original value is saved to
  `%LOCALAPPDATA%\FPSToolkit\state.json`. **Revert** restores exactly what you had.
- Every action is logged to `%LOCALAPPDATA%\FPSToolkit\toolkit.log`.
- Create a restore point (Home or Safety page) before applying anything.

## What it deliberately doesn't do

Paid panels advertise "1300+ tweaks". Many of them are placebo, and some are harmful. This app
leaves these out on purpose:

- Turning off Spectre/Meltdown mitigations: a security hole for ~0 FPS on modern CPUs.
- Turning off UAC, Windows Update or Defender.
- Turning off "all services": breaks Wi-Fi, Bluetooth, the Store and printing.
- Nagle / TCP / "network throttling" registry edits: Fortnite uses UDP, so they do nothing.
- BCDEdit timer tweaks, forced GPU P-states, disabling preemption: these can make stutter
  and stability worse.

Works on Windows 10 and Windows 11, including 24H2/25H2. On other systems it opens in
preview mode, where the buttons don't change anything.
