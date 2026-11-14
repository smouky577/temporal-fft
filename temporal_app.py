"""Temporal FFT — pick a clip and where the result goes.

The command-line script needed the file names up front. This window asks.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageTk

from temporal import Stopped, _progress_line, clip_needs_chunks, filter_clip, load_frames, render_clip, write_video

BG = "#10120f"
RAISED = "#242824"
TEXT = "#f3f1e8"
MUTED = "#9aa194"
ACCENT = "#d6f25c"
DARK = "#16180f"
TROUGH = "#2c312b"
RED = "#ff4d3a"
LINE = "#31362f"
ABOUT_EN = "Donate to Dr. Baysinger. He promises to spend it on hookers and beer."
ABOUT_BG = "Дарете на Dr. Baysinger. Той обещава да ги изхарчи за курви и бело."
REVOLUT = "revolut.me/georgi09qc"
BTC = (
    "bc1qr84akpfry7sjs6759wwq722fyert9apcarpst6",
    "bc1p6hp5q20u9t9upsjt7425wgqdrs0cr8wnnlf9atu24z92pzsnq85q62uc8e",
)

MODES = ("highpass", "lowpass", "bandpass", "spectrum")
SAVE_TYPES = (("MP4", "*.mp4"), ("AVI", "*.avi"))

UI = {
    "bg": {
        "hint": "бързи  ·  бавни  ·  лента  ·  спектър  ·  размер",
        "placeholder": "Отвори клип\nПосле избери къде да се запише резултатът",
        "detail": "Отвори клип. Резултатът се записва където посочиш.",
        "highpass": "Бързи",
        "lowpass": "Бавни",
        "bandpass": "Лента",
        "spectrum": "Спектър",
        "size": "Размер",
        "gray": "Черно-бяло",
        "gray_on": "Черно-бяло ВКЛ",
        "colors": "Цветове",
        "colors_on": "Цветове ВКЛ",
        "original": "Оригинал",
        "original_on": "Оригинал ВКЛ",
        "whole": "Цял клип",
        "whole_on": "Цял клип ВКЛ",
        "play": "Пусни",
        "stop": "Спри",
        "open": "Отвори клип",
        "run": "Обработи",
        "cancel": "Отказ",
        "cutoff": "Граница",
        "low": "Долна",
        "high": "Горна",
        "gain": "Усилване",
        "width": "Ширина",
        "length": "Дължина",
        "original_size": "оригинал",
        "whole_clip": "цял клип",
        "sec": "сек",
        "fps": "кадъра/сек",
        "open_title": "Избери клип",
        "save_title": "Къде да се запише",
        "video": "Видео",
        "all_files": "Всички файлове",
        "cannot_open": "Не мога да отворя този клип.",
        "no_frames": "Клипът няма кадри.",
        "reading": "Чете клипа…",
        "saving": "Записва {done}/{total}",
        "done": "Готово: {name}  ·  {note}",
        "spectrum_note": "спектър",
        "silent_note": "без звук",
        "error": "Грешка",
        "stopped": "Спряно.",
    },
    "en": {
        "hint": "fast  ·  slow  ·  band  ·  spectrum  ·  size",
        "placeholder": "Open a clip\nThen choose where the result is saved",
        "detail": "Open a clip. The result is saved where you choose.",
        "highpass": "Fast",
        "lowpass": "Slow",
        "bandpass": "Band",
        "spectrum": "Spectrum",
        "size": "Size",
        "gray": "Black & white",
        "gray_on": "Black & white ON",
        "colors": "Colors",
        "colors_on": "Colors ON",
        "original": "Original",
        "original_on": "Original ON",
        "whole": "Whole clip",
        "whole_on": "Whole clip ON",
        "play": "Play",
        "stop": "Stop",
        "open": "Open clip",
        "run": "Process",
        "cancel": "Cancel",
        "cutoff": "Cutoff",
        "low": "Low",
        "high": "High",
        "gain": "Gain",
        "width": "Width",
        "length": "Length",
        "original_size": "original",
        "whole_clip": "whole clip",
        "sec": "sec",
        "fps": "fps",
        "open_title": "Choose a clip",
        "save_title": "Where to save",
        "video": "Video",
        "all_files": "All files",
        "cannot_open": "Cannot open this clip.",
        "no_frames": "The clip has no frames.",
        "reading": "Reading the clip…",
        "saving": "Saving {done}/{total}",
        "done": "Done: {name}  ·  {note}",
        "spectrum_note": "spectrum",
        "silent_note": "no audio",
        "error": "Error",
        "stopped": "Stopped.",
    },
}


def paint_flag(kind: str) -> Image.Image:
    """Small flag for the language currently on screen."""
    width, height = 48, 30
    if kind == "bg":
        image = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        stripe = height // 3
        draw.rectangle((0, stripe, width, stripe * 2), fill=(0, 150, 110))
        draw.rectangle((0, stripe * 2, width, height), fill=(214, 38, 18))
    else:
        image = Image.new("RGB", (width, height), (1, 33, 105))
        draw = ImageDraw.Draw(image)
        white = (255, 255, 255)
        red = (200, 16, 46)
        draw.line((0, 0, width, height), fill=white, width=8)
        draw.line((0, height, width, 0), fill=white, width=8)
        draw.line((0, 0, width, height), fill=red, width=3)
        draw.line((0, height, width, 0), fill=red, width=3)
        draw.rectangle((width // 2 - 6, 0, width // 2 + 6, height), fill=white)
        draw.rectangle((0, height // 2 - 5, width, height // 2 + 5), fill=white)
        draw.rectangle((width // 2 - 3, 0, width // 2 + 3, height), fill=red)
        draw.rectangle((0, height // 2 - 2, width, height // 2 + 2), fill=red)
    framed = Image.new("RGB", (width + 2, height + 2), LINE)
    framed.paste(image, (1, 1))
    return framed


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def media_dir() -> str:
    videos = os.path.join(os.path.expanduser("~"), "Videos")
    if os.path.isdir(videos):
        return videos
    return os.path.expanduser("~")


def to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    out = np.empty_like(frame)
    for index in range(frame.shape[0]):
        gray = cv2.cvtColor(frame[index], cv2.COLOR_BGR2GRAY)
        out[index] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return out


def fit_display(image: np.ndarray, box_w: int, box_h: int) -> np.ndarray:
    height, width = image.shape[:2]
    if box_w < 2 or box_h < 2 or width < 1 or height < 1:
        return image
    scale = min(box_w / width, box_h / height)
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))
    if new_w == width and new_h == height:
        return image
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    return cv2.resize(image, (new_w, new_h), interpolation=interp)


def show_about(root: tk.Tk) -> None:
    window = tk.Toplevel(root)
    window.title("About")
    window.configure(bg=BG)
    window.resizable(False, False)
    window.transient(root)
    frame = tk.Frame(window, bg=BG, padx=18, pady=16)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="About", bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold"), anchor="w").pack(fill="x")
    tk.Label(
        frame, text=ABOUT_EN, bg=BG, fg=TEXT, font=("Segoe UI", 11), wraplength=640, justify="left", anchor="w"
    ).pack(fill="x", pady=(16, 8))
    tk.Label(
        frame, text=ABOUT_BG, bg=BG, fg=TEXT, font=("Segoe UI", 11), wraplength=640, justify="left", anchor="w"
    ).pack(fill="x")
    tk.Frame(frame, bg=LINE, height=1).pack(fill="x", pady=14)
    tk.Label(frame, text="Revolut", bg=BG, fg=TEXT, font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x")
    note = tk.Label(frame, text="", bg=BG, fg=ACCENT, font=("Segoe UI", 9), anchor="w")

    def copy(value: str) -> None:
        root.clipboard_clear()
        root.clipboard_append(value)
        note.configure(text="Copied")

    def link(value: str) -> None:
        label = tk.Label(
            frame, text=value, bg=BG, fg=ACCENT, font=("Segoe UI", 10), cursor="hand2", anchor="w", justify="left"
        )
        label.pack(fill="x", pady=(6, 0))
        label.bind("<Button-1>", lambda _event: copy(value))

    link(REVOLUT)
    tk.Label(frame, text="BTC address", bg=BG, fg=TEXT, font=("Segoe UI", 12, "bold"), anchor="w").pack(
        fill="x", pady=(16, 0)
    )
    for address in BTC:
        link(address)
    note.pack(fill="x", pady=(14, 0))
    window.update_idletasks()
    width, height = 700, window.winfo_reqheight()
    parent_x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
    parent_y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 2)
    window.geometry(f"{width}x{height}+{parent_x}+{parent_y}")
    window.grab_set()
    window.focus_set()


class TemporalApp:
    def __init__(self, visible: bool = True) -> None:
        enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Temporal FFT")
        self.root.configure(bg=BG)
        self.root.minsize(920, 700)
        try:
            ppi = float(self.root.winfo_fpixels("1i"))
            if ppi > 1:
                self.root.tk.call("tk", "scaling", ppi / 72.0)
        except tk.TclError:
            pass

        self.path = ""
        self.panel = "highpass"
        self.busy = False
        self.cancel_flag = False
        self.frames: np.ndarray | None = None
        self.source_count = 0
        self.play_index = 0
        self.play_fps = 30.0
        self.playing = False
        self.play_after = None
        self.capture = None
        self.capture_pos = -1
        self._capture_path = ""
        self.view_path = ""
        self._show_result = False
        self._seeking = False
        self.photo = None
        self.image_id = None
        self._shown = None
        self.gray = tk.BooleanVar(value=False)
        self.colors = tk.BooleanVar(value=False)
        self.lang = "bg"
        self.clip_info = None
        self._flag_image = None
        self._ready = False

        self._build()
        self._place_window()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._ready = True
        self._apply_mode()
        self._apply_language()
        if visible:
            self.root.deiconify()
            self.root.lift()

    def _place_window(self) -> None:
        width, height = 1180, 780
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = tk.Frame(self.root, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 0))
        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left", anchor="w")
        tk.Label(title_box, text="TEMPORAL", bg=BG, fg=ACCENT, font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(title_box, text="  FFT", bg=BG, fg=TEXT, font=("Segoe UI", 18)).pack(side="left")
        self.hint = tk.Label(
            header,
            text="",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        self.hint.pack(side="left", padx=(16, 0), pady=(8, 0))
        credit = tk.Label(
            header,
            text="by Dr. Baysinger",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        credit.pack(side="right")
        credit.bind("<Button-1>", lambda _event: show_about(self.root))
        self.flag_btn = tk.Label(header, bg=BG, cursor="hand2", bd=0)
        self.flag_btn.pack(side="right", padx=(0, 12))
        self.flag_btn.bind("<Button-1>", lambda _event: self._toggle_language())

        rule = tk.Frame(self.root, bg=ACCENT, height=2)
        rule.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 0))
        rule.grid_propagate(False)

        self.preview = tk.Frame(self.root, bg="#070807")
        self.preview.grid(row=1, column=0, sticky="nsew", padx=18, pady=(8, 0))
        self.canvas = tk.Canvas(
            self.preview,
            bg="#070807",
            highlightthickness=0,
            bd=0,
            width=800,
            height=180,
        )
        self.canvas.pack(fill="both", expand=True)
        self.placeholder = self.canvas.create_text(
            450,
            210,
            text="",
            fill=MUTED,
            font=("Segoe UI", 13),
            justify="center",
        )
        self.canvas.bind("<Configure>", lambda _e: self._draw())

        self.seek_row = tk.Frame(self.root, bg=BG)
        self.seek_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(8, 0))
        times = tk.Frame(self.seek_row, bg=BG)
        times.pack(fill="x")
        self.time_now = tk.Label(times, text="0:00", bg=BG, fg=MUTED, font=("Segoe UI", 9), width=6, anchor="w")
        self.time_now.pack(side="left")
        self.seek_var = tk.IntVar(value=0)
        self.seek = self._scale(times, self.seek_var, 0, 1, self._on_seek, TROUGH)
        self.seek.pack(side="left", fill="x", expand=True, padx=8)
        self.time_end = tk.Label(times, text="0:00", bg=BG, fg=MUTED, font=("Segoe UI", 9), width=6, anchor="e")
        self.time_end.pack(side="right")
        self.play_btn = self._button(self.seek_row, "Пусни", self._toggle_play)
        self.play_btn.pack(fill="x", pady=(6, 0))
        self.seek_row.grid_remove()

        buttons = tk.Frame(self.root, bg=BG)
        buttons.grid(row=4, column=0, sticky="ew", padx=18, pady=(10, 0))
        self.open_btn = self._button(buttons, "Отвори клип", self._on_open, primary=True)
        self.run_btn = self._button(buttons, "Обработи", self._on_run)
        self.cancel_btn = self._button(buttons, "Отказ", self._on_cancel)
        self.open_btn.pack(side="left", padx=(0, 8))
        self.run_btn.pack(side="left", padx=(0, 8))
        self.run_btn.configure(state="disabled")

        modes = tk.Frame(self.root, bg=BG)
        modes.grid(row=5, column=0, sticky="ew", padx=18, pady=(8, 0))
        self.mode_var = tk.StringVar(value="highpass")
        self.mode_buttons = {}
        for key in MODES:
            button = self._button(modes, key, lambda key=key: self._set_mode(key))
            button.pack(side="left", padx=(0, 8))
            self.mode_buttons[key] = button
        self.fit_btn = self._button(modes, "Размер", self._show_size)
        self.fit_btn.pack(side="left", padx=(0, 8))
        self.gray_btn = self._button(modes, "Черно-бяло", self._toggle_gray)
        self.colors_btn = self._button(modes, "Цветове", self._toggle_colors)
        self.size_btn = self._button(modes, "Оригинал", self._toggle_size)
        self.clip_btn = self._button(modes, "Цял клип", self._toggle_clip)
        self.gray_btn.pack(side="left", padx=(16, 8))
        self.colors_btn.pack(side="left", padx=(0, 8))
        self.size_btn.pack(side="left", padx=(0, 8))
        self.clip_btn.pack(side="left")

        self.slider_dock = tk.Frame(self.root, bg=BG, height=126)
        self.slider_dock.grid(row=6, column=0, sticky="ew", padx=18, pady=(8, 0))
        self.slider_dock.grid_propagate(False)
        self.slider_dock.columnconfigure(0, weight=1)
        for slot in range(3):
            self.slider_dock.rowconfigure(slot, weight=1, uniform="slider")

        self.cutoff_var = tk.DoubleVar(value=2.0)
        self.cutoff_row, self.cutoff_caption, self.cutoff_value = self._slider_row("cutoff", self.cutoff_var, 0.2, 12, 0.1)
        self.low_var = tk.DoubleVar(value=0.5)
        self.low_row, self.low_caption, self.low_value = self._slider_row("low", self.low_var, 0.1, 12, 0.1)
        self.high_var = tk.DoubleVar(value=3.0)
        self.high_row, self.high_caption, self.high_value = self._slider_row("high", self.high_var, 0.2, 16, 0.1)
        self.gain_var = tk.DoubleVar(value=3.0)
        self.gain_row, self.gain_caption, self.gain_value = self._slider_row("gain", self.gain_var, 0.5, 8, 0.1)
        self.width_var = tk.IntVar(value=480)
        self.width_row, self.width_caption, self.width_value = self._slider_row("width", self.width_var, 320, 1920, 16)
        self.seconds_var = tk.IntVar(value=10)
        self.seconds_row, self.seconds_caption, self.seconds_value = self._slider_row("length", self.seconds_var, 1, 60, 1)
        self.slider_rows = (
            self.cutoff_row,
            self.low_row,
            self.high_row,
            self.gain_row,
            self.width_row,
            self.seconds_row,
        )
        self.full_size = tk.BooleanVar(value=False)
        self.full_clip = tk.BooleanVar(value=False)

        status = tk.Frame(self.root, bg=BG)
        status.grid(row=13, column=0, sticky="ew", padx=18, pady=(8, 14))
        self.detail = tk.Label(
            status,
            text="",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.detail.pack(side="left", fill="x", expand=True)
        self.message = tk.Label(status, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="e")
        self.message.pack(side="right")

    def _caption(self, parent: tk.Misc, text: str) -> tk.Label:
        return tk.Label(parent, text=text, bg=BG, fg=MUTED, font=("Segoe UI", 10), width=16, anchor="w")

    def _button(self, parent: tk.Misc, text: str, command, primary: bool = False) -> tk.Button:
        bg = ACCENT if primary else RAISED
        fg = DARK if primary else TEXT
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            disabledforeground="#6e726c",
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            font=("Segoe UI", 10),
            cursor="hand2",
            takefocus=0,
        )

    def _scale(self, parent, variable, start, end, command, trough, resolution=1):
        return tk.Scale(
            parent,
            from_=start,
            to=end,
            orient="horizontal",
            showvalue=False,
            variable=variable,
            resolution=resolution,
            command=command,
            bg=parent["bg"],
            fg=TEXT,
            troughcolor=trough,
            activebackground=ACCENT,
            highlightthickness=0,
            bd=0,
            sliderrelief="flat",
            sliderlength=16,
            width=12,
            cursor="hand2",
            takefocus=0,
        )

    def _slider_row(self, key: str, variable, start, end, resolution):
        row = tk.Frame(self.slider_dock, bg=BG)
        caption = self._caption(row, key)
        caption.pack(side="left")
        scale = self._scale(row, variable, start, end, lambda _v: self._refresh_limits(), TROUGH, resolution)
        scale.pack(side="left", fill="x", expand=True, padx=12)
        value = tk.Label(row, text="", bg=BG, fg=TEXT, font=("Segoe UI", 10), width=28, anchor="e")
        value.pack(side="right")
        return row, caption, value

    def t(self, key: str) -> str:
        return UI["en" if self.lang == "en" else "bg"][key]

    def _toggle_language(self) -> None:
        self.lang = "en" if self.lang == "bg" else "bg"
        self._apply_language()

    def _show_flag(self) -> None:
        kind = "bg" if self.lang == "bg" else "uk"
        self._flag_image = ImageTk.PhotoImage(paint_flag(kind))
        self.flag_btn.configure(image=self._flag_image)

    def _apply_language(self) -> None:
        self.hint.configure(text=self.t("hint"))
        self.canvas.itemconfigure(self.placeholder, text=self.t("placeholder"))
        for key, button in self.mode_buttons.items():
            button.configure(text=self.t(key))
        self.open_btn.configure(text=self.t("open"))
        self.run_btn.configure(text=self.t("run"))
        self.cancel_btn.configure(text=self.t("cancel"))
        self.cutoff_caption.configure(text=self.t("cutoff"))
        self.low_caption.configure(text=self.t("low"))
        self.high_caption.configure(text=self.t("high"))
        self.gain_caption.configure(text=self.t("gain"))
        self.width_caption.configure(text=self.t("width"))
        self.seconds_caption.configure(text=self.t("length"))
        self._paint_size()
        self._show_flag()
        self._paint_toggles()
        self._paint_play()
        if self.clip_info:
            self._paint_info()
        elif not self.path:
            self.detail.configure(text=self.t("detail"))
        if self._ready:
            self._refresh_limits()

    def _paint_play(self) -> None:
        if self.playing:
            self._paint(self.play_btn, ACCENT, DARK, self.t("stop"))
        else:
            self._paint(self.play_btn, RAISED, TEXT, self.t("play"))

    def _paint_info(self) -> None:
        info = self.clip_info
        if not info:
            self.detail.configure(text=self.t("detail"))
            return
        bits = [info["name"], f"{info['width']}×{info['height']}"]
        if info["fps"] > 1:
            bits.append(f"{info['fps']:.2f} {self.t('fps')}")
        if info["seconds"]:
            bits.append(f"{info['seconds']:.1f} {self.t('sec')}")
        self.detail.configure(text="    ".join(bits))

    def _paint(self, button: tk.Button, bg: str, fg: str, text: str) -> None:
        button.configure(text=text, bg=bg, fg=fg, activebackground=bg, activeforeground=fg)

    def _toggle_size(self) -> None:
        if self.busy:
            return
        self.full_size.set(not self.full_size.get())
        self._paint_toggles()
        self._refresh_limits()

    def _toggle_clip(self) -> None:
        if self.busy:
            return
        self.full_clip.set(not self.full_clip.get())
        self._paint_toggles()
        self._refresh_limits()

    def _paint_toggles(self) -> None:
        if self.full_size.get():
            self._paint(self.size_btn, ACCENT, DARK, self.t("original_on"))
        else:
            self._paint(self.size_btn, RAISED, TEXT, self.t("original"))
        if self.full_clip.get():
            self._paint(self.clip_btn, ACCENT, DARK, self.t("whole_on"))
        else:
            self._paint(self.clip_btn, RAISED, TEXT, self.t("whole"))
        if self.gray.get():
            self._paint(self.gray_btn, ACCENT, DARK, self.t("gray_on"))
        else:
            self._paint(self.gray_btn, RAISED, TEXT, self.t("gray"))
        if self.colors.get():
            self._paint(self.colors_btn, ACCENT, DARK, self.t("colors_on"))
        else:
            self._paint(self.colors_btn, RAISED, TEXT, self.t("colors"))

    def _toggle_gray(self) -> None:
        if self.busy:
            return
        self.gray.set(not self.gray.get())
        if self.gray.get():
            self.colors.set(False)
        self._paint_toggles()
        self._draw()

    def _toggle_colors(self) -> None:
        if self.busy:
            return
        self.colors.set(not self.colors.get())
        if self.colors.get():
            self.gray.set(False)
        self._paint_toggles()
        self._draw()

    def _set_mode(self, key: str) -> None:
        if self.busy:
            return
        self.mode_var.set(key)
        self.panel = key
        self._apply_mode()

    def _show_size(self) -> None:
        if self.busy:
            return
        self.panel = "size"
        self._apply_mode()

    def _paint_size(self) -> None:
        if getattr(self, "fit_btn", None) is None:
            return
        if self.panel == "size":
            self._paint(self.fit_btn, ACCENT, DARK, self.t("size"))
        else:
            self._paint(self.fit_btn, RAISED, TEXT, self.t("size"))

    def _apply_mode(self) -> None:
        mode = self.mode_var.get()
        for key, button in self.mode_buttons.items():
            if key == mode:
                button.configure(bg=ACCENT, fg=DARK, activebackground=ACCENT, activeforeground=DARK)
            else:
                button.configure(bg=RAISED, fg=TEXT, activebackground=RAISED, activeforeground=TEXT)
        shown = {
            "highpass": (self.cutoff_row, self.gain_row),
            "lowpass": (self.cutoff_row,),
            "bandpass": (self.low_row, self.high_row, self.gain_row),
            "spectrum": (self.low_row, self.high_row, self.gain_row),
            "size": (self.width_row, self.seconds_row),
        }[self.panel]
        for row in self.slider_rows:
            row.grid_remove()
        for index, row in enumerate(shown):
            row.grid(row=index, column=0, sticky="ew")
        self._paint_size()
        self._paint_toggles()
        self._refresh_limits()

    def _refresh_limits(self) -> None:
        if not self._ready:
            return
        self.cutoff_value.configure(text=f"{self.cutoff_var.get():.1f} Hz")
        low = min(self.low_var.get(), self.high_var.get())
        high = max(self.low_var.get(), self.high_var.get())
        self.low_value.configure(text=f"{low:.1f} Hz")
        self.high_value.configure(text=f"{high:.1f} Hz")
        self.gain_value.configure(text=f"{self.gain_var.get():.1f}×")
        if self.full_size.get():
            self.width_value.configure(text=self.t("original_size"))
        else:
            self.width_value.configure(text=f"{int(self.width_var.get())} px")
        if self.full_clip.get():
            self.seconds_value.configure(text=self.t("whole_clip"))
        else:
            self.seconds_value.configure(text=f"{int(self.seconds_var.get())} {self.t('sec')}")

    def _on_open(self) -> None:
        if self.busy:
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title=self.t("open_title"),
            initialdir=os.path.dirname(self.path) if self.path else media_dir(),
            filetypes=(
                (self.t("video"), "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
                (self.t("all_files"), "*.*"),
            ),
        )
        if not path:
            return
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            messagebox.showerror("Temporal FFT", self.t("cannot_open"))
            return
        ok, frame = capture.read()
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if not ok or frame is None:
            capture.release()
            messagebox.showerror("Temporal FFT", self.t("no_frames"))
            return
        self._stop_play()
        self._close_capture()
        self.path = path
        self.view_path = path
        self._capture_path = path
        self.frames = None
        self.capture = capture
        self.capture_pos = 1
        self.source_count = max(count, 1)
        self.play_fps = fps if fps > 1 else 30.0
        self.play_index = 0
        last = max(0, self.source_count - 1)
        self.seek.configure(to=max(1, last))
        self._seeking = True
        self.seek_var.set(0)
        self._seeking = False
        self._show_bgr(frame)
        self._paint_time()
        if last > 0:
            self.seek_row.grid()
        else:
            self.seek_row.grid_remove()
        seconds = (count / fps) if fps > 1 and count > 0 else 0
        height, width = frame.shape[:2]
        self.clip_info = {
            "name": os.path.basename(path),
            "width": width,
            "height": height,
            "fps": fps,
            "seconds": seconds,
        }
        self._paint_info()
        self.message.configure(text="", fg=MUTED)
        self.run_btn.configure(state="normal")

    def _clock(self, frame: int) -> str:
        fps = self.play_fps if self.play_fps >= 1 else 30.0
        total = max(0, int(frame / fps))
        return f"{total // 60}:{total % 60:02d}"

    def _play_count(self) -> int:
        if self.frames is not None:
            return int(self.frames.shape[0])
        return int(self.source_count)

    def _close_capture(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None
            self.capture_pos = -1

    def _frame_at(self, index: int) -> np.ndarray | None:
        if self.frames is not None:
            return self.frames[index]
        media = self.view_path or self.path
        if not media:
            return None
        if self.capture is None or not self.capture.isOpened() or self._capture_path != media:
            self._close_capture()
            self.capture = cv2.VideoCapture(media)
            self._capture_path = media
            self.capture_pos = 0
        if self.capture_pos != index:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if not ok or frame is None:
            return None
        self.capture_pos = index + 1
        return frame

    def _move_to(self, index: int) -> None:
        count = self._play_count()
        if count <= 0:
            return
        index = max(0, min(index, count - 1))
        frame = self._frame_at(index)
        if frame is None:
            return
        self.play_index = index
        if int(self.seek_var.get()) != index:
            self._seeking = True
            self.seek_var.set(index)
            self._seeking = False
        self._show_bgr(frame)
        self._paint_time()

    def _paint_time(self) -> None:
        count = self._play_count()
        if count <= 0:
            return
        self.time_now.configure(text=self._clock(self.play_index))
        self.time_end.configure(text=self._clock(count))

    def _toggle_play(self) -> None:
        if self.busy or self._play_count() < 2:
            return
        if self.playing:
            self._stop_play()
            return
        self.playing = True
        self._paint_play()
        self._play_tick()

    def _stop_play(self) -> None:
        self.playing = False
        if self.play_after is not None:
            self.root.after_cancel(self.play_after)
            self.play_after = None
        if getattr(self, "play_btn", None) is not None:
            self._paint_play()

    def _play_tick(self) -> None:
        if not self.playing:
            return
        count = self._play_count()
        if count < 2:
            self._stop_play()
            return
        nxt = 0 if self.play_index >= count - 1 else self.play_index + 1
        self._move_to(nxt)
        delay = max(16, int(1000 / max(1.0, min(60.0, self.play_fps))))
        self.play_after = self.root.after(delay, self._play_tick)

    def _on_seek(self, _value=None) -> None:
        if self._seeking:
            return
        self._move_to(int(self.seek_var.get()))

    def _on_close(self) -> None:
        self._stop_play()
        self._close_capture()
        self.root.destroy()

    def _show_bgr(self, frame: np.ndarray) -> None:
        self._shown = frame
        self._draw()

    def _draw(self) -> None:
        frame = getattr(self, "_shown", None)
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if frame is None:
            self.canvas.coords(self.placeholder, width // 2, height // 2)
            self.canvas.itemconfigure(self.placeholder, state="normal")
            if self.image_id is not None:
                self.canvas.itemconfigure(self.image_id, state="hidden")
            return
        view = to_gray(frame) if self.gray.get() else frame
        fitted = fit_display(cv2.cvtColor(view, cv2.COLOR_BGR2RGB), width - 12, height - 12)
        image = Image.fromarray(fitted)
        self.photo = ImageTk.PhotoImage(image=image)
        cx, cy = width // 2, height // 2
        if self.image_id is None:
            self.image_id = self.canvas.create_image(cx, cy, image=self.photo)
        else:
            self.canvas.itemconfigure(self.image_id, image=self.photo, state="normal")
            self.canvas.coords(self.image_id, cx, cy)
        self.canvas.itemconfigure(self.placeholder, state="hidden")

    def _on_cancel(self) -> None:
        self.cancel_flag = True

    def _on_run(self) -> None:
        if self.busy or not self.path:
            return
        stem = os.path.splitext(os.path.basename(self.path))[0]
        folder = os.path.dirname(self.path) or media_dir()
        dest = filedialog.asksaveasfilename(
            parent=self.root,
            title=self.t("save_title"),
            initialdir=folder,
            initialfile=f"{stem}-fft.mp4",
            defaultextension=".mp4",
            filetypes=SAVE_TYPES,
        )
        if not dest:
            return
        mode = self.mode_var.get()
        cutoff = float(self.cutoff_var.get())
        low = min(float(self.low_var.get()), float(self.high_var.get()))
        high = max(float(self.low_var.get()), float(self.high_var.get()))
        gain = float(self.gain_var.get())
        max_width = None if self.full_size.get() else int(self.width_var.get())
        max_seconds = None if self.full_clip.get() else float(self.seconds_var.get())
        gray = bool(self.gray.get())
        colors = bool(self.colors.get()) or gray
        self.busy = True
        self.cancel_flag = False
        self._stop_play()
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        if not self.cancel_btn.winfo_ismapped():
            self.cancel_btn.pack(side="left")
        self.message.configure(text=self.t("reading"), fg=MUTED)
        threading.Thread(
            target=self._work,
            args=(self.path, dest, mode, cutoff, low, high, gain, max_width, max_seconds, gray, colors),
            daemon=True,
        ).start()

    def _work(self, src, dest, mode, cutoff, low, high, gain, max_width, max_seconds, gray, colors) -> None:
        try:
            def note(text: str) -> None:
                self._status(text)

            chunked, chunk_frames = clip_needs_chunks(src, max_width, max_seconds)
            if chunked:
                written, clip_fps = render_clip(
                    src,
                    dest,
                    mode,
                    cutoff,
                    low,
                    high,
                    gain,
                    max_width,
                    max_seconds,
                    gray,
                    colors,
                    note,
                    lambda: self.cancel_flag,
                    lang=self.lang,
                    chunk_frames=chunk_frames,
                )
                self.frames = None
            else:
                probe = cv2.VideoCapture(src)
                fps_guess = float(probe.get(cv2.CAP_PROP_FPS) or 30)
                probe.release()
                if fps_guess < 1 or fps_guess > 240:
                    fps_guess = 30.0

                def reading(done: int, total_frames: int) -> None:
                    note(_progress_line("read", done, total_frames if total_frames > 1 else 0, None, None, fps_guess, self.lang))

                video, clip_fps = load_frames(src, max_width, max_seconds, reading, lambda: self.cancel_flag, self.lang)
                count = int(video.shape[0])
                note(_progress_line("compute", count, count, None, None, clip_fps, self.lang))
                result = filter_clip(video, clip_fps, mode, cutoff, low, high, gain, colors)
                del video
                saved = to_gray(result) if gray else result

                def writing(done: int, total_frames: int) -> None:
                    note(self.t("saving").format(done=done, total=total_frames))

                write_video(dest, saved, clip_fps, writing, lambda: self.cancel_flag, self.lang)
                self.frames = result
                written = count
            self.view_path = dest
            self.source_count = written
            self.play_fps = float(clip_fps)
            self._show_result = True
            name = os.path.basename(dest)
            note_text = self.t("spectrum_note") if mode == "spectrum" else self.t("silent_note")
            done_text = self.t("done").format(name=name, note=note_text)
            self.root.after(0, lambda done_text=done_text: self._finish(done_text))
        except Stopped:
            try:
                os.remove(dest)
            except OSError:
                pass
            text = self.t("stopped")
            self.root.after(0, lambda text=text: self._finish(text, error=False))
        except Exception as exc:
            text = str(exc) or self.t("error")
            self.root.after(0, lambda text=text: self._finish(text, error=True))

    def _status(self, text: str) -> None:
        self.root.after(0, lambda: self.message.configure(text=text, fg=MUTED))

    def _finish(self, text: str, error: bool = False) -> None:
        self.busy = False
        self.cancel_btn.pack_forget()
        self.open_btn.configure(state="normal")
        self.run_btn.configure(state="normal" if self.path else "disabled")
        self.message.configure(text=text, fg=RED if error else ACCENT)
        if error:
            messagebox.showerror("Temporal FFT", text)
        if not error and self._show_result:
            self._show_result = False
            self._close_capture()
            self._stop_play()
            last = max(0, self.source_count - 1)
            self.play_index = 0
            self.seek.configure(to=max(1, last))
            self._seeking = True
            self.seek_var.set(0)
            self._seeking = False
            if last > 0:
                self.seek_row.grid()
            else:
                self.seek_row.grid_remove()
            frame = self._frame_at(0)
            if frame is not None:
                self._show_bgr(frame)
            self._paint_time()


def main() -> None:
    if "--self-test" in sys.argv:
        from temporal import _self_test

        _self_test()
        app = TemporalApp(visible=False)
        app.root.update()
        assert app.mode_buttons["highpass"].cget("text") == "Бързи"
        order = [widget.cget("text") for widget in app.gray_btn.master.pack_slaves()]
        assert order == ["Бързи", "Бавни", "Лента", "Спектър", "Размер", "Черно-бяло", "Цветове", "Оригинал", "Цял клип"]
        assert app.flag_btn.cget("image")
        app._toggle_language()
        app.root.update()
        assert app.lang == "en"
        assert app.mode_buttons["highpass"].cget("text") == "Fast"
        assert app.fit_btn.cget("text") == "Size"
        assert app.gray_btn.cget("text") == "Black & white"
        assert app.colors_btn.cget("text") == "Colors"
        assert app.open_btn.cget("text") == "Open clip"
        assert app.cutoff_caption.cget("text") == "Cutoff"
        assert app.hint.cget("text").startswith("fast")
        app._toggle_language()
        assert app.mode_buttons["bandpass"].cget("text") == "Лента"
        app._set_mode("spectrum")
        app.root.update()
        assert app.low_row.grid_info() and app.gain_row.grid_info()
        assert not app.width_row.grid_info()
        app._show_size()
        app.root.update()
        assert app.mode_var.get() == "spectrum"
        assert app.width_row.grid_info() and app.seconds_row.grid_info()
        assert not app.low_row.grid_info()
        assert _progress_line("compute", 10, 20, 3, 8, 10, "en") == "Computing 10/20, row 3/8 · up to 1.0 sec"
        app.root.destroy()
        print("temporal app: ok")
        return
    TemporalApp().root.mainloop()


if __name__ == "__main__":
    main()
