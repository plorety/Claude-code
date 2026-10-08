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

## Get the .exe

- **Download:** every push is built on GitHub's Windows machines. Open the repo's
  **Actions** tab → latest **Build Windows exe** run → download the **FPSToolkit** artifact (a zip
  containing `FPSToolkit.exe`).
- **Build it yourself:** double-click **`build.bat`**. The exe ends up in `dist\FPSToolkit.exe`.

The exe is a single file with Python built in, so it runs without installing anything. It asks for
administrator rights when it starts. Some antivirus programs wrongly flag new PyInstaller exes
because they're unsigned. If that happens, building it yourself with `build.bat` gives you an exe
made from the code you can read here.

## Modes

Pick one on the Home page. Switching modes also undoes the tweaks the new mode doesn't use, so
you can move between them freely.

| Mode | Turns on | Trade-off |
| --- | --- | --- |
| **Low** | Game Mode, background game recording off | None |
| **Balanced** | Low + Ultimate Performance power plan + Xbox Game Bar overlay off | More power at idle; laptops run warmer |
| **Extreme** | Balanced + GPU hardware scheduling + Store apps blocked in the background + High CPU priority for Fortnite | Store app notifications stop; Discord audio may crackle under full CPU load; needs a restart |

Extreme gives steadier frames and a few % more FPS. It's not a miracle, and it still leaves out
anything unsafe (see below).

## What's inside

| Page | What it does |
| --- | --- |
| Home | Your PC specs, the three modes, live CPU/RAM graphs, connection test |
| Windows | Ultimate Performance power plan, Game Mode, turn off background game recording, mouse acceleration off, GPU hardware scheduling, Game Bar overlay off, Store apps in the background, Fortnite CPU priority, refresh-rate check |
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
