#!/usr/bin/env python3
"""Floating Claude usage widget, portable edition (Tk).

A small always-on-top card showing Claude Code cost, tokens, and how much of each rate limit is
left. Drag to move, double-click to shrink to a one-line pill, right-click for the menu.
Standard library only (needs tkinter). Python 3.9+. Nothing leaves this machine.

    python3 claude_usage_widget.py
    python3 claude_usage_widget.py --decorated    keep the normal window frame, for window
                                                  managers that mishandle borderless windows
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_usage as cu  # noqa: E402

REFRESH_SECONDS = 30
CARD, BORDER, TIP = "#1C1B1A", "#3A3937", "#2B2A28"
TITLE, LABEL, VALUE, DIM, FAINT = "#9C9A92", "#B8B5AD", "#F3F1EA", "#8A877F", "#6F6C66"
ACCENT, HOT, IDLE, TRACK = "#D97757", "#E5484D", "#5A5853", "#34322F"
UI_FONTS = ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans")
MONO_FONTS = ("Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Liberation Mono", "Courier New")


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    _user32 = ctypes.WinDLL("user32")
    _user32.WindowFromPoint.argtypes, _user32.WindowFromPoint.restype = [wintypes.POINT], wintypes.HWND
    _user32.GetAncestor.argtypes, _user32.GetAncestor.restype = [wintypes.HWND, wintypes.UINT], wintypes.HWND
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.MonitorFromWindow.argtypes, _user32.MonitorFromWindow.restype = [wintypes.HWND, wintypes.DWORD], wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.UINT]

    def reclaim_from_taskbar(widget_hwnd):
        """Windows keeps the taskbar in the same always-on-top band as the widget, and the taskbar
        puts itself back on top whenever it is used, so a pill parked on it would disappear behind
        it. If the taskbar is covering the widget, take the top spot back.

        Targeted on purpose: it only ever acts when the window on top IS the taskbar, so it never
        fights Start, flyouts, or other always-on-top apps. Returns True while the widget sticks
        out of its monitor's work area, which is the caller's cue to keep checking quickly."""
        top = _user32.GetAncestor(widget_hwnd, 2)                      # GA_ROOT
        rect = wintypes.RECT()
        if not top or not _user32.GetWindowRect(top, ctypes.byref(rect)):
            return False
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(_user32.MonitorFromWindow(top, 2), ctypes.byref(info)):   # nearest monitor
            return False
        work = info.rcWork
        if not (rect.left < work.left or rect.top < work.top or rect.right > work.right or rect.bottom > work.bottom):
            return False
        # The middle of each edge (2px in) and the centre: the widget may only partly overlap the taskbar.
        mid_x, mid_y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        name = ctypes.create_unicode_buffer(64)
        for x, y in ((mid_x, mid_y), (mid_x, rect.bottom - 2), (mid_x, rect.top + 2), (rect.left + 2, mid_y), (rect.right - 2, mid_y)):
            over = _user32.GetAncestor(_user32.WindowFromPoint(wintypes.POINT(x, y)), 2)
            if not over or over == top:
                continue
            _user32.GetClassNameW(over, name, 64)
            if name.value in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                _user32.SetWindowPos(top, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # TOPMOST, no size/move/activate
                break
        return True


class Tooltip:
    """Shows text_fn() near the pointer after a short hover."""

    def __init__(self, widget, text_fn, font):
        self.widget, self.text_fn, self.font = widget, text_fn, font
        self.window = None
        self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event):
        self._hide()
        self.job = self.widget.after(600, self._show)

    def _show(self):
        self.job = None
        text = self.text_fn()
        if not text:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        try:
            self.window.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(self.window, text=text, justify="left", bg=TIP, fg=VALUE, padx=8, pady=5, font=self.font).pack()
        self.window.geometry("+%d+%d" % (self.widget.winfo_pointerx() + 14, self.widget.winfo_pointery() + 18))

    def _hide(self, _event=None):
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def blend(colour_a, colour_b, share):
    """Mix two #RRGGBB colours. share is how much of colour_b goes in."""
    a = [int(colour_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(colour_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(int(round(x + (y - x) * share)) for x, y in zip(a, b))


class Gauge:
    """Shared behaviour of the ring and the bar. set() glides from whatever is showing to the new
    value with an ease-out, and the gauge turns red once LOW_PERCENT or less is left."""

    SECONDS = 1.1

    def __init__(self, widget):
        self.widget = widget        # what gets packed; also owns the animation timer
        self.shown = 0.0            # fraction currently drawn
        self.target = None          # fraction we are heading for
        self.low = None
        self._job = None

    def set(self, left_percent):
        target = max(0.0, min(100.0, left_percent)) / 100.0
        low = left_percent <= cu.LOW_PERCENT
        if low != self.low:
            self.low = low
            self._restyle()
        if self.target is None or abs(target - self.target) > 0.0005:
            self.target, self._start, self._began = target, self.shown, time.monotonic()
            self._step()

    def replay(self):
        """Empty the gauge so the next set() sweeps in from nothing."""
        self.shown, self.target = 0.0, None
        self._draw()

    def _step(self):
        try:
            if self._job is not None:
                self.widget.after_cancel(self._job)
                self._job = None
            progress = min(1.0, (time.monotonic() - self._began) / self.SECONDS)
            self.shown = self._start + (self.target - self._start) * (1 - (1 - progress) ** 3)
            self._draw()
            if progress < 1.0:
                self._job = self.widget.after(16, self._step)
        except tk.TclError:
            pass                                            # the widget went away mid-animation

    def _restyle(self):
        raise NotImplementedError

    def _draw(self):
        raise NotImplementedError


def _rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _clamp01(value):
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


_RING_GEOMETRY = {}


def _ring_geometry(size, radius, thickness):
    """Everything about a ring that does not depend on its value, worked out once per size:
    for each pixel its distance and clockwise angle from 12 o'clock, how much of it the ring band
    covers, how strong the glow is there, and the track colour already blended onto the card."""
    key = (size, radius, thickness)
    if key not in _RING_GEOMETRY:
        centre, half = size / 2.0, thickness / 2.0
        card, track = _rgb(CARD), _rgb(TRACK)
        pixels = []
        for y in range(size):
            for x in range(size):
                dx, dy = x + 0.5 - centre, y + 0.5 - centre
                distance = math.hypot(dx, dy)
                off_band = abs(distance - radius) - half                   # < 0 inside the band
                band = _clamp01(0.5 - off_band)                            # one pixel of anti-aliasing
                glow = 0.55 * math.exp(-(max(0.0, off_band) / 2.0) ** 2)
                angle = math.atan2(dx, -dy) % (2 * math.pi)
                base = tuple(c + (t - c) * band for c, t in zip(card, track))
                pixels.append((x + 0.5, y + 0.5, distance, angle, band, glow, base))
        _RING_GEOMETRY[key] = pixels
    return _RING_GEOMETRY[key]


class Ring(Gauge):
    """A ring that shows what is LEFT, draining clockwise from 12 o'clock, with rounded ends and a
    soft glow. While the limit is low it turns red and slowly breathes.

    Tk's canvas has no anti-aliasing and no blur, and at this size its arcs turn into blobs, so the
    ring is rendered here pixel by pixel into a PhotoImage and blended onto the card colour. The
    per-pixel geometry is cached, so a frame is only a few hundred cheap operations."""

    def __init__(self, parent, diameter=14, thickness=2.6):
        self._size = int(diameter + 6)                       # room around the ring for the glow
        self._radius = (diameter - thickness) / 2.0
        self._thickness = thickness
        self._image = tk.PhotoImage(width=self._size, height=self._size)
        super().__init__(tk.Label(parent, image=self._image, bg=CARD, bd=0, highlightthickness=0))
        self._strength = 1.0                                 # dimmed and restored by the breathing
        self._phase = 0.0
        self._pulse = None
        self._draw()

    def _restyle(self):
        self._draw()
        if self.low and self._pulse is None:
            self._breathe()

    def _breathe(self):
        self._pulse = None
        try:
            if not self.low:
                self._strength = 1.0
                self._draw()
                return
            self._phase += 0.2
            self._strength = 1.0 - 0.6 * (0.5 * (1 - math.cos(self._phase)))      # about two seconds a cycle
            self._draw()
            self._pulse = self.widget.after(60, self._breathe)
        except tk.TclError:
            pass

    def _draw(self):
        colour = _rgb(HOT if self.low else ACCENT)
        strength = self._strength
        size, radius, half = self._size, self._radius, self._thickness / 2.0
        centre = size / 2.0
        sweep = 2 * math.pi * self.shown
        full, empty = self.shown >= 0.999, self.shown <= 0.004
        end_x, end_y = centre + radius * math.sin(sweep), centre - radius * math.cos(sweep)
        start_x, start_y = centre, centre - radius

        rows, row = [], []
        for px, py, distance, angle, band, glow, base in _ring_geometry(size, radius, self._thickness):
            if empty:
                lit = 0.0
            elif full:
                lit = 1.0
            else:
                # How far, in pixels along the ring, this pixel is inside the lit part of the arc.
                lit = min(_clamp01(angle * distance + 0.5), _clamp01((sweep - angle) * distance + 0.5))
            arc = band * lit
            if not empty and not full:
                # Rounded ends: a dot the width of the ring at each end of the arc.
                arc = max(arc, _clamp01(half + 0.5 - math.hypot(px - end_x, py - end_y)),
                          _clamp01(half + 0.5 - math.hypot(px - start_x, py - start_y)))
            halo = glow * lit * strength * (1.0 - arc)
            solid = arc * strength
            red, green, blue = (b + (c - b) * halo for b, c in zip(base, colour))
            row.append("#%02x%02x%02x" % (int(red + (colour[0] - red) * solid), int(green + (colour[1] - green) * solid),
                                          int(blue + (colour[2] - blue) * solid)))
            if len(row) == size:
                rows.append("{" + " ".join(row) + "}")
                row = []
        self._image.put(" ".join(rows))


class Bar(Gauge):
    """The wide bar under a limit row. Same draining and easing as the ring."""

    def __init__(self, parent):
        # width=1: a Canvas asks for 10cm by default, which would force the whole card wide.
        super().__init__(tk.Canvas(parent, width=1, height=4, bg=TRACK, highlightthickness=0))
        self._fill = self.widget.create_rectangle(0, 0, 0, 4, outline="", fill=ACCENT)
        self.widget.bind("<Configure>", lambda _event: self._draw())

    def _restyle(self):
        self.widget.itemconfigure(self._fill, fill=HOT if self.low else ACCENT)

    def _draw(self):
        self.widget.coords(self._fill, 0, 0, int(self.widget.winfo_width() * self.shown), self.widget.winfo_height())


class WidgetApp:
    ROWS = (("Last 5h", "h5"), ("Today", "today"), ("7 days", "d7"))

    def __init__(self, root, engine, decorated=False, start_worker=True, feed=None, state_file=None):
        self.root, self.engine, self.decorated, self.feed = root, engine, decorated, feed
        self.state_file = state_file or os.path.join(cu.state_dir(), "state-python.json")
        self.summary = None
        self.limits = None
        self.compact = False
        self._drag_from = None
        self._moved = False
        self._menu_open = False
        self._last_event = 0.0
        self._rows, self._row_order = {}, None        # limit rows are built once, then updated in place
        self._pill_parts, self._pill_order = {}, None
        self._results = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()

        families = set(tkfont.families(root))
        ui = next((f for f in UI_FONTS if f in families), "TkDefaultFont")
        mono = next((f for f in MONO_FONTS if f in families), "TkFixedFont")
        self.f_label, self.f_small, self.f_title = (ui, 9), (ui, 8), (ui, 8, "bold")
        self.f_value, self.f_dim = (mono, 10, "bold"), (mono, 8)

        self._style_window()
        self._build()
        self._build_menu()

        saved = self._load_state()
        if saved.get("compact"):
            self._show_view(True)
        if saved.get("topmost") is False:
            self.topmost_var.set(False)
            self._apply_topmost()
        self._place(saved)

        root.bind("<ButtonPress-1>", self._press)
        root.bind("<B1-Motion>", self._drag)
        root.bind("<ButtonRelease-1>", self._release)
        root.bind("<Double-Button-1>", lambda event: None if self._from_menu(event) else self.set_compact(not self.compact))
        for sequence in ("<Button-3>",) + (("<Button-2>", "<Control-Button-1>") if sys.platform == "darwin" else ()):
            root.bind(sequence, self._popup)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        if start_worker:
            threading.Thread(target=self._worker, name="usage-scan", daemon=True).start()
            root.after(200, self._poll)
            if os.name == "nt":
                root.after(1000, self._keep_on_top)

    # -- window ---------------------------------------------------------------

    def _style_window(self):
        root = self.root
        root.title("Claude Usage")
        root.configure(bg=BORDER)
        root.resizable(False, False)
        if not self.decorated:
            # Borderless means something different to every window system.
            if sys.platform == "darwin":
                try:
                    root.tk.call("::tk::unsupported::MacWindowStyle", "style", root._w, "plain", "none")
                except tk.TclError:
                    root.overrideredirect(True)
            elif sys.platform.startswith("linux"):
                try:
                    root.attributes("-type", "splash")      # undecorated, but still managed (so it can stay on top)
                except tk.TclError:
                    root.overrideredirect(True)
            else:
                root.overrideredirect(True)
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass

    def _build(self):
        self.card = tk.Frame(self.root, bg=CARD, padx=13, pady=9)
        self.card.pack(padx=1, pady=1)                      # the 1px gap shows the root colour as a border

        self.full = tk.Frame(self.card, bg=CARD)
        self.full.pack()

        header = tk.Frame(self.full, bg=CARD)
        header.pack(fill="x")
        self.dot = self._dot(header)
        self.dot.pack(side="left", padx=(0, 6))
        title = tk.Label(header, text="CLAUDE USAGE", bg=CARD, fg=TITLE, font=self.f_title)
        title.pack(side="left")
        Tooltip(title, lambda: "Double-click to shrink", self.f_small)
        close = tk.Label(header, text="×", bg=CARD, fg=FAINT, font=(self.f_label[0], 12), cursor="hand2")
        close.pack(side="right")
        close.bind("<ButtonPress-1>", lambda _event: (self.quit(), "break")[1])

        table = tk.Frame(self.full, bg=CARD)
        table.pack(fill="x", pady=(4, 0))
        table.columnconfigure(1, weight=1, minsize=84)
        table.columnconfigure(2, minsize=64)
        tk.Label(table, text="est. cost", bg=CARD, fg=FAINT, font=self.f_small).grid(row=0, column=1, sticky="e")
        tk.Label(table, text="tokens", bg=CARD, fg=FAINT, font=self.f_small).grid(row=0, column=2, sticky="e", padx=(12, 0))
        self.cells = {}
        for index, (caption, key) in enumerate(self.ROWS, start=1):
            name = tk.Label(table, text=caption, bg=CARD, fg=LABEL, font=self.f_label)
            cost = tk.Label(table, text="...", bg=CARD, fg=VALUE, font=self.f_value)
            tokens = tk.Label(table, text="", bg=CARD, fg=DIM, font=self.f_dim)
            name.grid(row=index, column=0, sticky="w", padx=(0, 14))
            cost.grid(row=index, column=1, sticky="e")
            tokens.grid(row=index, column=2, sticky="e", padx=(12, 0))
            self.cells[key] = (cost, tokens)
            for widget in (name, cost, tokens):
                Tooltip(widget, lambda key=key: self._bucket_tip(key), self.f_small)

        self.limit_rows = tk.Frame(self.full, bg=CARD)
        self.limit_rows.pack(fill="x")
        self.as_of = tk.Label(self.full, text="", bg=CARD, fg=FAINT, font=self.f_small, anchor="w")    # packed when stale
        self.split = tk.Label(self.full, text="", bg=CARD, fg=LABEL, font=self.f_small, anchor="w")
        self.split.pack(fill="x", pady=(6, 0))
        self.footer = tk.Label(self.full, text="starting", bg=CARD, fg=FAINT, font=self.f_small, anchor="w")
        self.footer.pack(fill="x", pady=(2, 0))

        self.pill = tk.Frame(self.card, bg=CARD)
        self.pill_dot = self._dot(self.pill)
        self.pill_dot.pack(side="left", padx=(0, 6))
        self.pill_items = tk.Frame(self.pill, bg=CARD)          # ring + "5h 84%" pairs, once there is a limit feed
        self.pill_text = tk.Label(self.pill, text="...", bg=CARD, fg=VALUE, font=(self.f_dim[0], 9))   # no feed: cost headline
        self.pill_text.pack(side="left")
        for widget in (self.pill, self.pill_text):
            Tooltip(widget, self._pill_tip, self.f_small)

    def _dot(self, parent):
        canvas = tk.Canvas(parent, width=8, height=8, bg=CARD, highlightthickness=0)
        canvas.create_oval(1, 1, 7, 7, fill=IDLE, outline="", tags="dot")
        return canvas

    def _build_menu(self):
        self.compact_var = tk.BooleanVar(value=False)
        self.topmost_var = tk.BooleanVar(value=True)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Refresh now", command=self.refresh)
        self.menu.add_checkbutton(label="Compact (double-click)", variable=self.compact_var,
                                  command=lambda: self.set_compact(self.compact_var.get()))
        self.menu.add_checkbutton(label="Always on top", variable=self.topmost_var, command=self._apply_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.quit)

    def _apply_topmost(self):
        try:
            self.root.attributes("-topmost", bool(self.topmost_var.get()))
        except tk.TclError:
            pass
        self._save_state()

    def _keep_on_top(self):
        """Windows only: every 30 ms while the widget is parked over the taskbar zone, once a second
        otherwise. Measured by forcing the taskbar on top and sampling every 10 ms: the pill is
        hidden for 30 to 46 ms, against up to a second with a slow re-check."""
        if self._stop.is_set():
            return
        fast = False
        try:
            if self.topmost_var.get() and self._drag_from is None and not self._menu_open:
                fast = reclaim_from_taskbar(self.root.winfo_id())
        except Exception:                                    # never let a z-order nicety take the widget down
            pass
        self.root.after(30 if fast else 1000, self._keep_on_top)

    # -- mouse ----------------------------------------------------------------

    def _from_menu(self, event):
        return str(event.widget).startswith(str(self.menu))      # the menu inherits the root's bindings

    def _press(self, event):
        if self._from_menu(event):
            return
        self._drag_from = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())
        self._moved = False

    def _drag(self, event):
        if self._drag_from is None or self._from_menu(event):
            return
        self.root.geometry("+%d+%d" % (event.x_root - self._drag_from[0], event.y_root - self._drag_from[1]))
        self._moved = True

    def _release(self, _event):
        if self._moved:
            self._save_state()
        self._drag_from = None
        self._moved = False

    def _popup(self, event):
        self._menu_open = True                  # the menu is an always-on-top popup too: do not jump in front of it
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
            self._menu_open = False

    # -- size, position, state ----------------------------------------------------

    def _show_view(self, compact):
        self.compact = compact
        self.compact_var.set(compact)
        if compact:
            self.full.pack_forget()
            self.pill.pack()
            self.card.configure(padx=10, pady=5)
        else:
            self.pill.pack_forget()
            self.full.pack()
            self.card.configure(padx=13, pady=9)

    def set_compact(self, compact):
        before = self._box()
        self._show_view(bool(compact))
        # A little flourish: the gauges of the view that just appeared sweep in from empty.
        for part in list(self._pill_parts.values() if self.compact else self._rows.values()):
            for gauge in part["gauges"]:
                gauge.replay()
        if self.summary is not None:
            self._fill(self.summary, self.limits, self._last_event)
        self._reanchor(before)
        self._save_state()

    def _box(self):
        root = self.root
        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
        if width <= 1 or height <= 1:                       # not mapped yet: Tk reports 1x1 until it is
            width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        return root.winfo_x(), root.winfo_y(), width, height

    def _screen(self):
        root = self.root
        width, height = root.winfo_vrootwidth(), root.winfo_vrootheight()
        if width <= 1 or height <= 1:
            return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
        return root.winfo_vrootx(), root.winfo_vrooty(), width, height

    def _reanchor(self, before):
        """Tk resizes a window from its top-left corner. Keep whichever edges are nearest the
        screen edge fixed instead, so a widget parked at the right or bottom does not drift."""
        x, y, width, height = before
        self.root.update_idletasks()
        new_width, new_height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        if (new_width, new_height) == (width, height):
            return
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if x + width / 2 > screen_width / 2:
            x += width - new_width
        if y + height / 2 > screen_height / 2:
            y += height - new_height
        # A card dragged partly past the screen edge would otherwise shrink to a pill that is
        # entirely off-screen (its fixed edge was the one outside). Always end up fully visible.
        origin_x, origin_y, total_width, total_height = self._screen()
        x = max(origin_x, min(x, origin_x + total_width - new_width))
        y = max(origin_y, min(y, origin_y + total_height - new_height))
        self.root.geometry("+%d+%d" % (x, y))

    def _load_state(self):
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            return saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        try:
            x, y, width, height = self._box()
            cu.write_atomic(self.state_file, json.dumps({
                "x": x, "y": y, "right": x + width, "bottom": y + height,
                "compact": self.compact, "topmost": bool(self.topmost_var.get())}))
        except (OSError, tk.TclError):
            pass

    def _place(self, saved):
        root = self.root
        root.update_idletasks()
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = screen_width - width - 16, screen_height - height - 64          # default: bottom right
        try:
            left, top = int(saved["x"]), int(saved["y"])
            right, bottom = int(saved.get("right", left + width)), int(saved.get("bottom", top + height))
            # Same rule as _reanchor: a widget on the right or bottom half hangs off that edge.
            want_x = right - width if (left + right) / 2 > screen_width / 2 else left
            want_y = bottom - height if (top + bottom) / 2 > screen_height / 2 else top
            origin_x, origin_y, total_width, total_height = self._screen()
            if origin_x <= want_x <= origin_x + total_width - 60 and origin_y <= want_y <= origin_y + total_height - 20:
                x, y = want_x, want_y                                          # still on a connected screen
        except (KeyError, TypeError, ValueError):
            pass
        root.geometry("+%d+%d" % (x, y))

    # -- data -----------------------------------------------------------------

    def refresh(self):
        self._wake.set()

    def _worker(self):
        while not self._stop.is_set():
            try:
                self.engine.discover()
                self.engine.scan_all()
                self._results.put((self.engine.summary(), cu.read_rate_limits(self.feed), self.engine.last_event, None))
            except Exception as error:                       # keep the widget alive whatever the scan hits
                self._results.put((None, None, 0.0, str(error)))
            self._wake.wait(REFRESH_SECONDS)
            self._wake.clear()

    def _poll(self):
        latest = None
        while True:
            try:
                latest = self._results.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            summary, limits, last_event, error = latest
            if error:
                self.footer.configure(text="scan error: %s" % error[:60])
            else:
                self.render(summary, limits, last_event)
        if not self._stop.is_set():
            self.root.after(250, self._poll)

    def render(self, summary, limits, last_event=0.0, now=None):
        before = self._box()
        self._fill(summary, limits, last_event, now)
        if self._drag_from is None:
            self._reanchor(before)

    def _fill(self, summary, limits, last_event=0.0, now=None):
        now = time.time() if now is None else now
        self.summary, self.limits, self._last_event = summary, limits, last_event

        for _caption, key in self.ROWS:
            cost, tokens = self.cells[key]
            cost.configure(text=cu.fmt_cost(summary[key]["cost"]))
            tokens.configure(text=cu.fmt_tokens(summary[key]["tokens"]))
        self.split.configure(text=cu.model_split(summary["today"]) or "no usage yet today")

        # Rows and pill items are built once and then updated in place. Rebuilding them on every
        # refresh would restart the gauge animations each time and make tooltips flicker.
        windows = (limits or {}).get("windows", [])
        order = [window["name"] for window in windows]
        if order != self._row_order:
            self._row_order = order
            for child in self.limit_rows.winfo_children():
                child.destroy()
            self._rows = {window["name"]: self._new_row(window) for window in windows}
        for window in windows:
            self._update_row(self._rows[window["name"]], window, now)

        if limits and limits["stale"]:
            read_at = time.localtime(limits["updated"])
            same_day = read_at[:3] == time.localtime(now)[:3]
            self.as_of.configure(text="limits as of " + time.strftime("%H:%M" if same_day else "%a %H:%M", read_at))
            self.as_of.pack(fill="x", after=self.limit_rows)
        else:
            self.as_of.pack_forget()

        live = now - last_event <= 120
        for canvas in (self.dot, self.pill_dot):
            canvas.itemconfigure("dot", fill=ACCENT if live else IDLE)
        self._fill_pill(summary, limits, windows)
        self.footer.configure(text=self.engine.pricing.warning or time.strftime("updated %H:%M:%S", time.localtime(now)))

    def _new_row(self, window):
        """Label on the left; ring, then "NN% left . resets 3d 4h" on the right; and a bar underneath.
        Ring and bar both drain as the allowance is used, and turn red for the last 15%."""
        row = {"tip": ""}
        frame = tk.Frame(self.limit_rows, bg=CARD)
        frame.pack(fill="x", pady=(6, 0))
        name = tk.Label(frame, text=window["label"], bg=CARD, fg=LABEL, font=self.f_label)
        name.pack(side="left")
        row["value"] = tk.Label(frame, text="", bg=CARD, fg=DIM, font=self.f_dim)
        row["value"].pack(side="right", padx=(2, 0))
        ring = Ring(frame, diameter=13, thickness=2.4)
        ring.widget.pack(side="right", padx=(9, 0))
        bar = Bar(self.limit_rows)
        bar.widget.pack(fill="x", pady=(2, 0))
        row["gauges"] = (ring, bar)
        for widget in (name, row["value"], ring.widget, bar.widget):
            Tooltip(widget, lambda row=row: row["tip"], self.f_small)
        return row

    def _update_row(self, row, window, now):
        text = "%.0f%% left" % window["left"]
        tip = "%.0f%% used" % window["used"]
        if window["resets"]:
            text += " %s resets %s" % (cu.DOT, cu.fmt_span(window["resets"] - now))
            tip += ", resets " + time.strftime("%a %d %b %H:%M", time.localtime(window["resets"]))
        row["value"].configure(text=text)
        row["tip"] = tip
        for gauge in row["gauges"]:
            gauge.set(window["left"])

    def _fill_pill(self, summary, limits, windows):
        """The pill shows a ring and "5h 84%" per limit, or the cost headline when there is no feed."""
        short = {"five_hour": "5h", "seven_day": "wk"}
        shown = [w for w in windows if w["name"] in short] or windows[:2]
        order = [w["name"] for w in shown]
        if order != self._pill_order:
            self._pill_order = order
            for child in self.pill_items.winfo_children():
                child.destroy()
            self._pill_parts = {}
            for index, window in enumerate(shown):
                ring = Ring(self.pill_items)
                ring.widget.pack(side="left", padx=(7 if index else 0, 2))
                label = tk.Label(self.pill_items, text="", bg=CARD, fg=VALUE, font=(self.f_dim[0], 9))
                label.pack(side="left")
                self._pill_parts[window["name"]] = {"label": label, "gauges": (ring,)}
            if shown:
                tk.Label(self.pill_items, text="left", bg=CARD, fg=FAINT, font=(self.f_dim[0], 9)).pack(side="left", padx=(6, 0))
            for widget in self.pill_items.winfo_children():
                Tooltip(widget, self._pill_tip, self.f_small)

        if shown:
            for window in shown:
                part = self._pill_parts[window["name"]]
                part["label"].configure(text="%s %.0f%%" % (short.get(window["name"], window["label"]), window["left"]),
                                        fg=HOT if window["left"] <= cu.LOW_PERCENT else VALUE)
                part["gauges"][0].set(window["left"])
            self.pill_text.pack_forget()
            self.pill_items.pack(side="left")
        else:
            self.pill_items.pack_forget()
            self.pill_text.configure(text=cu.oneline(summary, limits))
            self.pill_text.pack(side="left")

    def _bucket_tip(self, key):
        if not self.summary:
            return ""
        bucket = self.summary[key]
        lines = ["{:,} responses".format(bucket["responses"]),
                 "input  " + cu.fmt_tokens(bucket["input"]), "output  " + cu.fmt_tokens(bucket["output"]),
                 "cache write  " + cu.fmt_tokens(bucket["cache_write"]), "cache read  " + cu.fmt_tokens(bucket["cache_read"])]
        lines += ["%s  %s" % (label, cu.fmt_cost(cost)) for label, cost in sorted(bucket["by_model"].items(), key=lambda i: -i[1])]
        return "\n".join(lines)

    def _pill_tip(self):
        if not self.summary:
            return ""
        lines = cu.limit_lines(self.limits)
        lines.append("Last 5h %s   Today %s   7 days %s" % tuple(cu.fmt_cost(self.summary[k]["cost"]) for k in ("h5", "today", "d7")))
        lines.append("Double-click to expand")
        return "\n".join(lines)

    def quit(self):
        self._stop.set()
        self._wake.set()
        self._save_state()
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Floating Claude Code usage widget.")
    parser.add_argument("--decorated", action="store_true", help="keep the normal window frame")
    parser.add_argument("--projects-root", help="transcripts folder (default: ~/.claude/projects)")
    parser.add_argument("--pricing", help="path to pricing.json")
    args = parser.parse_args(argv)

    root = tk.Tk()
    WidgetApp(root, cu.UsageEngine(args.projects_root, cu.load_pricing(args.pricing)), decorated=args.decorated)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
