"""CustomTkinter interface: sidebar navigation, tweak cards, live CPU/RAM graphs and a log."""

from __future__ import annotations

import getpass
import queue
import threading
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk
import psutil

from . import actions, benchmark, sysinfo
from .actions import Action
from .tweaks import MODE_TWEAK_IDS, MODES, TWEAKS, Mode, Tweak
from .winutils import IS_WINDOWS, WindowsOnlyError, write_log_file

C = {
    "bg": "#0f1013", "sidebar": "#15161a", "card": "#1c1d22", "card_border": "#2a2c33",
    "hover": "#25272e", "text": "#ececf1", "muted": "#8b8e99", "accent": "#3b82f6",
    "accent_hover": "#2563eb", "accent_soft": "#1b2a45", "good": "#22c55e", "bad": "#ef4444",
    "graph_fill": "#16284a", "grid": "#262830", "button2": "#2b2d35", "button2_hover": "#363944",
}

IMPACT = {
    "Real gain": "#15803d", "Small gain": "#1d4ed8", "Preference": "#6d28d9",
    "Maintenance": "#3f4654", "Diagnostic": "#0e7490", "Repair": "#a16207", "Safety": "#15803d",
}

PAGES = [
    ("home", "⌂", "Home"),
    ("benchmark", "⏱", "Benchmark"),
    ("windows", "⊞", "Windows"),
    ("network", "⇅", "Network"),
    ("cleanup", "♻", "Cleanup"),
    ("gpu", "▣", "GPU"),
    ("guide", "★", "Fortnite guide"),
    ("safety", "✚", "Safety"),
]


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


# =========================================================================== widgets

class WrapLabel(ctk.CTkLabel):
    """A label that re-wraps its text when its width changes."""

    def __init__(self, master, **kwargs) -> None:
        kwargs.setdefault("justify", "left")
        kwargs.setdefault("anchor", "w")
        super().__init__(master, wraplength=360, **kwargs)
        self._last = 0
        self._pending = None
        self.bind("<Configure>", self._schedule)

    def _schedule(self, _event) -> None:
        # Rewrapping inside a layout pass makes CTkScrollableFrame recurse forever,
        # so do it shortly after the layout settles instead.
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(40, self._rewrap)

    def _rewrap(self) -> None:
        self._pending = None
        width = max(self.winfo_width() - 8, 120)
        if abs(width - self._last) > 12:
            self._last = width
            self.configure(wraplength=width)


def badge(master, text: str, color: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=f" {text} ", fg_color=color, corner_radius=6,
                        text_color="#ffffff", font=font(11, "bold"), height=20)


class Card(ctk.CTkFrame):
    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color=C["card"], corner_radius=14, border_width=1,
                         border_color=C["card_border"], **kwargs)


class ItemCard(Card):
    """Card for a Tweak (Apply / Revert + status) or an Action (one button)."""

    def __init__(self, master, app: "App", item: Tweak | Action) -> None:
        super().__init__(master)
        self.app = app
        self.item = item
        self.is_tweak = isinstance(item, Tweak)
        self.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=item.title, font=font(15, "bold"), anchor="w",
                     text_color=C["text"]).grid(row=0, column=0, sticky="w")
        col = 1
        if item.restart:
            badge(head, "Restart", "#7c2d12").grid(row=0, column=col, padx=(6, 0))
            col += 1
        badge(head, item.impact, IMPACT.get(item.impact, "#3f4654")).grid(
            row=0, column=col, padx=(6, 0))

        WrapLabel(self, text=item.description, text_color=C["muted"], font=font(12)).grid(
            row=1, column=0, sticky="ew", padx=16)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 14))
        foot.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(foot, text="", font=font(12), anchor="w")
        self.status.grid(row=0, column=0, sticky="w")

        if self.is_tweak:
            self.revert_btn = ctk.CTkButton(
                foot, text="Revert", width=84, height=32, corner_radius=8, font=font(12),
                fg_color=C["button2"], hover_color=C["button2_hover"],
                command=lambda: app.run_tweak(item, apply=False))
            self.revert_btn.grid(row=0, column=1, padx=(6, 0))
            self.apply_btn = ctk.CTkButton(
                foot, text="Apply", width=84, height=32, corner_radius=8, font=font(12, "bold"),
                fg_color=C["accent"], hover_color=C["accent_hover"],
                command=lambda: app.run_tweak(item, apply=True))
            self.apply_btn.grid(row=0, column=2, padx=(6, 0))
            self.set_status(None if IS_WINDOWS else "windows-only")
        else:
            ctk.CTkButton(
                foot, text=item.button, width=100, height=32, corner_radius=8,
                font=font(12, "bold"), fg_color=C["accent"], hover_color=C["accent_hover"],
                command=lambda: app.run_action(item)).grid(row=0, column=1)

    def set_status(self, applied) -> None:
        if applied is True:
            self.status.configure(text="●  Applied", text_color=C["good"])
        elif applied is False:
            self.status.configure(text="○  Not applied", text_color=C["muted"])
        elif applied == "windows-only":
            self.status.configure(text="—  Windows only", text_color=C["muted"])
        else:
            self.status.configure(text="…", text_color=C["muted"])


