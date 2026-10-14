"""Filttr brand theme for the Tk GUI (colors/fonts from https://www.filttr.org site.css)."""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
from typing import Optional

# Brand tokens (site.css :root)
INK = "#15140F"          # --ink
SIGNAL = "#FFD400"       # --signal (brand yellow)
PAPER = "#F4F0E6"        # --paper
STONE = "#B7AF9C"        # --stone
CARD_DARK = "#201E16"    # --card
HAIR_DARK = "#2A281F"    # --hair
PAPER_CARD = "#FBF9F2"   # --paper-card
PAPER_HAIR = "#E2DBC9"   # --paper-hair

# Derived UI tokens
MUTED = "#6B6556"
SIGNAL_ACTIVE = "#F0C800"
OK = "#1F9D55"
OK_BG = "#E3F4EA"
WARN = "#B7791F"
WARN_BG = "#FCF1D6"
BAD = "#C53030"
BAD_BG = "#FBE4E4"
INFO_BG = "#EEE9DC"

STATUS_COLORS = {
    "ok": (OK, OK_BG),
    "warn": (WARN, WARN_BG),
    "bad": (BAD, BAD_BG),
    "info": (MUTED, INFO_BG),
}


def asset_path(name: str) -> Optional[Path]:
    """Locate a bundled asset (PyInstaller _MEIPASS, exe dir, or repo)."""
    roots = []
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS))  # type: ignore[attr-defined]
    roots.append(Path(sys.executable).parent)
    roots.append(Path(__file__).resolve().parent.parent)
    for r in roots:
        p = r / "assets" / name
        if p.exists():
            return p
    return None


