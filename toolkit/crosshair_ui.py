"""Crosshair: standalone overlay app with built-in crosshairs and per-weapon-slot switching."""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import colorchooser, messagebox

import customtkinter as ctk

from . import crosshair as X
from .ui import C, Card, font
from .winutils import IS_WINDOWS, WindowsOnlyError

SWATCHES = ("#00ff4c", "#00f0ff", "#ffe600", "#ff8a00", "#ff2a2a", "#ff2bd6", "#ffffff")
SLIDERS = [("length", "Length", 0, 40), ("thickness", "Thickness", 1, 10), ("gap", "Gap", 0, 30),
           ("dot", "Dot size", 0, 12), ("radius", "Circle size", 2, 60),
           ("outline", "Black outline", 0, 4), ("opacity", "Opacity %", 10, 100),
           ("offset_x", "Move left/right", -100, 100), ("offset_y", "Move up/down", -100, 100)]


def mini_preview(canvas: tk.Canvas, cfg: dict) -> None:
    """Draw a crosshair to fit a small canvas: 2x when it fits, otherwise actual size."""
    canvas.delete("all")
    w, h = int(canvas["width"]), int(canvas["height"])
    scale = 2 if X.canvas_size(cfg) * 2 <= min(w, h) + 8 else 1
    X.draw(canvas, dict(cfg, offset_x=0, offset_y=0), w // 2, h // 2, scale)


class CrosshairApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(fg_color=C["bg"])
        ctk.set_appearance_mode("dark")
        self.title("Crosshair")
        self.geometry("1080x720")
        self.minsize(940, 620)

        self.s = X.load_settings()
        self.selected = self.s["active"]
        self.master_on = False   # the Show/Hide button and toggle hotkey
        self.slot_hidden = False  # a slot key bound to "Hide crosshair" was pressed
        self.overlay = X.Overlay(self)
        self.hotkey_listener = None
        self.key_listener = None
        self._events: queue.Queue = queue.Queue()
        self._save_job = None
        self._thumb_job = None
        self._sliders: dict[str, tuple[ctk.CTkSlider, ctk.CTkLabel]] = {}
        self._slot_rows: list[dict] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self.tabs = ctk.CTkTabview(self, fg_color=C["sidebar"], corner_radius=14,
                                   segmented_button_selected_color=C["accent"],
                                   segmented_button_selected_hover_color=C["accent_hover"],
                                   segmented_button_unselected_color=C["button2"],
                                   segmented_button_unselected_hover_color=C["button2_hover"])
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        for name in ("Weapon slots", "Crosshairs", "Settings"):
            self.tabs.add(name)
        self._build_slots(self.tabs.tab("Weapon slots"))
        self._build_library(self.tabs.tab("Crosshairs"))
        self._build_settings(self.tabs.tab("Settings"))
        self.status = ctk.CTkLabel(self, text="", font=font(12), text_color=C["muted"], anchor="w")
        self.status.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 10))

        self._select(self.selected)
        self._refresh_slots()
        self._render()
        self._start_listeners()
        self.after(50, self._pump)
        if not IS_WINDOWS:
            self.say("Preview mode: the overlay and keys only work on Windows.")
        elif self.s["show_on_start"]:
            self.after(800, self.toggle)

    # ------------------------------------------------------------------ layout

    def _build_header(self) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=22, pady=(16, 8))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="✛  Crosshair", font=font(26, "bold")).grid(row=0, column=0, sticky="w")
        self.state_label = ctk.CTkLabel(head, text="", font=font(13, "bold"))
        self.state_label.grid(row=0, column=1, sticky="e", padx=16)
        self.toggle_btn = ctk.CTkButton(head, text="Show crosshair", width=170, height=40,
                                        corner_radius=10, font=font(14, "bold"),
                                        fg_color=C["accent"], hover_color=C["accent_hover"],
                                        command=self.toggle)
        self.toggle_btn.grid(row=0, column=2, sticky="e")
        ctk.CTkLabel(head, text="Free crosshair overlay. Switches crosshair when you press your "
                                "weapon-slot keys. Never touches the game.",
                     font=font(12), text_color=C["muted"]).grid(row=1, column=0, columnspan=3, sticky="w")

    def _menu(self, master, values, command, width=None) -> ctk.CTkOptionMenu:
        extra = {"width": width} if width else {}
        return ctk.CTkOptionMenu(master, values=values, command=command, height=32,
                                 corner_radius=8, font=font(12), fg_color=C["button2"],
                                 button_color=C["button2"], button_hover_color=C["button2_hover"],
                                 dynamic_resizing=False, **extra)

    def _scroll_area(self, tab) -> ctk.CTkScrollableFrame:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        area = ctk.CTkScrollableFrame(tab, fg_color="transparent",
                                      scrollbar_button_color=C["button2"])
        area.grid(row=0, column=0, sticky="nsew")
        return area

    def _build_slots(self, tab) -> None:
        tab = self._scroll_area(tab)
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        card = Card(tab)
        card.grid(row=0, column=0, sticky="nsew", padx=(4, 8), pady=4)
        card.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(card, text="Weapon slots", font=font(16, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=4, sticky="ew", padx=16, pady=(14, 2))
        ctk.CTkLabel(card, text="Pick the same keys as your Fortnite weapon-slot binds.",
                     font=font(12), text_color=C["muted"], anchor="w").grid(
            row=1, column=0, columnspan=4, sticky="ew", padx=16, pady=(0, 8))
        for col, text in ((1, "Key"), (2, "Crosshair")):
            ctk.CTkLabel(card, text=text, font=font(11, "bold"), text_color=C["muted"],
                         anchor="w").grid(row=2, column=col, sticky="w", padx=(0, 8))
        for i in range(len(self.s["slots"])):
            r = i + 3
            ctk.CTkLabel(card, text=f"Slot {i + 1}", font=font(13, "bold"), width=60,
                         anchor="w").grid(row=r, column=0, sticky="w", padx=(16, 8), pady=3)
            key = self._menu(card, list(X.SLOT_KEYS), lambda v, i=i: self._set_slot(i, "key", v),
                             width=80)
            key.grid(row=r, column=1, sticky="w", padx=(0, 8), pady=3)
            xh = self._menu(card, [X.NO_CHANGE], lambda v, i=i: self._set_slot(i, "crosshair", v))
            xh.grid(row=r, column=2, sticky="ew", padx=(0, 8), pady=3)
            prev = tk.Canvas(card, width=48, height=48, bg="#6f8fb0", highlightthickness=0)
            prev.grid(row=r, column=3, padx=(0, 16), pady=3)
            self._slot_rows.append({"key": key, "crosshair": xh, "preview": prev})
        self.fortnite_only = ctk.CTkCheckBox(
            card, text="Only switch while Fortnite is the active window (so typing in Discord "
                       "doesn't change it)", font=font(12), fg_color=C["accent"],
            hover_color=C["accent_hover"], command=self._set_fortnite_only)
        if self.s["fortnite_only"]:
            self.fortnite_only.select()
        self.fortnite_only.grid(row=len(self.s["slots"]) + 3, column=0, columnspan=4, sticky="w",
                                padx=16, pady=(10, 16))

        tips = Card(tab)
        tips.grid(row=0, column=1, sticky="nsew", padx=(8, 4), pady=4)
        tips.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tips, text="How it works", font=font(16, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        lines = [
            "Press a slot key in game and the crosshair switches to the one you picked.",
            "Your key press still goes to Fortnite as normal. Nothing is pressed for you.",
            "Bind your building or pickaxe keys (e.g. F1–F5) to 'Hide crosshair'. Pressing a "
            "weapon key brings it back.",
            "'No change' ignores that key.",
            "Scroll-wheel weapon switching can't be followed (the mouse isn't hooked, so your "
            "aim input is never slowed down). Use number keys.",
            "Fortnite must be in Windowed Fullscreen for any overlay to show.",
        ]
        for i, line in enumerate(lines, start=1):
            ctk.CTkLabel(tips, text=f"•  {line}", font=font(12), text_color=C["muted"],
                         anchor="w", justify="left", wraplength=330).grid(
                row=i, column=0, sticky="ew", padx=16, pady=3)

    def _build_library(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        left = Card(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 8), pady=4)
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="Your crosshairs", font=font(16, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self.lib_list = ctk.CTkScrollableFrame(left, width=230, fg_color="transparent",
                                               scrollbar_button_color=C["button2"])
        self.lib_list.grid(row=1, column=0, sticky="nsew", padx=8)
        btns = ctk.CTkFrame(left, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 12))
        btns.grid_columnconfigure((0, 1), weight=1)
        for i, (text, cmd) in enumerate((("New", self._new), ("Duplicate", self._duplicate),
                                         ("Rename", self._rename), ("Delete", self._delete))):
            ctk.CTkButton(btns, text=text, height=30, corner_radius=8, font=font(12),
                          fg_color=C["button2"], hover_color=C["button2_hover"], command=cmd).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
        ctk.CTkButton(btns, text="Restore built-in crosshairs", height=30, corner_radius=8,
                      font=font(12), fg_color=C["button2"], hover_color=C["button2_hover"],
                      command=self._restore_builtins).grid(row=2, column=0, columnspan=2,
                                                           sticky="ew", padx=3, pady=3)

        edit = ctk.CTkScrollableFrame(tab, fg_color=C["card"], corner_radius=14, border_width=1,
                                      border_color=C["card_border"],
                                      scrollbar_button_color=C["button2"])
        edit.grid(row=0, column=1, sticky="nsew", padx=(8, 4), pady=4)
        edit.grid_columnconfigure(1, weight=1)
        top = ctk.CTkFrame(edit, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(14, 8))
        top.grid_columnconfigure(0, weight=1)
        self.edit_title = ctk.CTkLabel(top, text="", font=font(16, "bold"), anchor="w")
        self.edit_title.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(top, text="Show this one now", height=30, corner_radius=8, font=font(12, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_hover"],
                      command=self._use_selected).grid(row=0, column=1)

        previews = ctk.CTkFrame(edit, fg_color="transparent")
        previews.grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 8))
        self.prev_actual = tk.Canvas(previews, width=180, height=120, bg="#6f8fb0", highlightthickness=0)
        self.prev_actual.grid(row=0, column=0, padx=(0, 10))
        self.prev_zoom = tk.Canvas(previews, width=180, height=120, bg="#6f8fb0", highlightthickness=0)
        self.prev_zoom.grid(row=0, column=1)
        for col, text in enumerate(("Actual size", "Zoomed 3×")):
            ctk.CTkLabel(previews, text=text, font=font(11), text_color=C["muted"]).grid(row=1, column=col)

        ctk.CTkLabel(edit, text="Style", font=font(12), anchor="w").grid(
            row=2, column=0, sticky="w", padx=(16, 8), pady=3)
        self.style_menu = self._menu(edit, X.STYLES, lambda v: self._set("style", v))
        self.style_menu.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=3)

        ctk.CTkLabel(edit, text="Color", font=font(12), anchor="w").grid(
            row=3, column=0, sticky="w", padx=(16, 8), pady=3)
        sw = ctk.CTkFrame(edit, fg_color="transparent")
        sw.grid(row=3, column=1, columnspan=2, sticky="w", padx=(0, 16), pady=3)
        for i, color in enumerate(SWATCHES):
            ctk.CTkButton(sw, text="", width=26, height=26, corner_radius=6, fg_color=color,
                          hover_color=color, border_width=1, border_color=C["card_border"],
                          command=lambda c=color: self._set("color", c)).grid(row=0, column=i, padx=(0, 5))
        ctk.CTkButton(sw, text="Custom…", width=70, height=26, corner_radius=6, font=font(12),
                      fg_color=C["button2"], hover_color=C["button2_hover"],
                      command=self._pick_color).grid(row=0, column=len(SWATCHES))

        for i, (key, label, lo, hi) in enumerate(SLIDERS, start=4):
            ctk.CTkLabel(edit, text=label, font=font(12), anchor="w").grid(
                row=i, column=0, sticky="w", padx=(16, 8), pady=2)
            slider = ctk.CTkSlider(edit, from_=lo, to=hi, number_of_steps=hi - lo,
                                   progress_color=C["accent"], button_color=C["accent"],
                                   button_hover_color=C["accent_hover"],
                                   command=lambda v, k=key: self._set(k, int(round(v))))
            slider.grid(row=i, column=1, sticky="ew", pady=2)
            value = ctk.CTkLabel(edit, text="", width=36, font=font(12, "bold"), anchor="e")
            value.grid(row=i, column=2, sticky="e", padx=(8, 16), pady=2)
            self._sliders[key] = (slider, value)

    def _build_settings(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        card = Card(tab)
        card.grid(row=0, column=0, sticky="new", padx=4, pady=4)
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(card, text="Settings", font=font(16, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 8))

        ctk.CTkLabel(card, text="Show/hide hotkey", font=font(12), anchor="w").grid(
            row=1, column=0, sticky="w", padx=(16, 12), pady=5)
        self.hotkey_menu = self._menu(card, list(X.HOTKEYS), self._set_hotkey, width=200)
        self.hotkey_menu.set(self.s["hotkey"])
        self.hotkey_menu.grid(row=1, column=1, sticky="w", pady=5)

        ctk.CTkLabel(card, text="Monitor", font=font(12), anchor="w").grid(
            row=2, column=0, sticky="w", padx=(16, 12), pady=5)
        self.monitor_names = [f"{i + 1}:  {m['width']}×{m['height']}" + ("  (main)" if m["primary"] else "")
                              for i, m in enumerate(X.monitors())]
        if self.s["monitor"] >= len(self.monitor_names):
            self.s["monitor"] = 0
        self.monitor_menu = self._menu(card, self.monitor_names, self._set_monitor, width=200)
        self.monitor_menu.set(self.monitor_names[self.s["monitor"]])
        self.monitor_menu.grid(row=2, column=1, sticky="w", pady=5)

        self.on_start = ctk.CTkCheckBox(card, text="Show the crosshair when the app starts",
                                        font=font(12), fg_color=C["accent"],
                                        hover_color=C["accent_hover"], command=self._set_on_start)
        if self.s["show_on_start"]:
            self.on_start.select()
        self.on_start.grid(row=3, column=0, columnspan=2, sticky="w", padx=16, pady=(8, 6))

        info = ("Fortnite: Settings → Video → Display Mode → Windowed Fullscreen. In normal "
                "Fullscreen the game covers every window, including this one.\n\n"
                "The overlay is a see-through window your clicks pass through. It doesn't read or "
                "change the game. The slot keys are only watched, never pressed or blocked. "
                "Tournaments can have their own rules about third-party software, so check them "
                "if you compete.")
        ctk.CTkLabel(card, text=info, font=font(12), text_color=C["muted"], anchor="w",
                     justify="left", wraplength=760).grid(row=4, column=0, columnspan=2,
                                                          sticky="ew", padx=16, pady=(10, 16))

    # ------------------------------------------------------------------ plumbing

    def call_ui(self, fn, *args) -> None:
        self._events.put((fn, args))

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self._events.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(20, self._pump)

    def say(self, text: str) -> None:
        self.status.configure(text=text)

    def _save_soon(self) -> None:
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(400, self._save)

    def _save(self) -> None:
        self._save_job = None
        try:
            X.save_settings(self.s)
        except OSError as exc:
            self.say(f"Couldn't save settings: {exc}")

    # ------------------------------------------------------------------ overlay state

    def _active_cfg(self) -> dict:
        return dict(self.s["crosshairs"][self.s["active"]], monitor=self.s["monitor"])

    def _render(self) -> None:
        visible = self.master_on and not self.slot_hidden
        if visible:
            try:
                if self.overlay.visible:
                    self.overlay.update(self._active_cfg())
                else:
                    self.overlay.show(self._active_cfg())
            except WindowsOnlyError:
                self.master_on = False
                visible = False
                self.say("The overlay only works on Windows.")
        elif self.overlay.visible:
            self.overlay.hide()

        if not self.master_on:
            self.state_label.configure(text="○  Off", text_color=C["muted"])
            self.toggle_btn.configure(text="Show crosshair", fg_color=C["accent"],
                                      hover_color=C["accent_hover"])
        else:
            text = f"●  {self.s['active']}" if visible else "●  Hidden by slot key"
            self.state_label.configure(text=text, text_color=C["good"] if visible else C["muted"])
            self.toggle_btn.configure(text="Hide crosshair", fg_color="#b91c1c", hover_color="#991b1b")

    def toggle(self) -> None:
        self.master_on = not self.master_on
        self.slot_hidden = False
        self._render()

    def _start_listeners(self) -> None:
        self._restart_hotkey(quiet=True)
        if IS_WINDOWS:
            self.key_listener = X.start_key_listener(lambda vk: self.call_ui(self._on_slot_key, vk))
            if not self.key_listener:
                self.say("Couldn't start the weapon-slot key listener.")
        self._update_watched()

    def _restart_hotkey(self, quiet: bool = False) -> None:
        if self.hotkey_listener:
            self.hotkey_listener.stop()
            self.hotkey_listener = None
        combo = self.s["hotkey"]
        if combo == "Off" or not IS_WINDOWS:
            return
        self.hotkey_listener = X.start_hotkey(combo, lambda: self.call_ui(self.toggle))
        if not self.hotkey_listener:
            self.say(f"{combo} is already used by another program. Pick another hotkey in Settings.")
        elif not quiet:
            self.say(f"Show/hide hotkey: {combo}")

    def _update_watched(self) -> None:
        if self.key_listener:
            self.key_listener.watched = frozenset(
                X.SLOT_KEYS[slot["key"]] for slot in self.s["slots"]
                if X.SLOT_KEYS.get(slot["key"]) and slot["crosshair"] != X.NO_CHANGE)

    def _on_slot_key(self, vk: int) -> None:
        if self.s["fortnite_only"] and X.foreground_process_name().lower() != X.GAME_EXE.lower():
            return
        for slot in self.s["slots"]:
            if X.SLOT_KEYS.get(slot["key"]) != vk or slot["crosshair"] == X.NO_CHANGE:
                continue
            if slot["crosshair"] == X.HIDE:
                self.slot_hidden = True
            else:
                self.slot_hidden = False
                self.s["active"] = slot["crosshair"]
                self._save_soon()
            self._render()
            return

    # ------------------------------------------------------------------ weapon slots

    def _choices(self) -> list[str]:
        return [X.NO_CHANGE, X.HIDE, *self.s["crosshairs"]]

    def _refresh_slots(self) -> None:
        choices = self._choices()
        for row, slot in zip(self._slot_rows, self.s["slots"]):
            row["key"].set(slot["key"])
            row["crosshair"].configure(values=choices)
            row["crosshair"].set(slot["crosshair"])
            canvas = row["preview"]
            canvas.delete("all")
            if slot["crosshair"] in self.s["crosshairs"]:
                mini_preview(canvas, self.s["crosshairs"][slot["crosshair"]])
            else:
                text = "hide" if slot["crosshair"] == X.HIDE else "—"
                canvas.create_text(24, 24, text=text, fill="#1d2633", font=("Segoe UI", 9, "bold"))
        self._update_watched()

    def _set_slot(self, index: int, field: str, value: str) -> None:
        if field == "key" and value != "—":
            for i, other in enumerate(self.s["slots"]):
                if i != index and other["key"] == value:
                    other["key"] = "—"  # one key can only belong to one slot
        self.s["slots"][index][field] = value
        self._refresh_slots()
        self._save_soon()

    def _set_fortnite_only(self) -> None:
        self.s["fortnite_only"] = bool(self.fortnite_only.get())
        self._save_soon()

    # ------------------------------------------------------------------ library & editor

    def _rebuild_list(self) -> None:
        for child in self.lib_list.winfo_children():
            child.destroy()
        for i, (name, cfg) in enumerate(self.s["crosshairs"].items()):
            selected = name == self.selected
            row = ctk.CTkFrame(self.lib_list, corner_radius=10,
                               fg_color=C["accent_soft"] if selected else "transparent")
            row.grid(row=i, column=0, sticky="ew", pady=2)
            self.lib_list.grid_columnconfigure(0, weight=1)
            row.grid_columnconfigure(1, weight=1)
            canvas = tk.Canvas(row, width=40, height=40, bg="#6f8fb0", highlightthickness=0)
            canvas.grid(row=0, column=0, padx=6, pady=4)
            mini_preview(canvas, cfg)
            label = name + ("   (showing)" if name == self.s["active"] else "")
            btn = ctk.CTkButton(row, text=label, anchor="w", height=36, font=font(13),
                                fg_color="transparent", hover_color=C["hover"],
                                text_color=C["accent"] if selected else C["text"],
                                command=lambda n=name: self._select(n))
            btn.grid(row=0, column=1, sticky="ew", padx=(0, 6))
            canvas.bind("<Button-1>", lambda _e, n=name: self._select(n))

    def _select(self, name: str) -> None:
        self.selected = name
        cfg = self.s["crosshairs"][name]
        self.edit_title.configure(text=name)
        self.style_menu.set(cfg["style"])
        for key, (slider, value) in self._sliders.items():
            slider.set(cfg[key])
            value.configure(text=str(cfg[key]))
        self._draw_previews()
        self._rebuild_list()

    def _draw_previews(self) -> None:
        cfg = dict(self.s["crosshairs"][self.selected], offset_x=0, offset_y=0)
        for canvas, scale in ((self.prev_actual, 1), (self.prev_zoom, 3)):
            canvas.delete("all")
            w, h = int(canvas["width"]), int(canvas["height"])
            canvas.create_rectangle(0, h * 0.62, w, h, fill="#5d7a3a", outline="")
            X.draw(canvas, cfg, w // 2, h // 2, scale)

    def _set(self, key: str, value) -> None:
        self.s["crosshairs"][self.selected][key] = value
        if key in self._sliders:
            self._sliders[key][1].configure(text=str(value))
        self._draw_previews()
        if self.selected == self.s["active"]:
            self._render()
        self._save_soon()
        if self._thumb_job:  # refresh thumbnails once the user stops dragging
            self.after_cancel(self._thumb_job)
        self._thumb_job = self.after(450, self._refresh_thumbs)

    def _refresh_thumbs(self) -> None:
        self._thumb_job = None
        self._rebuild_list()
        self._refresh_slots()

    def _pick_color(self) -> None:
        cfg = self.s["crosshairs"][self.selected]
        picked = colorchooser.askcolor(color=cfg["color"], title="Crosshair color")
        if picked and picked[1]:
            self._set("color", picked[1])

    def _use_selected(self) -> None:
        self.s["active"] = self.selected
        self.slot_hidden = False
        if not self.master_on:
            self.master_on = True
        self._render()
        self._rebuild_list()
        self._save_soon()

    def _unique(self, base: str) -> str:
        name, n = base, 2
        while name in self.s["crosshairs"]:
            name, n = f"{base} {n}", n + 1
        return name

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        name = ctk.CTkInputDialog(text=f"{title}:" + (f" (now: {initial})" if initial else ""),
                                  title=title).get_input()
        name = (name or "").strip()
        if not name:
            return None
        if name in (X.NO_CHANGE, X.HIDE) or name in self.s["crosshairs"]:
            messagebox.showinfo("Crosshair", "That name is already used. Pick another.")
            return None
        return name

    def _new(self) -> None:
        name = self._unique("New crosshair")
        self.s["crosshairs"][name] = dict(X.DEFAULT)
        self._after_library_change(name)

    def _duplicate(self) -> None:
        name = self._unique(f"{self.selected} copy")
        self.s["crosshairs"][name] = dict(self.s["crosshairs"][self.selected])
        self._after_library_change(name)

    def _rename(self) -> None:
        old = self.selected
        new = self._ask_name("New name", old)
        if not new:
            return
        self.s["crosshairs"] = {(new if k == old else k): v for k, v in self.s["crosshairs"].items()}
        for slot in self.s["slots"]:
            if slot["crosshair"] == old:
                slot["crosshair"] = new
        if self.s["active"] == old:
            self.s["active"] = new
        self._after_library_change(new)

    def _delete(self) -> None:
        if len(self.s["crosshairs"]) <= 1:
            messagebox.showinfo("Crosshair", "You need at least one crosshair.")
            return
        name = self.selected
        if not messagebox.askyesno("Crosshair", f"Delete '{name}'?"):
            return
        del self.s["crosshairs"][name]
        for slot in self.s["slots"]:
            if slot["crosshair"] == name:
                slot["crosshair"] = X.NO_CHANGE
        if self.s["active"] == name:
            self.s["active"] = next(iter(self.s["crosshairs"]))
            self._render()
        self._after_library_change(next(iter(self.s["crosshairs"])))

    def _restore_builtins(self) -> None:
        if not messagebox.askyesno("Crosshair", "Put back the built-in crosshairs (SMG dot, "
                                                "Shotgun circle, …) with their original look?"):
            return
        for name, cfg in X.BUILTIN.items():
            self.s["crosshairs"][name] = dict(cfg)
        self._render()
        self._after_library_change(self.selected if self.selected in self.s["crosshairs"] else "SMG dot")

    def _after_library_change(self, select: str) -> None:
        self._select(select)
        self._refresh_slots()
        self._save_soon()

    # ------------------------------------------------------------------ settings

    def _set_hotkey(self, combo: str) -> None:
        self.s["hotkey"] = combo
        self._restart_hotkey()
        self._save_soon()

    def _set_monitor(self, label: str) -> None:
        self.s["monitor"] = self.monitor_names.index(label)
        self._render()
        self._save_soon()

    def _set_on_start(self) -> None:
        self.s["show_on_start"] = bool(self.on_start.get())
        self._save_soon()

    def destroy(self) -> None:
        for listener in (self.hotkey_listener, self.key_listener):
            if listener:
                listener.stop()
        if self._save_job:
            self.after_cancel(self._save_job)
            self._save()
        super().destroy()
