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
        self.split = tk.Label(self.full, text="", bg=CARD, fg=LABEL, font=self.f_small, anchor="w")
        self.split.pack(fill="x", pady=(6, 0))
        self.footer = tk.Label(self.full, text="starting", bg=CARD, fg=FAINT, font=self.f_small, anchor="w")
        self.footer.pack(fill="x", pady=(2, 0))

        self.pill = tk.Frame(self.card, bg=CARD)
        self.pill_dot = self._dot(self.pill)
        self.pill_dot.pack(side="left", padx=(0, 6))
        self.pill_text = tk.Label(self.pill, text="...", bg=CARD, fg=VALUE, font=(self.f_dim[0], 9))
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
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

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
        now = time.time() if now is None else now
        before = self._box()
        self.summary, self.limits = summary, limits

        for _caption, key in self.ROWS:
            cost, tokens = self.cells[key]
            cost.configure(text=cu.fmt_cost(summary[key]["cost"]))
            tokens.configure(text=cu.fmt_tokens(summary[key]["tokens"]))
        self.split.configure(text=cu.model_split(summary["today"]) or "no usage yet today")

        for child in self.limit_rows.winfo_children():
            child.destroy()
        for window in (limits or {}).get("windows", []):
            self._limit_row(window, now)
        if limits and limits["stale"]:
            read_at = time.localtime(limits["updated"])
            same_day = read_at[:3] == time.localtime(now)[:3]
            text = "limits as of " + time.strftime("%H:%M" if same_day else "%a %H:%M", read_at)
            tk.Label(self.limit_rows, text=text, bg=CARD, fg=FAINT, font=self.f_small, anchor="w").pack(fill="x")

        live = now - last_event <= 120
        for canvas in (self.dot, self.pill_dot):
            canvas.itemconfigure("dot", fill=ACCENT if live else IDLE)
        self.pill_text.configure(text=cu.oneline(summary, limits),
                                 fg=HOT if cu.lowest_left(limits) <= cu.LOW_PERCENT else VALUE)
        self.footer.configure(text=self.engine.pricing.warning or time.strftime("updated %H:%M:%S", time.localtime(now)))

        if self._drag_from is None:
            self._reanchor(before)

    def _limit_row(self, window, now):
        """Label on the left, "NN% left . resets 3d 4h" on the right, and a bar that drains as the
        allowance is used (full = plenty left), turning red for the last 15%."""
        text = "%.0f%% left" % window["left"]
        tip = "%.0f%% used" % window["used"]
        if window["resets"]:
            text += " %s resets %s" % (cu.DOT, cu.fmt_span(window["resets"] - now))
            tip += ", resets " + time.strftime("%a %d %b %H:%M", time.localtime(window["resets"]))
        row = tk.Frame(self.limit_rows, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        name = tk.Label(row, text=window["label"], bg=CARD, fg=LABEL, font=self.f_label)
        name.pack(side="left")
        value = tk.Label(row, text=text, bg=CARD, fg=DIM, font=self.f_dim)
        value.pack(side="right", padx=(12, 0))

        # width=1: a Canvas asks for 10cm by default, which would force the whole card wide.
        bar = tk.Canvas(self.limit_rows, width=1, height=4, bg=TRACK, highlightthickness=0)
        bar.pack(fill="x", pady=(2, 0))
        colour = HOT if window["left"] <= cu.LOW_PERCENT else ACCENT
        fraction = window["left"] / 100.0

        def draw(event, canvas=bar, colour=colour, fraction=fraction):
            canvas.delete("fill")
            canvas.create_rectangle(0, 0, int(event.width * fraction), event.height, fill=colour, outline="", tags="fill")

        bar.bind("<Configure>", draw)
        for widget in (name, value, bar):
            Tooltip(widget, lambda tip=tip: tip, self.f_small)

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