class ModeCard(Card):
    """One of the Low / Balanced / Extreme presets."""

    def __init__(self, master, app: "App", mode: Mode) -> None:
        super().__init__(master)
        self.mode = mode
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=mode.name, font=font(20, "bold"), anchor="w",
                     text_color=mode.color if mode.id != "low" else C["good"]).grid(
            row=0, column=0, sticky="w")
        self.active_badge = badge(head, "ACTIVE", mode.color)
        self.active_badge.grid(row=0, column=1)
        self.active_badge.grid_remove()

        WrapLabel(self, text=mode.tagline, font=font(12), text_color=C["text"]).grid(
            row=1, column=0, sticky="ew", padx=16)
        includes = "\n".join(f"✓  {t.title}" for t in mode.tweaks)
        ctk.CTkLabel(self, text=includes, font=font(12), text_color=C["muted"], anchor="w",
                     justify="left").grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 0))
        WrapLabel(self, text=f"Trade-off: {mode.cost}", font=font(11),
                  text_color=C["muted"]).grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 0))
        ctk.CTkButton(self, text=f"Use {mode.name} mode", height=36, corner_radius=8,
                      font=font(13, "bold"), fg_color=mode.color,
                      hover_color=C["button2_hover"],
                      command=lambda: app.apply_mode(mode)).grid(
            row=5, column=0, sticky="sew", padx=16, pady=(12, 14))

    def set_active(self, active: bool) -> None:
        if active:
            self.active_badge.grid()
            self.configure(border_color=self.mode.color, border_width=2)
        else:
            self.active_badge.grid_remove()
            self.configure(border_color=C["card_border"], border_width=1)


