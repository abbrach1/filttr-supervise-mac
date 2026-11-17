"""Look and feel for the Tk GUI: a plain light Windows utility, Filttr ink as the accent."""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
from typing import Optional

BG = "#FFFFFF"
SURFACE = "#F5F4F1"
RULE = "#E3E1DB"
TEXT = "#1C1B17"
MUTED = "#6F6B62"
FAINT = "#A8A397"
INK = "#15140F"
INK_HOVER = "#3A382F"
SELECT = "#E8E4D6"
OK = "#1A7F37"
WARN = "#9A6700"
BAD = "#B42318"

STATUS_FG = {"ok": OK, "warn": WARN, "bad": BAD, "info": MUTED}


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
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
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
        body = _pick(root, ("Segoe UI", "DejaVu Sans", "TkDefaultFont"))
        semi = _pick(root, ("Segoe UI Semibold", body))
        mono = _pick(root, ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "TkFixedFont"))
        self.body = (body, 10)
        self.small = (body, 9)
        self.strong = (semi, 10)
        self.section = (semi, 11)
        self.title = (semi, 13)
        self.device = (semi, 15)
        self.heading = (semi, 17)
        self.mono = (mono, 9)


def apply_theme(root: tk.Tk) -> Fonts:
    f = Fonts(root)
    root.configure(bg=BG)
    root.option_add("*Font", f.body)
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", background=BG, foreground=TEXT, font=f.body, bordercolor=RULE,
                 lightcolor=BG, darkcolor=RULE, troughcolor=SURFACE, focuscolor=BG)
    st.configure("TFrame", background=BG)
    st.configure("TLabel", background=BG, foreground=TEXT)
    st.configure("Muted.TLabel", foreground=MUTED)
    st.configure("Small.TLabel", foreground=MUTED, font=f.small)
    st.configure("Strong.TLabel", font=f.strong)
    st.configure("Section.TLabel", font=f.section)
    st.configure("Title.TLabel", font=f.title)
    st.configure("Device.TLabel", font=f.device)
    st.configure("Heading.TLabel", font=f.heading)
    st.configure("Ok.TLabel", foreground=OK, font=f.strong)
    st.configure("Warn.TLabel", foreground=WARN, font=f.strong)
    st.configure("Bad.TLabel", foreground=BAD, font=f.strong)
    st.configure("Error.TLabel", foreground=BAD, font=f.small)

    st.configure("TButton", background=BG, foreground=TEXT, bordercolor="#CFCBC1",
                 lightcolor=BG, darkcolor=BG, padding=(12, 5), relief="solid", borderwidth=1)
    st.map("TButton",
           background=[("disabled", BG), ("pressed", SELECT), ("active", SURFACE)],
           foreground=[("disabled", FAINT)],
           bordercolor=[("disabled", RULE), ("focus", TEXT)])
    st.configure("Primary.TButton", background=INK, foreground="#FFFFFF", bordercolor=INK,
                 lightcolor=INK, darkcolor=INK, padding=(22, 7), font=f.strong)
    st.map("Primary.TButton",
           background=[("disabled", SURFACE), ("pressed", INK), ("active", INK_HOVER)],
           foreground=[("disabled", FAINT)],
           bordercolor=[("disabled", RULE), ("active", INK_HOVER)])
    st.configure("Link.TButton", background=BG, foreground=MUTED, bordercolor=BG,
                 lightcolor=BG, darkcolor=BG, padding=(2, 2), relief="flat", font=f.small)
    st.map("Link.TButton", foreground=[("active", TEXT), ("disabled", FAINT)],
           background=[("active", BG)], bordercolor=[("focus", BG)])

    st.configure("TEntry", fieldbackground=BG, foreground=TEXT, bordercolor="#CFCBC1",
                 lightcolor=BG, darkcolor=BG, padding=(6, 4), insertcolor=TEXT)
    st.map("TEntry", bordercolor=[("focus", TEXT)], lightcolor=[("focus", BG)],
           fieldbackground=[("disabled", SURFACE), ("readonly", SURFACE)])
    st.configure("TRadiobutton", background=BG, foreground=TEXT)
    st.map("TRadiobutton", background=[("active", BG)], indicatorcolor=[("selected", INK)])
    st.configure("TCheckbutton", background=BG, foreground=TEXT)
    st.map("TCheckbutton", background=[("active", BG)], indicatorcolor=[("selected", INK)])
    st.configure("TSeparator", background=RULE)
    st.configure("Vertical.TScrollbar", background=SURFACE, troughcolor=BG, bordercolor=BG,
                 lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=MUTED, gripcount=0)
    st.map("Vertical.TScrollbar", background=[("active", RULE)])

    st.configure("Treeview", background=BG, fieldbackground=BG, foreground=TEXT,
                 bordercolor=RULE, rowheight=26, font=f.body)
    st.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    st.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, font=f.small,
                 bordercolor=RULE, lightcolor=SURFACE, darkcolor=SURFACE, relief="flat",
                 padding=(6, 4))
    st.map("Treeview.Heading", background=[("active", SURFACE)])
    st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
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


def rule(master: tk.Misc) -> tk.Frame:
    """A one-pixel horizontal divider."""
    return tk.Frame(master, bg=RULE, height=1)