def enable_dpi_awareness() -> None:
    """Crisp text on high-DPI Windows displays (must run before Tk())."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # per-monitor (system) aware
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _pick(root: tk.Misc, candidates: tuple[str, ...]) -> str:
    try:
        families = {f.lower() for f in tkfont.families(root)}
    except tk.TclError:
        families = set()
    for c in candidates:
        if c.lower() in families:
            return c
    return candidates[-1]


class Fonts:
    def __init__(self, root: tk.Misc) -> None:
        body = _pick(root, ("Segoe UI", "Instrument Sans", "DejaVu Sans", "TkDefaultFont"))
        disp = _pick(root, ("Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Semibold", body))
        mono = _pick(root, ("Cascadia Mono", "Consolas", "Geist Mono", "DejaVu Sans Mono", "TkFixedFont"))
        self.body = (body, 10)
        self.body_b = (body, 10, "bold")
        self.small = (body, 9)
        self.title = (disp, 13, "bold")
        self.header = (disp, 15, "bold")
        self.big = (disp, 16, "bold")
        self.button = (body, 10, "bold")
        self.button_big = (body, 12, "bold")
        self.mono = (mono, 9)
        self.icon = (body, 12, "bold")


def apply_theme(root: tk.Tk) -> Fonts:
    f = Fonts(root)
    root.configure(bg=PAPER)
    root.option_add("*Font", f.body)
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", background=PAPER, foreground=INK, font=f.body,
                 bordercolor=PAPER_HAIR, lightcolor=PAPER_CARD, darkcolor=PAPER_HAIR,
                 troughcolor=PAPER, focuscolor=SIGNAL)
    st.configure("TFrame", background=PAPER)
    st.configure("Card.TFrame", background=PAPER_CARD)
    st.configure("Header.TFrame", background=INK)
    st.configure("TLabel", background=PAPER, foreground=INK)
    st.configure("Card.TLabel", background=PAPER_CARD, foreground=INK)
    st.configure("CardMuted.TLabel", background=PAPER_CARD, foreground=MUTED, font=f.small)
    st.configure("CardTitle.TLabel", background=PAPER_CARD, foreground=INK, font=f.title)
    st.configure("CardBig.TLabel", background=PAPER_CARD, foreground=INK, font=f.big)
    st.configure("Header.TLabel", background=INK, foreground=PAPER, font=f.header)
    st.configure("HeaderMuted.TLabel", background=INK, foreground=STONE, font=f.small)
    st.configure("Muted.TLabel", background=PAPER, foreground=MUTED, font=f.small)

    # Buttons
    st.configure("TButton", background=PAPER_CARD, foreground=INK, font=f.button,
                 bordercolor=PAPER_HAIR, padding=(14, 7), relief="flat")
    st.map("TButton",
           background=[("disabled", PAPER), ("pressed", PAPER_HAIR), ("active", "#F1ECDF")],
           foreground=[("disabled", STONE)],
           bordercolor=[("focus", INK)])
    st.configure("Primary.TButton", background=SIGNAL, foreground=INK, font=f.button_big,
                 bordercolor=SIGNAL, lightcolor=SIGNAL, darkcolor=SIGNAL, padding=(28, 12))
    st.map("Primary.TButton",
           background=[("disabled", "#EDE6CF"), ("pressed", SIGNAL_ACTIVE), ("active", SIGNAL_ACTIVE)],
           foreground=[("disabled", STONE)],
           bordercolor=[("disabled", "#EDE6CF"), ("focus", INK)])
    st.configure("Secondary.TButton", background=PAPER_CARD, foreground=BAD, font=f.button,
                 bordercolor=PAPER_HAIR, padding=(18, 12))
    st.map("Secondary.TButton",
           background=[("disabled", PAPER), ("active", BAD_BG)],
           foreground=[("disabled", STONE)])
    st.configure("Header.TButton", background=HAIR_DARK, foreground=PAPER, font=f.button,
                 bordercolor=HAIR_DARK, lightcolor=HAIR_DARK, darkcolor=HAIR_DARK, padding=(12, 6))
    st.map("Header.TButton",
           background=[("pressed", CARD_DARK), ("active", "#3A372B")],
           foreground=[("active", SIGNAL)])
    st.configure("Link.TButton", background=PAPER, foreground=MUTED, font=f.small,
                 bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER, padding=(4, 2))
    st.map("Link.TButton", foreground=[("active", INK)], background=[("active", PAPER)])

    # Inputs
    st.configure("TEntry", fieldbackground="#FFFFFF", foreground=INK, bordercolor=PAPER_HAIR,
                 lightcolor=PAPER_HAIR, darkcolor=PAPER_HAIR, padding=(8, 6), insertcolor=INK)
    st.map("TEntry", bordercolor=[("focus", INK)], lightcolor=[("focus", SIGNAL)],
           fieldbackground=[("disabled", PAPER)])
    st.configure("TRadiobutton", background=PAPER_CARD, foreground=INK)
    st.map("TRadiobutton", background=[("active", PAPER_CARD)], indicatorcolor=[("selected", SIGNAL)])
    st.configure("TCheckbutton", background=PAPER_CARD, foreground=INK)
    st.map("TCheckbutton", background=[("active", PAPER_CARD)], indicatorcolor=[("selected", SIGNAL)])
    st.configure("Vertical.TScrollbar", background=PAPER_HAIR, troughcolor=PAPER_CARD,
                 bordercolor=PAPER_CARD, arrowcolor=MUTED, gripcount=0)
    return f


def set_window_icon(win: tk.Misc) -> None:
    try:
        ico = asset_path("filttr.ico")
        if sys.platform == "win32" and ico:
            win.iconbitmap(default=str(ico))  # type: ignore[call-arg]
            return
        png = asset_path("filttr-icon-64.png")
        if png:
            img = tk.PhotoImage(file=str(png))
            win.iconphoto(True, img)  # type: ignore[attr-defined]
            win._filttr_icon = img  # type: ignore[attr-defined]  # keep reference
    except tk.TclError:
        pass


def load_image(name: str) -> Optional[tk.PhotoImage]:
    p = asset_path(name)
    if not p:
        return None
    try:
        return tk.PhotoImage(file=str(p))
    except tk.TclError:
        return None


class Card(tk.Frame):
    """Paper card with a hairline border and a title row; put content in .body."""

    def __init__(self, master: tk.Misc, title: str, fonts: Fonts, subtitle: str = "") -> None:
        super().__init__(master, bg=PAPER_CARD, highlightthickness=1,
                         highlightbackground=PAPER_HAIR, bd=0)
        head = tk.Frame(self, bg=PAPER_CARD)
        head.pack(fill=tk.X, padx=16, pady=(14, 6))
        tk.Frame(head, bg=SIGNAL, width=4, height=18).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(head, text=title.upper(), style="CardTitle.TLabel").pack(side=tk.LEFT)
        self.head_right = tk.Frame(head, bg=PAPER_CARD)
        self.head_right.pack(side=tk.RIGHT)
        if subtitle:
            ttk.Label(self, text=subtitle, style="CardMuted.TLabel").pack(anchor=tk.W, padx=16)
        self.body = tk.Frame(self, bg=PAPER_CARD)
        self.body.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 14))


class Pill(tk.Label):
    """Rounded-look status pill (colored text on tinted background)."""

    def __init__(self, master: tk.Misc, fonts: Fonts, kind: str = "info", text: str = "",
                 big: bool = False) -> None:
        super().__init__(master, text=text, font=fonts.button_big if big else fonts.body_b,
                         padx=14 if big else 10, pady=6 if big else 3, bd=0)
        self.set(kind, text)

    def set(self, kind: str, text: str) -> None:
        fg, bg = STATUS_COLORS.get(kind, STATUS_COLORS["info"])
        self.configure(text=("●  " + text) if text else "", fg=fg, bg=bg)