class UsageGraph(Card):
    def __init__(self, master, name: str) -> None:
        super().__init__(master)
        self.name = name
        self.values: deque[float] = deque([0.0] * 60, maxlen=60)
        self.title = ctk.CTkLabel(self, text=f"{name}", font=font(18, "bold"), anchor="w")
        self.title.pack(fill="x", padx=16, pady=(12, 4))
        self.canvas = tk.Canvas(self, height=130, bg=C["card"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.canvas.bind("<Configure>", lambda _e: self.redraw())

    def push(self, value: float) -> None:
        self.values.append(value)
        self.title.configure(text=f"{self.name}  ({value:.0f}%)")
        self.redraw()

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10 or h < 10:
            return
        for pct in (25, 50, 75):
            y = h - pct / 100 * h
            c.create_line(0, y, w, y, fill=C["grid"], dash=(2, 4))
        step = w / (len(self.values) - 1)
        points = []
        for i, v in enumerate(self.values):
            points += [i * step, h - 2 - v / 100 * (h - 4)]
        c.create_polygon(0, h, *points, w, h, fill=C["graph_fill"], outline="")
        c.create_line(*points, fill=C["accent"], width=2)


# =========================================================================== app

class App(ctk.CTk):
    def __init__(self, admin: bool) -> None:
        super().__init__(fg_color=C["bg"])
        ctk.set_appearance_mode("dark")
        self.admin = admin
        self.title("FPS Toolkit" + ("  —  Administrator" if admin else ""))
        self.geometry("1200x780")
        self.minsize(1000, 640)

        self._events: queue.Queue = queue.Queue()
        self._job_running = False
        self.tweak_cards: list[ItemCard] = []
        self.mode_cards: list[ModeCard] = []
        self.active_mode: str | None = None
        self._runs: list[dict] = []
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.pages: dict[str, ctk.CTkScrollableFrame] = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()
        self._build_pages()
        self.show("home")

        self.log("FPS Toolkit started" + (" as administrator" if admin else ""))
        if IS_WINDOWS and not admin:
            self.log("⚠ Not running as administrator, so system-wide tweaks will fail. "
                     "Restart the app and accept the UAC prompt.")
        if not IS_WINDOWS:
            self.log("Preview mode: this isn't Windows, so buttons won't change anything.")

        psutil.cpu_percent(interval=None)
        self.after(60, self._pump)
        self.after(1000, self._tick_stats)
        threading.Thread(target=self._load_sysinfo, daemon=True).start()
        self.refresh_statuses()


    # ------------------------------------------------------------------ layout

    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=C["sidebar"], corner_radius=0, width=220)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_rowconfigure(len(PAGES) + 2, weight=1)

        ctk.CTkLabel(bar, text="FPS Toolkit", font=font(22, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=22, pady=(24, 0))
        ctk.CTkLabel(bar, text="free · open source · reversible", font=font(11),
                     text_color=C["muted"], anchor="w").grid(
            row=1, column=0, sticky="ew", padx=22, pady=(0, 20))

        for i, (key, icon, label) in enumerate(PAGES):
            btn = ctk.CTkButton(
                bar, text=f"  {icon}    {label}", anchor="w", height=42, corner_radius=10,
                font=font(14), fg_color="transparent", hover_color=C["hover"],
                text_color=C["muted"], command=lambda k=key: self.show(k))
            btn.grid(row=i + 2, column=0, sticky="new", padx=12, pady=2)
            self.nav_buttons[key] = btn

        ok = self.admin
        text = "●  Administrator" if ok else ("●  Not administrator" if IS_WINDOWS else "●  Preview mode")
        ctk.CTkLabel(bar, text=text, font=font(12), anchor="w",
                     text_color=C["good"] if ok else C["bad"]).grid(
            row=len(PAGES) + 3, column=0, sticky="sew", padx=22, pady=(0, 18))

    def _build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=(12, 12))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self.page_holder = ctk.CTkFrame(main, fg_color="transparent")
        self.page_holder.grid(row=0, column=0, sticky="nsew")
        self.page_holder.grid_columnconfigure(0, weight=1)
        self.page_holder.grid_rowconfigure(0, weight=1)

        bottom = Card(main)
        bottom.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bottom.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(bottom, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 0))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Activity", font=font(13, "bold")).grid(row=0, column=0)
        self.job_label = ctk.CTkLabel(top, text="", font=font(12), text_color=C["muted"])
        self.job_label.grid(row=0, column=1, sticky="e", padx=10)
        self.progress = ctk.CTkProgressBar(top, width=140, mode="indeterminate",
                                           progress_color=C["accent"])
        self.progress.grid(row=0, column=2)
        self.progress.grid_remove()
        self.logbox = ctk.CTkTextbox(bottom, height=96, font=ctk.CTkFont(family="Consolas", size=12),
                                     fg_color=C["bg"], text_color="#c9cbd3", corner_radius=10)
        self.logbox.grid(row=1, column=0, sticky="ew", padx=10, pady=(6, 10))
        self.logbox.configure(state="disabled")

    def _new_page(self, key: str) -> ctk.CTkScrollableFrame:
        page = ctk.CTkScrollableFrame(self.page_holder, fg_color="transparent",
                                      scrollbar_button_color=C["button2"])
        page.grid_columnconfigure((0, 1), weight=1, uniform="col")
        self.pages[key] = page
        return page

    def _header(self, page, title: str, subtitle: str) -> int:
        ctk.CTkLabel(page, text=title, font=font(28, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 0))
        WrapLabel(page, text=subtitle, font=font(13), text_color=C["muted"]).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        return 2

    def _section(self, page, row: int, title: str) -> int:
        ctk.CTkLabel(page, text=title.upper(), font=font(11, "bold"), text_color=C["muted"],
                     anchor="w").grid(row=row, column=0, columnspan=2, sticky="ew",
                                      padx=8, pady=(16, 2))
        return row + 1

    def _cards(self, page, row: int, items) -> int:
        for i, item in enumerate(items):
            card = ItemCard(page, self, item)
            card.grid(row=row + i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            if card.is_tweak:
                self.tweak_cards.append(card)
        return row + (len(items) + 1) // 2

    def _text_card(self, page, row: int, col: int, title: str, lines: list[str],
                   span: int = 1) -> None:
        card = Card(page)
        card.grid(row=row, column=col, columnspan=span, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, font=font(15, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        for i, line in enumerate(lines, start=1):
            WrapLabel(card, text=f"•  {line}", font=font(12), text_color=C["muted"]).grid(
                row=i, column=0, sticky="ew", padx=16, pady=2)
        ctk.CTkFrame(card, fg_color="transparent", width=1, height=8).grid(row=len(lines) + 1, column=0)

    # ------------------------------------------------------------------ pages

    def _build_pages(self) -> None:
        self._build_home()
        self._build_benchmark()

        tweaks = {t.id: t for t in TWEAKS}

        page = self._new_page("windows")
        row = self._header(page, "Windows", "Settings that affect frame times and input. "
                                            "Each one shows whether it's applied and can be reverted.")
        row = self._section(page, row, "Performance")
        row = self._cards(page, row, [tweaks["power_plan"], tweaks["game_dvr"],
                                      tweaks["game_mode"], tweaks["hags"]])
        row = self._section(page, row, "Input & display")
        row = self._cards(page, row, [tweaks["mouse_accel"], *actions.WINDOWS_ACTIONS])
        row = self._section(page, row, "Extra (used by Balanced and Extreme modes)")
        row = self._cards(page, row, [tweaks["game_bar_overlay"], tweaks["background_apps"],
                                      tweaks["fortnite_priority"]])

        page = self._new_page("network")
        row = self._header(page, "Network", "Software can't shorten the distance to Fortnite's "
                                            "servers, but it can show you what's wrong. Most lag "
                                            "spikes and packet loss come from Wi-Fi.")
        self._cards(page, row, actions.NETWORK_ACTIONS)

        page = self._new_page("cleanup")
        row = self._header(page, "Cleanup", "Free up space and stop programs you don't need "
                                            "running in the background.")
        self._cards(page, row, actions.CLEANUP_ACTIONS)

        page = self._new_page("gpu")
        row = self._header(page, "GPU", "Drivers and graphics settings. Your in-game settings "
                                        "matter more than anything here, see the Fortnite guide.")
        self.gpu_info = WrapLabel(page, text="Detecting graphics card…", font=font(13),
                                  text_color=C["text"])
        self.gpu_info.grid(row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 4))
        self._cards(page, row + 1, actions.GPU_ACTIONS)

        self._build_guide()

        page = self._new_page("safety")
        row = self._header(page, "Safety", "Everything this app changes is backed up first. "
                                           "Make a restore point before you start.")
        row = self._cards(page, row, actions.SAFETY_ACTIONS)
        revert_all = Action("revert_all", "Revert all tweaks",
                            "Undoes every tweak that's currently applied and restores your "
                            "original settings from the backup.",
                            "Safety", "Revert all", self._revert_all,
                            confirm="Revert every applied tweak to your original settings?")
        self._cards(page, row, [revert_all])

    def _build_home(self) -> None:
        page = self._new_page("home")
        name = getpass.getuser() or "User"
        row = self._header(page, f"Welcome, {name}!",
                           "Free, transparent tweaks for smoother frames and lower input delay. "
                           "No mystery scripts: every button says what it does.")
        row = self._section(page, row, "Overview")

        boxes = ctk.CTkFrame(page, fg_color="transparent")
        boxes.grid(row=row, column=0, columnspan=2, sticky="ew")
        boxes.grid_columnconfigure((0, 1, 2), weight=1, uniform="box")
        row += 1

        pc = Card(boxes)
        pc.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        pc.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(pc, text="Your PC", font=font(15, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 6))
        self.pc_labels = {}
        for i, key in enumerate(("CPU", "GPU", "RAM", "Windows", "Network"), start=1):
            ctk.CTkLabel(pc, text=key, font=font(12, "bold"), text_color=C["muted"],
                         anchor="w", width=72).grid(row=i, column=0, sticky="nw", padx=(16, 4), pady=1)
            lbl = ctk.CTkLabel(pc, text="…", font=font(12), anchor="w", justify="left",
                               wraplength=190)
            lbl.grid(row=i, column=1, sticky="ew", padx=(0, 12), pady=1)
            self.pc_labels[key] = lbl
        ctk.CTkFrame(pc, fg_color="transparent", width=1, height=10).grid(row=9, column=0)

        quick = Card(boxes)
        quick.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        quick.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(quick, text="Quick start", font=font(15, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        WrapLabel(quick, text="1.  Create a restore point\n2.  Run a 'Before' benchmark\n"
                              "3.  Pick a mode below, then benchmark again", font=font(12),
                  text_color=C["muted"]).grid(row=1, column=0, sticky="ew", padx=16)
        ctk.CTkButton(quick, text="Create restore point", height=34, corner_radius=8,
                      fg_color=C["accent"], hover_color=C["accent_hover"], font=font(12, "bold"),
                      command=lambda: self.run_action(actions.RESTORE_POINT)).grid(
            row=2, column=0, sticky="ew", padx=16, pady=(12, 4))
        ctk.CTkButton(quick, text="Open Benchmark", height=34, corner_radius=8,
                      fg_color=C["button2"], hover_color=C["button2_hover"], font=font(12),
                      command=lambda: self.show("benchmark")).grid(
            row=3, column=0, sticky="ew", padx=16, pady=(4, 14))

        conn = Card(boxes)
        conn.grid(row=0, column=2, sticky="nsew", padx=6, pady=6)
        conn.grid_columnconfigure(0, weight=1)
        conn.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(conn, text="Connection", font=font(15, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self.conn_label = WrapLabel(conn, text="Not tested yet. Checks ping, jitter and "
                                               "packet loss.", font=font(12), text_color=C["muted"])
        self.conn_label.grid(row=1, column=0, sticky="new", padx=16)
        ctk.CTkButton(conn, text="Run connection test", height=34, corner_radius=8,
                      fg_color=C["button2"], hover_color=C["button2_hover"], font=font(12),
                      command=lambda: self.run_action(actions.CONNECTION_TEST,
                                                      on_result=self._show_conn)).grid(
            row=3, column=0, sticky="sew", padx=16, pady=(12, 14))

        row = self._section(page, row, "Modes")
        modes = ctk.CTkFrame(page, fg_color="transparent")
        modes.grid(row=row, column=0, columnspan=2, sticky="ew")
        modes.grid_columnconfigure(tuple(range(len(MODES))), weight=1, uniform="mode")
        for i, mode in enumerate(MODES):
            card = ModeCard(modes, self, mode)
            card.grid(row=0, column=i, sticky="nsew", padx=6, pady=6)
            self.mode_cards.append(card)
        row += 1

        row = self._section(page, row, "PC stats")
        self.cpu_graph = UsageGraph(page, "CPU usage")
        self.cpu_graph.grid(row=row, column=0, sticky="nsew", padx=6, pady=6)
        self.ram_graph = UsageGraph(page, "RAM usage")
        self.ram_graph.grid(row=row, column=1, sticky="nsew", padx=6, pady=6)

    def _build_benchmark(self) -> None:
        page = self._new_page("benchmark")
        row = self._header(page, "Benchmark", "Test before and after you change settings, then "
                                              "compare the two runs side by side.")
        self._text_card(page, row, 0, "How to compare fairly", [
            "Run a test and name it 'Before'.",
            "Pick a mode on the Home page (and restart if it says so).",
            "Run the same test again, named 'After'. The comparison appears below.",
            "Results vary a little every run, so only trust clear differences. Running each "
            "test twice helps.",
        ], span=2)
        row += 1

        name_card = Card(page)
        name_card.grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        name_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(name_card, text="Name this run", font=font(13, "bold")).grid(
            row=0, column=0, padx=(16, 12), pady=14)
        self.run_name = ctk.CTkEntry(name_card, height=34, corner_radius=8, font=font(13),
                                     placeholder_text="e.g. Before, After Extreme",
                                     fg_color=C["bg"], border_color=C["card_border"])
        self.run_name.insert(0, "Before")
        self.run_name.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=14)
        row += 1

        row = self._section(page, row, "Run a test")
        system = self._test_card(page, row, 0, "System test", "about 12 s",
                                 "Measures what the tweaks change: how fast Windows wakes up "
                                 "programs, how quickly the CPU ramps up, and background load. "
                                 "Close games and downloads first. No game needed.")
        ctk.CTkButton(system, text="Run system test", height=36, corner_radius=8,
                      font=font(13, "bold"), fg_color=C["accent"], hover_color=C["accent_hover"],
                      command=self._run_system_test).grid(
            row=3, column=0, sticky="sew", padx=16, pady=(12, 14))

        game = self._test_card(page, row, 1, "In-game FPS test", "Fortnite",
                               "Records real frame times while you play, using Intel's free "
                               "PresentMon (it never touches the game). Do the same thing each "
                               "run, like the same Creative map, for a fair comparison.")
        controls = ctk.CTkFrame(game, fg_color="transparent")
        controls.grid(row=3, column=0, sticky="sew", padx=16, pady=(12, 14))
        controls.grid_columnconfigure(1, weight=1)
        self.duration = ctk.CTkSegmentedButton(controls, values=["30 s", "60 s", "120 s"],
                                               font=font(12), height=36,
                                               selected_color=C["accent"],
                                               selected_hover_color=C["accent_hover"])
        self.duration.set("60 s")
        self.duration.grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(controls, text="Run FPS test", height=36, corner_radius=8,
                      font=font(13, "bold"), fg_color=C["accent"], hover_color=C["accent_hover"],
                      command=self._run_game_test).grid(row=0, column=1, sticky="ew")
        row += 1

        row = self._section(page, row, "Compare")
        cmp = Card(page)
        cmp.grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        cmp.grid_columnconfigure(0, weight=1)
        pick = ctk.CTkFrame(cmp, fg_color="transparent")
        pick.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        pick.grid_columnconfigure((0, 2), weight=1, uniform="pick")
        menu_style = dict(height=34, corner_radius=8, font=font(12), fg_color=C["button2"],
                          button_color=C["button2"], button_hover_color=C["button2_hover"],
                          dynamic_resizing=False, command=lambda _v: self._render_compare())
        self.run_a = ctk.CTkOptionMenu(pick, values=["—"], **menu_style)
        self.run_a.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(pick, text="vs", font=font(13, "bold"), text_color=C["muted"]).grid(
            row=0, column=1, padx=12)
        self.run_b = ctk.CTkOptionMenu(pick, values=["—"], **menu_style)
        self.run_b.grid(row=0, column=2, sticky="ew")
        ctk.CTkButton(pick, text="Clear results", width=110, height=34, corner_radius=8,
                      font=font(12), fg_color=C["button2"], hover_color=C["button2_hover"],
                      command=self._clear_results).grid(row=0, column=3, padx=(12, 0))
        self.compare_body = ctk.CTkFrame(cmp, fg_color="transparent")
        self.compare_body.grid(row=1, column=0, sticky="ew", padx=16, pady=(6, 14))
        self._refresh_compare()

    def _test_card(self, page, row: int, col: int, title: str, tag: str, text: str) -> Card:
        card = Card(page)
        card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=title, font=font(16, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w")
        badge(head, tag, IMPACT["Diagnostic"]).grid(row=0, column=1)
        WrapLabel(card, text=text, font=font(12), text_color=C["muted"]).grid(
            row=1, column=0, sticky="ew", padx=16)
        return card

    def _build_guide(self) -> None:
        page = self._new_page("guide")
        row = self._header(page, "Fortnite guide", "The settings that make the biggest real "
                                                   "difference. None of them cost anything.")
        self._text_card(page, row, 0, "In-game settings", [
            "NVIDIA Reflex Low Latency: On + Boost. The biggest input-delay setting there is.",
            "Rendering mode: DirectX 12 or Performance mode. Try both and keep the smoother one.",
            "Frame rate limit: a cap your PC can hold steadily. A stable 240 feels better than "
            "a jumpy 300.",
            "V-Sync off. With G-Sync/FreeSync, cap a few FPS below your refresh rate.",
            "Turn on 'Net Debug Stats' (HUD settings) to see your real ping and packet loss.",
            "Matchmaking region: pick the one with the lowest ping.",
        ])
        self._text_card(page, row, 1, "Hardware & BIOS", [
            "Intel 13th/14th gen CPU (e.g. i9-14900K): update your motherboard BIOS to one with "
            "Intel microcode 0x12B or newer. It fixes a known crash, stutter and degradation "
            "problem.",
            "Turn on XMP / EXPO in the BIOS so your RAM runs at its rated speed.",
            "Plug your monitor into the graphics card, not the motherboard.",
            "Check the Windows refresh rate is your monitor's max (Windows page).",
            "Watch temperatures with HWiNFO. Overheating throttles your FPS.",
        ])
        self._text_card(page, row + 1, 0, "Network", [
            "Use an Ethernet cable. It's the best fix for lag spikes and packet loss.",
            "Ping is mostly the distance to the server. No tweak or program can shrink it.",
            "Pause downloads and streams on your network while playing.",
        ])
        self._text_card(page, row + 1, 1, "What this app won't do (and why)", [
            "Turn off Spectre/Meltdown mitigations: opens security holes for ~0 FPS on modern CPUs.",
            "Turn off UAC, Windows Update or Defender: leaves your PC unprotected.",
            "Turn off 'all services': breaks Wi-Fi, Bluetooth, the Store, printers and more.",
            "Nagle / TCP / 'network throttling' tweaks: Fortnite uses UDP, so they do nothing.",
            "BCDEdit timer tweaks, forced GPU P-states, disabling preemption: can make "
            "stutter and stability worse.",
        ])
        self._text_card(page, row + 2, 0, "Getting better at the game", [
            "Settings remove problems, but they don't make you aim better. Practice does.",
            "Creative aim and edit courses (warm up 10–15 minutes before playing).",
            "Keep the same sensitivity so your muscle memory sticks.",
        ], span=2)

    # ------------------------------------------------------------------ navigation

    def show(self, key: str) -> None:
        for name, page in self.pages.items():
            if name == key:
                page.grid(row=0, column=0, sticky="nsew")
            else:
                page.grid_remove()
        for name, btn in self.nav_buttons.items():
            selected = name == key
            btn.configure(fg_color=C["accent_soft"] if selected else "transparent",
                          text_color=C["accent"] if selected else C["muted"])

    # ------------------------------------------------------------------ thread plumbing

    def call_ui(self, fn, *args) -> None:
        self._events.put((fn, args))

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self._events.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(60, self._pump)

    def log(self, message: str) -> None:
        write_log_file(message)
        self.call_ui(self._append_log, f"{datetime.now():%H:%M:%S}  {message}\n")

    def _append_log(self, text: str) -> None:
        self.logbox.configure(state="normal")
        self.logbox.insert("end", text)
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def run_job(self, label: str, fn, on_result=None) -> None:
        if self._job_running:
            messagebox.showinfo("FPS Toolkit", "Another task is still running. Please wait for it to finish.")
            return
        self._job_running = True
        self.job_label.configure(text=f"Working: {label}…")
        self.progress.grid()
        self.progress.start()

        def worker() -> None:
            result = None
            try:
                result = fn(self.log)
                self.log(f"✓ {label}: done")
            except WindowsOnlyError:
                self.log(f"✗ {label}: only works on Windows")
            except PermissionError:
                self.log(f"✗ {label}: access denied, run the app as administrator")
            except Exception as exc:  # noqa: BLE001 - show any failure in the log
                self.log(f"✗ {label} failed: {exc}")
            self.call_ui(self._job_done, result, on_result)

        threading.Thread(target=worker, daemon=True).start()

    def _job_done(self, result, on_result) -> None:
        self._job_running = False
        self.progress.stop()
        self.progress.grid_remove()
        self.job_label.configure(text="")
        if on_result and result:
            on_result(result)
        self.refresh_statuses()

    def run_tweak(self, tweak: Tweak, apply: bool) -> None:
        verb = "Apply" if apply else "Revert"
        self.log(f"{verb}: {tweak.title}")

        def job(log):
            (tweak.apply if apply else tweak.revert)(log)
            if tweak.restart:
                log("    restart your PC for this to take effect")

        self.run_job(f"{verb} {tweak.title}", job)

    def run_action(self, action: Action, on_result=None) -> None:
        if action.confirm and not messagebox.askyesno("FPS Toolkit", action.confirm):
            return
        self.log(f"Run: {action.title}")
        self.run_job(action.title, action.func, on_result)

    def apply_mode(self, mode: Mode) -> None:
        turn_on = mode.tweaks
        turn_off = [t for t in TWEAKS if t.id in MODE_TWEAK_IDS and t.id not in mode.tweak_ids]
        text = f"{mode.name} mode will turn on:\n\n" + "\n".join(f"•  {t.title}" for t in turn_on)
        if turn_off:
            text += "\n\nand undo these if they're on:\n\n" + "\n".join(
                f"•  {t.title}" for t in turn_off)
        text += "\n\nCreate a restore point first if you haven't yet. Continue?"
        if not messagebox.askyesno(f"{mode.name} mode", text):
            return

        def job(log):
            needs_restart = False
            for t in turn_on:
                if t.status() is True:
                    log(f"Already on: {t.title}")
                    continue
                log(f"Apply: {t.title}")
                t.apply(log)
                needs_restart |= t.restart
            for t in turn_off:
                if t.status():
                    log(f"Revert: {t.title}")
                    t.revert(log)
                    needs_restart |= t.restart
            if needs_restart:
                log("    restart your PC to finish switching modes")

        self.log(f"Switching to {mode.name} mode")
        self.run_job(f"{mode.name} mode", job)

    # ------------------------------------------------------------------ benchmark

    def _run_label(self) -> str:
        return self.run_name.get().strip() or (f"{self.active_mode} mode" if self.active_mode
                                               else "Unnamed")

    def _after_test(self, result: dict) -> None:
        if self.run_name.get().strip().lower() == "before":
            self.run_name.delete(0, "end")
            self.run_name.insert(0, "After")
        self._refresh_compare(latest=result)

    def _run_system_test(self) -> None:
        label = self._run_label()

        def job(log):
            return benchmark.save_result("system", label, benchmark.system_test(log))

        self.log(f"System test: {label}")
        self.run_job("System test", job, on_result=self._after_test)

    def _run_game_test(self) -> None:
        seconds = int(self.duration.get().split()[0])
        if not messagebox.askyesno(
                "In-game FPS test",
                "Fortnite needs to be running.\n\n"
                "After you click Yes you have 10 seconds to switch to the game. You'll hear a "
                f"beep when recording starts, and another when it stops ({seconds} seconds).\n\n"
                "Do the same thing every run for a fair comparison. The first time, the app may "
                "download PresentMon (Intel's free, open-source frame-time tool). Continue?"):
            return
        label = self._run_label()

        def job(log):
            return benchmark.save_result("game", label, benchmark.game_test(log, seconds))

        self.log(f"In-game FPS test ({seconds} s): {label}")
        self.run_job("FPS test", job, on_result=self._after_test)

    def _clear_results(self) -> None:
        if messagebox.askyesno("FPS Toolkit", "Delete all saved benchmark results?"):
            benchmark.clear_results()
            self._refresh_compare()

    @staticmethod
    def _run_title(index: int, run: dict) -> str:
        kind = "System" if run["kind"] == "system" else "FPS"
        return f"{index + 1}. {run['label']}  ·  {kind}  ·  {run['time']}"

    def _refresh_compare(self, latest: dict | None = None) -> None:
        self._runs = benchmark.load_results()
        titles = [self._run_title(i, r) for i, r in enumerate(self._runs)] or ["—"]
        self.run_a.configure(values=titles)
        self.run_b.configure(values=titles)
        if len(self._runs) >= 2:
            last = len(self._runs) - 1
            same_kind = [i for i in range(last) if self._runs[i]["kind"] == self._runs[last]["kind"]]
            if latest or self.run_b.get() not in titles or self.run_a.get() not in titles:
                self.run_a.set(titles[same_kind[-1] if same_kind else last - 1])
                self.run_b.set(titles[last])
        else:
            self.run_a.set(titles[0])
            self.run_b.set(titles[-1])
        self._render_compare()

    def _render_compare(self) -> None:
        for child in self.compare_body.winfo_children():
            child.destroy()
        body = self.compare_body

        def note(text: str) -> None:
            ctk.CTkLabel(body, text=text, font=font(12), text_color=C["muted"], anchor="w").grid(
                row=0, column=0, sticky="w")

        titles = [self._run_title(i, r) for i, r in enumerate(self._runs)]
        if len(self._runs) < 2:
            note("Run at least two tests to compare them." if not self._runs else
                 "One result saved. Run the test again after changing settings to compare.")
            return
        a = self._runs[titles.index(self.run_a.get())]
        b = self._runs[titles.index(self.run_b.get())]
        if a["kind"] != b["kind"]:
            note("Pick two runs of the same test type (System vs System, or FPS vs FPS).")
            return

        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure((1, 2, 3), weight=2, uniform="val")
        for col, text in enumerate(("Metric", a["label"], b["label"], "Change")):
            ctk.CTkLabel(body, text=text, font=font(12, "bold"), text_color=C["muted"],
                         anchor="w").grid(row=0, column=col, sticky="ew", pady=(0, 4))
        colors = {"better": C["good"], "worse": C["bad"], "same": C["muted"]}
        rows = benchmark.compare(a, b)
        for r, (name, va, vb, change, verdict) in enumerate(rows, start=1):
            for col, text in enumerate((name, va, vb)):
                ctk.CTkLabel(body, text=text, font=font(13), anchor="w").grid(
                    row=r, column=col, sticky="ew", pady=2)
            ctk.CTkLabel(body, text=change, font=font(13, "bold"), anchor="w",
                         text_color=colors[verdict]).grid(row=r, column=3, sticky="ew", pady=2)
        counts = {v: sum(1 for row in rows if row[4] == v) for v in colors}
        ctk.CTkLabel(body, text=f"{counts['better']} better  ·  {counts['worse']} worse  ·  "
                                f"{counts['same']} about the same",
                     font=font(12), text_color=C["muted"], anchor="w").grid(
            row=len(rows) + 1, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def _revert_all(self, log) -> None:
        for t in TWEAKS:
            if t.status():
                log(f"Revert: {t.title}")
                t.revert(log)

    def refresh_statuses(self) -> None:
        if not IS_WINDOWS:
            return

        def worker() -> None:
            statuses = {}
            for t in TWEAKS:
                try:
                    statuses[t.id] = t.status()
                except Exception:  # noqa: BLE001
                    statuses[t.id] = None
            for card in self.tweak_cards:
                self.call_ui(card.set_status, statuses[card.item.id])
            on = {tid for tid in MODE_TWEAK_IDS if statuses.get(tid)}
            for card in self.mode_cards:
                self.call_ui(card.set_active, on == set(card.mode.tweak_ids))
            active = next((m.name for m in MODES if on == set(m.tweak_ids)), None)
            self.call_ui(setattr, self, "active_mode", active)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ live info

    def _tick_stats(self) -> None:
        self.cpu_graph.push(psutil.cpu_percent(interval=None))
        self.ram_graph.push(psutil.virtual_memory().percent)
        self.after(1000, self._tick_stats)

    def _load_sysinfo(self) -> None:
        def safe(fn, fallback="Unknown"):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                return fallback

        gpu_list = safe(sysinfo.gpus, [])
        info = {
            "CPU": safe(sysinfo.cpu_name),
            "GPU": ", ".join(name for name, _ in gpu_list) or "Unknown",
            "RAM": f"{safe(sysinfo.ram_gb)} GB",
            "Windows": safe(sysinfo.windows_version),
            "Network": safe(sysinfo.connection_type),
        }
        gpu_text = "\n".join(f"Detected: {n}   ·   driver {d}" for n, d in gpu_list) \
            or "No graphics card detected (preview mode)."
        self.call_ui(self._show_sysinfo, info, gpu_text)

    def _show_sysinfo(self, info: dict, gpu_text: str) -> None:
        for key, value in info.items():
            self.pc_labels[key].configure(text=value)
        self.gpu_info.configure(text=gpu_text)

    def _show_conn(self, summary: str) -> None:
        self.conn_label.configure(text=summary, text_color=C["text"])

    def report_callback_exception(self, exc, val, tb) -> None:  # Tk hook
        import traceback
        write_log_file("".join(traceback.format_exception(exc, val, tb)))
        self.log(f"✗ unexpected error: {val}")
