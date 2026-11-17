"""User-side Filttr Agent GUI.

The user plugs their phone in, opens this program, and types the code
their Filttr admin gives them. The program shares the phone's USB
connection with the admin over an outbound relay — nothing on this PC
listens on the internet.

The user can disconnect at any time by closing the window.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from filttr_supervise import theme
from filttr_supervise.usbtunnel import (
    DEFAULT_RELAY_URL,
    ShareAgent,
    local_usb_service_up,
    pretty_code,
)

# Override in config.json with "relay_url" if using a custom relay server.
_RELAY_URL = DEFAULT_RELAY_URL


class FilttrAgentApp(tk.Tk):
    def __init__(self, relay_url: str = _RELAY_URL) -> None:
        super().__init__()
        self.relay_url = relay_url
        self.title("Filttr Agent")
        self.fonts = theme.apply_theme(self)
        theme.set_window_icon(self)
        self.resizable(False, False)

        self._agent: Optional[ShareAgent] = None
        self._running = False

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        f = self.fonts
        pad = {"padx": 28, "pady": 22}

        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)

        # header
        hdr = ttk.Frame(outer)
        hdr.pack(fill=tk.X, **pad)
        img = theme.load_image("filttr-mark-40.png")
        if img:
            self._img = img
            ttk.Label(hdr, image=img).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(hdr, text="Filttr Agent", style="Title.TLabel").pack(
            side=tk.LEFT, anchor=tk.W
        )

        theme.rule(outer).pack(fill=tk.X)

        body = ttk.Frame(outer)
        body.pack(fill=tk.X, padx=28, pady=18)

        # phone status
        self._lbl_phone = ttk.Label(
            body,
            text="🔍 Checking for iPhone…",
            style="Muted.TLabel",
        )
        self._lbl_phone.pack(anchor=tk.W)

        # code entry
        ttk.Label(
            body,
            text="Enter the code your admin gave you:",
            style="Small.TLabel",
        ).pack(anchor=tk.W, pady=(18, 4))

        entry_row = ttk.Frame(body)
        entry_row.pack(anchor=tk.W)
        self._var_code = tk.StringVar()
        self._entry_code = ttk.Entry(
            entry_row,
            textvariable=self._var_code,
            font=(self.fonts.mono[0], 16),
            width=16,
        )
        self._entry_code.pack(side=tk.LEFT)
        self._entry_code.bind("<Return>", lambda _: self._on_connect())

        self._btn_connect = ttk.Button(
            entry_row, text="Connect", style="Primary.TButton", command=self._on_connect
        )
        self._btn_connect.pack(side=tk.LEFT, padx=(10, 0))

        # status line (shown during / after connection)
        self._lbl_status = ttk.Label(body, text="", style="Muted.TLabel", wraplength=340)
        self._lbl_status.pack(anchor=tk.W, pady=(14, 0))

        # disconnect button (hidden until connected)
        self._btn_disconnect = ttk.Button(
            body, text="Disconnect", command=self._on_close
        )
        self._btn_disconnect.pack(anchor=tk.W, pady=(10, 0))
        self._btn_disconnect.pack_forget()

        theme.rule(outer).pack(fill=tk.X, pady=(8, 0))
        ttk.Label(
            outer, text="© AB Brachfeld", style="Small.TLabel"
        ).pack(anchor=tk.E, padx=28, pady=8)

        # check phone on startup
        self.after(400, self._check_phone)

    def _check_phone(self) -> None:
        import sys
        if local_usb_service_up():
            self._lbl_phone.configure(
                text="📱 Apple USB service is running. Plug in the iPhone if you haven't."
            )
        else:
            if sys.platform == "darwin":
                msg = "⚠ iPhone not detected. Plug in the iPhone with a USB cable."
            else:
                msg = (
                    "⚠ Apple USB service not found. Install the Apple Devices app "
                    "from the Microsoft Store and plug in the iPhone."
                )
            self._lbl_phone.configure(text=msg)

    def _on_connect(self) -> None:
        if self._running:
            return
        raw = self._var_code.get().strip()
        if not raw:
            self._lbl_status.configure(text="Enter the code first.")
            return

        self._running = True
        self._entry_code.configure(state=tk.DISABLED)
        self._btn_connect.configure(state=tk.DISABLED)
        self._lbl_status.configure(text="Connecting…")
        self._btn_disconnect.pack(anchor=tk.W, pady=(10, 0))

        self._agent = ShareAgent(
            raw,
            on_status=lambda t: self.after(0, lambda t=t: self._lbl_status.configure(text=t)),
            on_end=self._on_end,
            relay_url=self.relay_url,
        )
        self._agent.start()

    def _on_end(self, reason: str) -> None:
        self._running = False

        def _do() -> None:
            self._entry_code.configure(state=tk.NORMAL)
            self._btn_connect.configure(state=tk.NORMAL)
            self._btn_disconnect.pack_forget()
            if reason and reason not in ("The connection was closed.", ""):
                self._lbl_status.configure(
                    text=f"Disconnected: {reason}"
                )
            else:
                self._lbl_status.configure(text="Session ended.")
            self._var_code.set("")

        self.after(0, _do)

    def _on_close(self) -> None:
        if self._agent is not None:
            self._agent.stop()
            self._agent = None
        self.destroy()


def run_agent_gui(relay_url: str = _RELAY_URL) -> None:
    # Allow relay_url override from config.json if present
    try:
        from filttr_supervise.config import load_config
        cfg = load_config()
        if cfg.relay_url:
            relay_url = cfg.relay_url
    except Exception:
        pass

    theme.enable_dpi_awareness()
    app = FilttrAgentApp(relay_url=relay_url)
    app.mainloop()
