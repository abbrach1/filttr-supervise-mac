"""Tkinter GUI for Filttr Supervise — unlock, user search, sparse supervise."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

from filttr_supervise import __version__
from filttr_supervise import theme
from filttr_supervise.auth import (
    DEFAULT_ADMIN_EMAIL,
    check_trusted_device,
    checkcode,
    effective_tool_token,
    send_email_code,
    untrust_device,
    verify_email_code,
)
from filttr_supervise.config import AppConfig, load_config
from filttr_supervise.device import DeviceInfo
from filttr_supervise.supervise import supervise_device
from filttr_supervise.watcher import DeviceWatcher
from filttr_supervise.users import PortalUser, UnauthorizedError, lookup_user_id, search_users

NO_SELECTION = "(none — search, look up, or paste access key)"


class UnlockDialog(tk.Toplevel):
    """Modal unlock: admin code OR email code (+ trust this PC)."""

    def __init__(self, master: tk.Tk, config: AppConfig) -> None:
        super().__init__(master)
        self.config_data = config
        self.result_ok = False
        self.title("Sign in — Filttr Supervise")
        self.configure(bg=theme.PAPER_CARD, padx=12, pady=12)
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        pad = {"padx": 10, "pady": 6}

        ttk.Label(
            self,
            text="Unlock to search portal users (session token ~8 hours).",
            wraplength=420, style="Card.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(self, text="Admin email:", style="Card.TLabel").grid(row=1, column=0, sticky="e", **pad)
        self.var_email = tk.StringVar(value=DEFAULT_ADMIN_EMAIL)
        ttk.Entry(self, textvariable=self.var_email, width=36).grid(
            row=1, column=1, columnspan=2, sticky="we", **pad
        )

        self.mode = tk.StringVar(value="admin")
        ttk.Radiobutton(
            self, text="Admin code", variable=self.mode, value="admin", command=self._toggle
        ).grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        ttk.Radiobutton(
            self,
            text="Email me a code",
            variable=self.mode,
            value="email",
            command=self._toggle,
        ).grid(row=3, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(self, text="Code:", style="Card.TLabel").grid(row=4, column=0, sticky="e", **pad)
        self.var_code = tk.StringVar()
        ttk.Entry(self, textvariable=self.var_code, width=24, show="*").grid(
            row=4, column=1, sticky="w", **pad
        )
        self.btn_send = ttk.Button(self, text="Send email code", command=self._send)
        self.btn_send.grid(row=4, column=2, **pad)

        self.var_trust = tk.BooleanVar(value=True)
        self.chk_trust = ttk.Checkbutton(
            self, text="Trust this PC (60 days)", variable=self.var_trust
        )
        self.chk_trust.grid(row=5, column=0, columnspan=3, sticky="w", **pad)

        self.lbl = ttk.Label(self, text="", wraplength=420, style="CardMuted.TLabel")
        self.lbl.grid(row=6, column=0, columnspan=3, sticky="w", **pad)

        btns = ttk.Frame(self, style="Card.TFrame")
        btns.grid(row=7, column=0, columnspan=3, **pad)
        ttk.Button(btns, text="Unlock", style="Primary.TButton",
                   command=self._unlock).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=4)

        self._toggle()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _toggle(self) -> None:
        email_mode = self.mode.get() == "email"
        state = tk.NORMAL if email_mode else tk.DISABLED
        self.btn_send.configure(state=state)
        self.chk_trust.configure(state=state)

    def _bg(self, fn, on_done) -> None:
        """Run blocking network call off the Tk thread."""
        self.lbl.configure(text="Working…")

        def work() -> None:
            try:
                res = fn()
            except Exception as exc:
                res = exc

            def done() -> None:
                try:
                    if self.winfo_exists():
                        on_done(res)
                except tk.TclError:
                    pass

            try:
                self.after(0, done)
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=work, daemon=True).start()

    def _send(self) -> None:
        email = self.var_email.get().strip()

        def done(res) -> None:
            if isinstance(res, Exception):
                res = (False, f"Send failed: {res}")
            ok, msg = res
            self.lbl.configure(text=msg)
            if not ok:
                messagebox.showerror("Send failed", msg, parent=self)

        self._bg(lambda: send_email_code(self.config_data, email), done)

    def _unlock(self) -> None:
        email = self.var_email.get().strip()
        code = self.var_code.get().strip()
        if not code:
            messagebox.showerror("Code required", "Enter the code.", parent=self)
            return
        admin = self.mode.get() == "admin"
        trust = self.var_trust.get()

        def call():
            if admin:
                return checkcode(self.config_data, email, code)
            return verify_email_code(self.config_data, email, code, trust=trust)

        def done(res) -> None:
            if isinstance(res, Exception):
                res = (False, f"Unlock failed: {res}", None)
            ok, msg, _tok = res
            self.lbl.configure(text=msg)
            if ok:
                self.result_ok = True
                self.destroy()
            else:
                messagebox.showerror("Unlock failed", msg, parent=self)

        self._bg(call, done)

    def _cancel(self) -> None:
        self.result_ok = False
        self.destroy()


class FilttrSuperviseApp(tk.Tk):
    def __init__(self, config: Optional[AppConfig] = None) -> None:
        super().__init__()
        self.config_data = config or load_config()
        self.title("Filttr Supervise")
        self.fonts = theme.apply_theme(self)
        theme.set_window_icon(self)
        self.geometry("1040x800")
        self.minsize(900, 700)
        self._busy = False
        self._device: Optional[DeviceInfo] = None
        self._selected: Optional[PortalUser] = None
        self._results: list[PortalUser] = []
        self._build()
        # Sign-in is optional for now: never block on unlock at launch.
        self.after(100, self._startup_check_trust)
        self._watcher = DeviceWatcher(self._on_watcher_info)
        self.after(300, self._watcher.start)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_supervise_enabled()

    def _on_close(self) -> None:
        try:
            self._watcher.stop()
        finally:
            self.destroy()

    def _on_watcher_info(self, info: DeviceInfo) -> None:
        """Called on the watcher thread — marshal to Tk via after()."""
        try:
            self.after(0, lambda: None if self._busy else self._apply_device(info))
        except (tk.TclError, RuntimeError):
            pass

    def _build(self) -> None:
        f = self.fonts
        # ---- Header bar (ink) with logo, app name and sign-in pill ----
        header = tk.Frame(self, bg=theme.INK)
        header.pack(fill=tk.X)
        tk.Frame(self, bg=theme.SIGNAL, height=3).pack(fill=tk.X)
        hin = tk.Frame(header, bg=theme.INK)
        hin.pack(fill=tk.X, padx=20, pady=12)
        self._img_mark = theme.load_image("filttr-mark-40.png")
        self._img_word = theme.load_image("filttr-wordmark-30.png")
        if self._img_mark:
            tk.Label(hin, image=self._img_mark, bg=theme.INK, bd=0).pack(side=tk.LEFT)
        if self._img_word:
            tk.Label(hin, image=self._img_word, bg=theme.INK, bd=0).pack(side=tk.LEFT, padx=(10, 0))
        titles = tk.Frame(hin, bg=theme.INK)
        titles.pack(side=tk.LEFT, padx=(14, 0))
        ttk.Label(titles, text="Supervise", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(
            titles, text=f"v{__version__} · sparse restore · never erases the phone",
            style="HeaderMuted.TLabel",
        ).pack(anchor=tk.W)
        ttk.Button(hin, text="Sign out", style="Header.TButton",
                   command=self.on_sign_out).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(hin, text="Sign in…", style="Header.TButton",
                   command=self.open_unlock).pack(side=tk.RIGHT, padx=(6, 0))
        self.lbl_lock = theme.Pill(hin, f, "info", "Not signed in (optional)")
        self.lbl_lock.pack(side=tk.RIGHT, padx=(0, 10))

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)
        body.columnconfigure(0, weight=5, uniform="col")
        body.columnconfigure(1, weight=6, uniform="col")
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # ---- Device card ----
        dev = theme.Card(left, "Device", f)
        dev.pack(fill=tk.X)
        self.btn_refresh = ttk.Button(dev.head_right, text="↻  Refresh", command=self.refresh_device)
        self.btn_refresh.pack(side=tk.RIGHT)
        self.badge_device = theme.Pill(dev.body, f, "warn", "Looking for USB iPhone…", big=True)
        self.badge_device.pack(anchor=tk.W, pady=(2, 10))
        self.lbl_device_name = ttk.Label(dev.body, text="", style="CardBig.TLabel")
        self.lbl_device_name.pack(anchor=tk.W)
        self.lbl_device = ttk.Label(dev.body, text="Connect an iPhone via USB and unlock it.",
                                    style="CardMuted.TLabel", justify=tk.LEFT, wraplength=360)
        self.lbl_device.pack(anchor=tk.W, pady=(2, 0))

        # ---- Prechecks card ----
        pre = theme.Card(left, "Prechecks", f)
        pre.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        self.pre_rows = tk.Frame(pre.body, bg=theme.PAPER_CARD)
        self.pre_rows.pack(fill=tk.X)
        self.lbl_pre = ttk.Label(
            pre.body,
            text="Prep: Find My OFF, phone unlocked, tap Trust this PC.\n"
            "This tool never factory-resets the phone.",
            style="CardMuted.TLabel", justify=tk.LEFT, wraplength=360,
        )
        self.lbl_pre.pack(anchor=tk.W, pady=(10, 0))
        self._render_prechecks([])

        # ---- User picker card ----
        userf = theme.Card(right, "Portal user", f, subtitle="Applied to the phone's enrollment URL")
        userf.pack(fill=tk.BOTH, expand=True)
        ub = userf.body
        row = tk.Frame(ub, bg=theme.PAPER_CARD)
        row.pack(fill=tk.X, pady=(4, 6))
        self.var_query = tk.StringVar()
        self.entry_query = ttk.Entry(row, textvariable=self.var_query)
        self.entry_query.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_query.bind("<Return>", lambda _e: self.on_search())
        self.btn_search = ttk.Button(row, text="Search", command=self.on_search)
        self.btn_search.pack(side=tk.LEFT, padx=(8, 0))

        rowu = tk.Frame(ub, bg=theme.PAPER_CARD)
        rowu.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(rowu, text="or User ID", style="CardMuted.TLabel").pack(side=tk.LEFT)
        self.var_userid = tk.StringVar()
        ttk.Entry(rowu, textvariable=self.var_userid, width=14).pack(side=tk.LEFT, padx=8)
        ttk.Button(rowu, text="Look up", command=self.on_lookup).pack(side=tk.LEFT)

        lstf = tk.Frame(ub, bg=theme.PAPER_HAIR, bd=0)
        lstf.pack(fill=tk.BOTH, expand=True)
        self.lst_users = tk.Listbox(
            lstf, height=6, exportselection=False, activestyle="none", bd=0,
            highlightthickness=0, relief=tk.FLAT, bg="#FFFFFF", fg=theme.INK,
            selectbackground=theme.SIGNAL, selectforeground=theme.INK,
            font=f.body, disabledforeground=theme.STONE,
        )
        sb = ttk.Scrollbar(lstf, orient=tk.VERTICAL, command=self.lst_users.yview)
        self.lst_users.configure(yscrollcommand=sb.set)
        self.lst_users.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        sb.pack(side=tk.RIGHT, fill=tk.Y, pady=1, padx=(0, 1))
        self.lst_users.bind("<<ListboxSelect>>", self.on_pick_user)

        self.lbl_search_status = ttk.Label(ub, text="Type a name and press Enter.",
                                           style="CardMuted.TLabel", justify=tk.LEFT,
                                           wraplength=420)
        self.lbl_search_status.pack(anchor=tk.W, pady=(4, 8))

        sel = tk.Frame(ub, bg=theme.INK)
        sel.pack(fill=tk.X)
        tk.Label(sel, text="SELECTED USER", bg=theme.INK, fg=theme.SIGNAL,
                 font=f.small).pack(anchor=tk.W, padx=14, pady=(10, 0))
        self.lbl_selected = tk.Label(
            sel, text=NO_SELECTION, bg=theme.INK, fg=theme.PAPER, font=f.body_b,
            justify=tk.LEFT, anchor=tk.W, wraplength=420,
        )
        self.lbl_selected.pack(fill=tk.X, padx=14, pady=(2, 10))

        row2 = tk.Frame(ub, bg=theme.PAPER_CARD)
        row2.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(row2, text="Access key", style="CardMuted.TLabel").pack(side=tk.LEFT)
        self.var_access_key = tk.StringVar(value=self.config_data.access_key or "")
        self.entry_access_key = ttk.Entry(row2, textvariable=self.var_access_key)
        self.entry_access_key.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)
        self.var_access_key.trace_add("write", lambda *_: self._on_access_key_typed())

        self.lbl_cfg = ttk.Label(ub, text="", style="CardMuted.TLabel", justify=tk.LEFT,
                                 wraplength=420)
        self.lbl_cfg.pack(anchor=tk.W, pady=(6, 0))
        self._refresh_cfg_label()

        # ---- Action bar ----
        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=20, pady=(0, 10))
        self.btn_supervise = ttk.Button(btns, text="Supervise iPhone", style="Primary.TButton",
                                        command=self.on_supervise)
        self.btn_supervise.pack(side=tk.LEFT)
        self.btn_remove = ttk.Button(btns, text="Remove supervision", style="Secondary.TButton",
                                     command=self.on_remove)
        self.btn_remove.pack(side=tk.LEFT, padx=10)
        ttk.Label(btns, text="Sparse MobileBackup2 restore of CloudConfigurationDetails only — "
                  "user data is not erased.", style="Muted.TLabel",
                  wraplength=360, justify=tk.LEFT).pack(side=tk.LEFT, padx=10)

        # ---- Collapsible log ----
        logbar = ttk.Frame(self)
        logbar.pack(fill=tk.X, padx=20)
        self.btn_log = ttk.Button(logbar, text="▾  Log", style="Link.TButton",
                                  command=self._toggle_log)
        self.btn_log.pack(side=tk.LEFT)
        self.logf = tk.Frame(self, bg=theme.INK)
        self.logf.pack(fill=tk.X, padx=20, pady=(2, 16))
        self.log = scrolledtext.ScrolledText(
            self.logf, height=6, state=tk.DISABLED, wrap=tk.WORD, bd=0, relief=tk.FLAT,
            bg=theme.INK, fg=theme.PAPER, insertbackground=theme.SIGNAL, font=f.mono,
            highlightthickness=0, padx=10, pady=8,
        )
        self.log.pack(fill=tk.BOTH, expand=True)
        self._log_open = True

    def _toggle_log(self) -> None:
        self._log_open = not self._log_open
        if self._log_open:
            self.logf.pack(fill=tk.X, padx=20, pady=(2, 16))
            self.btn_log.configure(text="▾  Log")
        else:
            self.logf.pack_forget()
            self.btn_log.configure(text="▸  Log (hidden)")

    def _render_prechecks(self, checks: list[tuple[str, str, str]]) -> None:
        """checks: (label, value text, kind in ok/warn/bad/info)."""
        for w in self.pre_rows.winfo_children():
            w.destroy()
        if not checks:
            tk.Label(self.pre_rows, text="Waiting for a device…", bg=theme.PAPER_CARD,
                     fg=theme.MUTED, font=self.fonts.body).pack(anchor=tk.W)
            return
        icons = {"ok": "✔", "bad": "✘", "warn": "!", "info": "•"}
        for i, (label, val, kind) in enumerate(checks):
            fg, bg = theme.STATUS_COLORS[kind]
            r = tk.Frame(self.pre_rows, bg=theme.PAPER_CARD)
            r.pack(fill=tk.X, pady=3)
            tk.Label(r, text=icons[kind], width=2, bg=bg, fg=fg,
                     font=self.fonts.icon).pack(side=tk.LEFT)
            tk.Label(r, text=label, bg=theme.PAPER_CARD, fg=theme.INK,
                     font=self.fonts.body).pack(side=tk.LEFT, padx=10)
            tk.Label(r, text=val, bg=theme.PAPER_CARD, fg=fg,
                     font=self.fonts.body_b).pack(side=tk.RIGHT)

    def _startup_check_trust(self) -> None:
        """Silently reuse a trusted-PC session if one exists; never prompt."""
        def work() -> None:
            try:
                ok, msg = check_trusted_device(self.config_data)
            except Exception:
                ok, msg = False, ""

            def done() -> None:
                if ok and msg:
                    self.append_log(msg)
                self._refresh_lock_label()

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()
        self.append_log(
            "Sign-in not required. Search users or paste an access key, then Supervise."
        )

    def _refresh_lock_label(self) -> None:
        if effective_tool_token(self.config_data):
            self.lbl_lock.set("ok", "Signed in")
        else:
            self.lbl_lock.set("info", "Not signed in (optional)")

    def open_unlock(self) -> None:
        dlg = UnlockDialog(self, self.config_data)
        self.wait_window(dlg)
        self._refresh_lock_label()
        if dlg.result_ok:
            self.append_log("Tool unlocked.")
        self._refresh_cfg_label()

    def on_sign_out(self) -> None:
        def work() -> None:
            try:
                _ok, msg = untrust_device(self.config_data)
            except Exception as exc:
                msg = f"Sign out failed: {exc}"

            def done() -> None:
                self.append_log(msg)
                self._refresh_lock_label()
                messagebox.showinfo("Signed out", msg)

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _sync_access_key(self) -> None:
        self.config_data.access_key = self.var_access_key.get().strip()

    def _refresh_cfg_label(self) -> None:
        self._sync_access_key()
        preview = self.config_data.configuration_url_for()
        tok = "yes" if effective_tool_token(self.config_data) else "no"
        self.lbl_cfg.configure(
            text=(
                f"Signed in: {tok} (optional)\n"
                f"ConfigurationURL: {preview or '(select a user or paste access key)'}"
            )
        )

    def _update_supervise_enabled(self) -> None:
        self._sync_access_key()
        ok = bool(self.config_data.access_key) and not self._busy
        self.btn_supervise.configure(state=tk.NORMAL if ok else tk.DISABLED)

    def _on_access_key_typed(self) -> None:
        key = self.var_access_key.get().strip()
        if self._selected and self._selected.access_key != key:
            self._selected = None
            self.lbl_selected.configure(text=f"Manual access key  k={key[:12]}…")
        elif not self._selected and key:
            self.lbl_selected.configure(text=f"Manual access key  k={key}")
        elif not key:
            self.lbl_selected.configure(text=NO_SELECTION)
        self._refresh_cfg_label()
        self._update_supervise_enabled()

    def append_log(self, line: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _handle_auth_msg(self, msg: str) -> bool:
        if "unauthorized" in msg.lower() or "unlock the tool" in msg.lower():
            self.lbl_search_status.configure(
                text="Search needs sign-in on the server. Paste the user's access key "
                "below to supervise, or use Sign in (optional)."
            )
            self.entry_access_key.focus_set()
            return True
        return False

    def on_search(self) -> None:
        if self._busy:
            return
        q = self.var_query.get().strip()
        self.lbl_search_status.configure(text="Searching…")
        self.lst_users.delete(0, tk.END)
        self._results = []

        def work() -> None:
            try:
                users, msg = search_users(self.config_data, q)
            except Exception as exc:
                users, msg = [], f"Search failed: {exc}"

            def done() -> None:
                self._results = users
                self.lst_users.delete(0, tk.END)
                for u in users:
                    self.lst_users.insert(tk.END, u.label())
                self.lbl_search_status.configure(text=msg)
                self.append_log(f"Search {q!r}: {msg}")
                self._handle_auth_msg(msg)

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def on_lookup(self) -> None:
        if self._busy:
            return
        uid = self.var_userid.get().strip()

        def work() -> None:
            try:
                user, msg = lookup_user_id(self.config_data, uid)
            except Exception as exc:
                user, msg = None, f"Lookup failed: {exc}"

            def done() -> None:
                self.lbl_search_status.configure(text=msg)
                self.append_log(f"Lookup {uid!r}: {msg}")
                if self._handle_auth_msg(msg):
                    return
                if user:
                    self._apply_selected(user)

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _apply_selected(self, user: PortalUser) -> None:
        self._selected = user
        self.config_data.access_key = user.access_key
        self.var_access_key.set(user.access_key)
        meta = []
        if user.email:
            meta.append(user.email)
        if user.user_id:
            meta.append(f"userId {user.user_id}")
        meta.append(f"k={user.access_key}")
        self.lbl_selected.configure(
            text=(user.name or "(no name)") + "\n" + "  ·  ".join(meta)
        )
        self._refresh_cfg_label()
        self._update_supervise_enabled()
        self.append_log(f"Selected user {user.label()} → accessKey set")

    def on_pick_user(self, _event=None) -> None:
        sel = self.lst_users.curselection()
        if not sel:
            return
        self._apply_selected(self._results[sel[0]])

    def refresh_device(self) -> None:
        if self._busy:
            return

        self.badge_device.set("warn", "Reading device…")
        self._watcher.refresh_now()

    def _apply_device(self, info: DeviceInfo) -> None:
        self._device = info
        if not info.connected:
            self.badge_device.set("bad", "No iPhone connected")
            self.lbl_device_name.configure(text="")
            self.lbl_device.configure(
                text=(info.error or "No USB iPhone connected")
                + "\nConnect an iPhone with Apple Devices / iTunes drivers installed."
            )
            self._render_prechecks([])
            return
        if info.paired:
            self.badge_device.set("ok", "Connected & trusted")
        else:
            self.badge_device.set("warn", "Connected — tap Trust on the iPhone")
        self.lbl_device_name.configure(text=info.name or "(unnamed)")
        self.lbl_device.configure(
            text=(
                f"{info.product_type}  ·  iOS {info.product_version} ({info.build_version})\n"
                f"Serial {info.serial}\n"
                f"UDID {info.udid}"
            )
        )

        def yn(val, good_when):
            if val is None:
                return "unknown", "info"
            return ("Yes" if val else "No"), ("ok" if val == good_when else "bad")

        act = info.activation_state or "unknown"
        sup = info.supervised
        checks = [
            ("Paired / Trusted", *yn(info.paired, True)),
            ("Activation", act, "ok" if act.lower() == "activated" else "warn"),
            ("Supervised (current)", "unknown" if sup is None else ("Yes" if sup else "No"), "info"),
            ("Find My", *yn(info.find_my_enabled, False)),
            ("Encrypted backup", "unknown" if info.will_encrypt_backup is None
             else ("Yes" if info.will_encrypt_backup else "No"),
             "warn" if info.will_encrypt_backup else "info"),
        ]
        self._render_prechecks(checks)
        lines = [
            "Prep: Find My OFF, phone unlocked, tap Trust this PC.\n"
            "This tool never factory-resets the phone."
        ]
        if info.notes:
            lines.extend(f"• {n}" for n in info.notes)
        self.lbl_pre.configure(text="\n".join(lines))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for w in (
            self.btn_refresh,
            self.btn_remove,
            self.btn_search,
            self.entry_query,
            self.entry_access_key,
            self.lst_users,
        ):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self._update_supervise_enabled()

    def on_supervise(self) -> None:
        if self._busy:
            return
        self._sync_access_key()
        err = self.config_data.require_access_key()
        if err:
            messagebox.showerror("User / access key required", err)
            return
        url = self.config_data.configuration_url_for()
        who = "Selected: " + self.lbl_selected.cget("text")
        if not messagebox.askyesno(
            "Supervise (no erase)",
            f"{who}\n\n"
            "Inject CloudConfigurationDetails via sparse MobileBackup2 restore.\n"
            "The phone will reboot. User data is NOT erased.\n\n"
            f"ConfigurationURL:\n{url}\n\n"
            "Find My must be off. Continue?",
        ):
            return
        self._run_flow(remove=False)

    def on_remove(self) -> None:
        if self._busy:
            return
        if not messagebox.askyesno(
            "Remove supervision",
            "Write IsSupervised=false via the same sparse restore (no erase).\n"
            "Best-effort / iOS-version dependent. Continue?",
        ):
            return
        self._run_flow(remove=True)

    def _run_flow(self, *, remove: bool) -> None:
        self._sync_access_key()
        selected = None if remove else self._selected
        self._set_busy(True)
        self._watcher.pause()
        self.append_log("—" * 40)
        self.append_log("Starting removal…" if remove else "Starting sparse supervise…")
        if not remove:
            self.append_log(f"ConfigurationURL: {self.config_data.configuration_url_for()}")

        def work() -> None:
            def progress(line: str) -> None:
                self.after(0, lambda l=line: self.append_log(l))

            try:
                result = supervise_device(
                    self.config_data,
                    remove=remove,
                    progress=progress,
                    selected_user=selected,
                )
            except Exception as exc:  # never leave the GUI stuck busy
                from filttr_supervise.supervise import SuperviseResult

                result = SuperviseResult(ok=False, message=f"Unexpected error: {exc}")
                progress(result.message)

            def done() -> None:
                self._set_busy(False)
                self._watcher.resume()
                if result.ok:
                    extra = ""
                    if result.enrollment_url:
                        extra = f"\n\nEnrollment URL:\n{result.enrollment_url}"
                    messagebox.showinfo(
                        "Filttr Supervise", (result.message or "Done") + extra
                    )
                else:
                    messagebox.showerror(
                        "Filttr Supervise", result.message or "Failed — see log"
                    )
                self.refresh_device()

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()


def run_gui(config: Optional[AppConfig] = None) -> None:
    theme.enable_dpi_awareness()
    app = FilttrSuperviseApp(config=config)
    app.mainloop()
