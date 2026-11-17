"""Tkinter GUI for Filttr Supervise.

Sign-in is required: the main window is only built after the server accepts an
admin code, an emailed code, or a saved trusted-PC record. Signing out tears the
main view down again.
"""
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
    load_device_record,
    send_email_code,
    untrust_device,
    verify_email_code,
)
from filttr_supervise.config import AppConfig, load_config
from filttr_supervise.device import DeviceInfo
from filttr_supervise.supervise import supervise_device
from filttr_supervise.users import PortalUser, lookup_user_id, search_users
from filttr_supervise.watcher import DeviceWatcher

NO_SELECTION = "No user selected"


class FilttrSuperviseApp(tk.Tk):
    def __init__(self, config: Optional[AppConfig] = None) -> None:
        super().__init__()
        self.config_data = config or load_config()
        self.title("Filttr Supervise")
        self.fonts = theme.apply_theme(self)
        theme.set_window_icon(self)
        self.geometry("980x720")
        self.minsize(900, 660)

        self._busy = False
        self._signed_in = False
        self._email = ""
        self._device: Optional[DeviceInfo] = None
        self._selected: Optional[PortalUser] = None
        self._results: list[PortalUser] = []
        self._watcher: Optional[DeviceWatcher] = None
        self._log_lines: list[str] = []
        self._log_open = False
        self.log = None

        self._img_mark = theme.load_image("filttr-mark-40.png")

        self.container = ttk.Frame(self)
        self.container.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_signin()
        self.after(150, self._try_trusted_signin)

    # ---------------------------------------------------------------- shared

    def _clear_container(self) -> None:
        for w in self.container.winfo_children():
            w.destroy()

    def _bg(self, fn, on_done) -> None:
        """Run a blocking call off the Tk thread, then hand the result back."""
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

    def append_log(self, line: str) -> None:
        self._log_lines.append(line)
        del self._log_lines[:-500]
        if self.log is None:
            return
        try:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, line + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        try:
            if self._watcher is not None:
                self._watcher.stop()
        finally:
            self.destroy()

    # -------------------------------------------------------------- sign-in

    def _build_signin(self) -> None:
        self._signed_in = False
        self.log = None
        self._clear_container()

        ttk.Label(self.container, text="בס״ד",
                  style="Small.TLabel").place(relx=1.0, x=-18, y=12, anchor="ne")
        outer = ttk.Frame(self.container)
        outer.place(relx=0.5, rely=0.5, anchor="center")

        if self._img_mark:
            ttk.Label(outer, image=self._img_mark).pack(pady=(0, 14))
        ttk.Label(outer, text="Filttr Supervise", style="Heading.TLabel").pack()
        ttk.Label(outer, text="Sign in to use this tool.",
                  style="Muted.TLabel").pack(pady=(4, 20))

        form = ttk.Frame(outer)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Admin email").grid(row=0, column=0, sticky="w", pady=(0, 10))
        rec = load_device_record()
        self.var_email = tk.StringVar(value=(rec.email if rec else DEFAULT_ADMIN_EMAIL))
        ttk.Entry(form, textvariable=self.var_email, width=34).grid(
            row=0, column=1, columnspan=2, sticky="we", padx=(14, 0), pady=(0, 10))

        self.mode = tk.StringVar(value="admin")
        modes = ttk.Frame(form)
        modes.grid(row=1, column=1, columnspan=2, sticky="w", padx=(14, 0), pady=(0, 10))
        ttk.Radiobutton(modes, text="Admin code", variable=self.mode, value="admin",
                        command=self._on_mode).pack(side=tk.LEFT)
        ttk.Radiobutton(modes, text="Email me a code", variable=self.mode, value="email",
                        command=self._on_mode).pack(side=tk.LEFT, padx=(18, 0))

        ttk.Label(form, text="Code").grid(row=2, column=0, sticky="w")
        self.var_code = tk.StringVar()
        self.entry_code = ttk.Entry(form, textvariable=self.var_code, width=18, show="•")
        self.entry_code.grid(row=2, column=1, sticky="w", padx=(14, 0))
        self.entry_code.bind("<Return>", lambda _e: self._do_signin())
        self.btn_send = ttk.Button(form, text="Send code", command=self._send_code)
        self.btn_send.grid(row=2, column=2, sticky="e")

        self.chk_trust_var = tk.BooleanVar(value=True)
        self.chk_trust = ttk.Checkbutton(form, text="Trust this PC for 60 days",
                                         variable=self.chk_trust_var)
        self.chk_trust.grid(row=3, column=1, columnspan=2, sticky="w",
                            padx=(14, 0), pady=(12, 0))

        self.lbl_signin_msg = ttk.Label(outer, text="", style="Small.TLabel",
                                        wraplength=380, justify=tk.LEFT)
        self.lbl_signin_msg.pack(anchor=tk.W, pady=(14, 0))

        row = ttk.Frame(outer)
        row.pack(fill=tk.X, pady=(18, 0))
        self.btn_signin = ttk.Button(row, text="Sign in", style="Primary.TButton",
                                     command=self._do_signin)
        self.btn_signin.pack(side=tk.LEFT)
        ttk.Button(row, text="Quit", command=self._on_close).pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(outer, text=f"Version {__version__}",
                  style="Small.TLabel").pack(anchor=tk.W, pady=(22, 0))
        ttk.Label(outer, text="© AB Brachfeld",
                  style="Small.TLabel").pack(anchor=tk.W, pady=(2, 0))

        self._on_mode()
        self.entry_code.focus_set()

    def _on_mode(self) -> None:
        # "Trust this PC" only applies to an emailed code, so it is hidden for the
        # admin code rather than shown greyed out.
        email_mode = self.mode.get() == "email"
        try:
            self.btn_send.configure(state=tk.NORMAL if email_mode else tk.DISABLED)
            if email_mode:
                self.chk_trust.grid()
            else:
                self.chk_trust.grid_remove()
        except tk.TclError:
            pass

    def _signin_busy(self, busy: bool, msg: str = "") -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        for w in (self.btn_signin, self.btn_send, self.entry_code):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        if not busy:
            self._on_mode()
        if msg:
            self.lbl_signin_msg.configure(text=msg, style="Small.TLabel")

    def _try_trusted_signin(self) -> None:
        """Reuse a saved trusted-PC record if the server still accepts it."""
        if not load_device_record():
            return
        self._signin_busy(True, "Checking this PC…")

        def done(res) -> None:
            if isinstance(res, Exception):
                res = (False, f"Trusted-PC check failed: {res}")
            ok, msg = res
            if ok:
                self._enter_main(self.var_email.get().strip(), msg)
            else:
                self._signin_busy(False, msg or "Enter a code to sign in.")

        self._bg(lambda: check_trusted_device(self.config_data), done)

    def _send_code(self) -> None:
        email = self.var_email.get().strip()
        if not email:
            self.lbl_signin_msg.configure(text="Enter the admin email.", style="Error.TLabel")
            return
        self._signin_busy(True, "Sending…")

        def done(res) -> None:
            if isinstance(res, Exception):
                res = (False, f"Send failed: {res}")
            ok, msg = res
            self._signin_busy(False)
            self.lbl_signin_msg.configure(text=msg,
                                          style="Small.TLabel" if ok else "Error.TLabel")

        self._bg(lambda: send_email_code(self.config_data, email), done)

    def _do_signin(self) -> None:
        email = self.var_email.get().strip()
        code = self.var_code.get().strip()
        if not code:
            self.lbl_signin_msg.configure(text="Enter the code.", style="Error.TLabel")
            self.entry_code.focus_set()
            return
        admin = self.mode.get() == "admin"
        trust = self.chk_trust_var.get()
        self._signin_busy(True, "Signing in…")

        def call():
            if admin:
                return checkcode(self.config_data, email, code)
            return verify_email_code(self.config_data, email, code, trust=trust)

        def done(res) -> None:
            if isinstance(res, Exception):
                res = (False, f"Sign-in failed: {res}", "")
            ok, msg, _tok = res
            if ok:
                self._enter_main(email, msg)
            else:
                self._signin_busy(False)
                self.var_code.set("")
                self.lbl_signin_msg.configure(text=msg, style="Error.TLabel")
                self.entry_code.focus_set()

        self._bg(call, done)

    # ----------------------------------------------------------- main window

    def _enter_main(self, email: str, msg: str = "") -> None:
        self._signed_in = True
        self._email = email
        self._clear_container()
        self._build_main()
        if msg:
            self.append_log(msg)
        self._watcher = DeviceWatcher(self._on_watcher_info)
        self.after(200, self._watcher.start)
        self._refresh_url_label()
        self._update_supervise_enabled()

    def _build_main(self) -> None:
        f = self.fonts

        header = ttk.Frame(self.container)
        header.pack(fill=tk.X, padx=22, pady=(16, 12))
        if self._img_mark:
            ttk.Label(header, image=self._img_mark).pack(side=tk.LEFT)
        titles = ttk.Frame(header)
        titles.pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(titles, text="Supervise", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(titles, text="Writes supervision settings over USB. Never erases the phone.",
                  style="Small.TLabel").pack(anchor=tk.W)
        ttk.Label(header, text="בס״ד",
                  style="Small.TLabel").pack(side=tk.RIGHT, padx=(14, 0))
        ttk.Button(header, text="Sign out", command=self.on_sign_out).pack(side=tk.RIGHT)
        ttk.Label(header, text=self._email or "Signed in",
                  style="Small.TLabel").pack(side=tk.RIGHT, padx=(0, 12))
        theme.rule(self.container).pack(fill=tk.X)

        body = ttk.Frame(self.container)
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=18)
        body.columnconfigure(0, weight=4, uniform="col")
        body.columnconfigure(1, weight=5, uniform="col")
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")

        # --- iPhone -------------------------------------------------------
        head = ttk.Frame(left, height=30)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        ttk.Label(head, text="iPhone", style="Section.TLabel").pack(side=tk.LEFT)
        self.btn_refresh = ttk.Button(head, text="Refresh", command=self.refresh_device)
        self.btn_refresh.pack(side=tk.RIGHT)
        theme.rule(left).pack(fill=tk.X, pady=(8, 12))

        self.lbl_device_status = ttk.Label(left, text="Looking for an iPhone…",
                                           style="Warn.TLabel")
        self.lbl_device_status.pack(anchor=tk.W)
        self.lbl_device_name = ttk.Label(left, text="", style="Device.TLabel")
        self.lbl_device_name.pack(anchor=tk.W, pady=(6, 0))
        self.lbl_device_meta = ttk.Label(left, text="Connect an iPhone over USB and unlock it.",
                                         style="Small.TLabel", justify=tk.LEFT, wraplength=330)
        self.lbl_device_meta.pack(anchor=tk.W, pady=(2, 0))

        self.checks = ttk.Frame(left)
        self.checks.pack(fill=tk.X, pady=(16, 0))
        self.checks.columnconfigure(1, weight=1)
        self._render_checks([])

        self.lbl_prep = ttk.Label(
            left,
            text="Before you start: turn Find My off, unlock the phone, and tap Trust.",
            style="Small.TLabel", justify=tk.LEFT, wraplength=330)
        self.lbl_prep.pack(anchor=tk.W, pady=(16, 0))

        # --- Portal user --------------------------------------------------
        rhead = ttk.Frame(right, height=30)
        rhead.pack(fill=tk.X)
        rhead.pack_propagate(False)
        ttk.Label(rhead, text="Portal user", style="Section.TLabel").pack(side=tk.LEFT)
        theme.rule(right).pack(fill=tk.X, pady=(8, 12))
        ttk.Label(right, text="Pick who this phone belongs to. Its enrollment link is built "
                             "from their access key.",
                  style="Small.TLabel", justify=tk.LEFT, wraplength=420).pack(anchor=tk.W)

        srow = ttk.Frame(right)
        srow.pack(fill=tk.X, pady=(12, 0))
        self.var_query = tk.StringVar()
        self.entry_query = ttk.Entry(srow, textvariable=self.var_query)
        self.entry_query.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_query.bind("<Return>", lambda _e: self.on_search())
        self.btn_search = ttk.Button(srow, text="Search", command=self.on_search)
        self.btn_search.pack(side=tk.LEFT, padx=(8, 0))

        lrow = ttk.Frame(right)
        lrow.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(lrow, text="or user ID", style="Small.TLabel").pack(side=tk.LEFT)
        self.var_userid = tk.StringVar()
        e_uid = ttk.Entry(lrow, textvariable=self.var_userid, width=14)
        e_uid.pack(side=tk.LEFT, padx=(8, 8))
        e_uid.bind("<Return>", lambda _e: self.on_lookup())
        self.btn_lookup = ttk.Button(lrow, text="Look up", command=self.on_lookup)
        self.btn_lookup.pack(side=tk.LEFT)

        tw = tk.Frame(right, bg=theme.RULE)
        tw.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.tree_users = ttk.Treeview(tw, columns=("name", "email", "uid"),
                                       show="headings", height=7, selectmode="browse")
        self.tree_users.heading("name", text="NAME")
        self.tree_users.heading("email", text="EMAIL")
        self.tree_users.heading("uid", text="USER ID")
        self.tree_users.column("name", width=140, anchor="w")
        self.tree_users.column("email", width=190, anchor="w")
        self.tree_users.column("uid", width=80, anchor="w")
        sb = ttk.Scrollbar(tw, orient=tk.VERTICAL, command=self.tree_users.yview)
        self.tree_users.configure(yscrollcommand=sb.set)
        self.tree_users.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        sb.pack(side=tk.RIGHT, fill=tk.Y, pady=1, padx=(0, 1))
        self.tree_users.bind("<<TreeviewSelect>>", self.on_pick_user)

        self.lbl_search_status = ttk.Label(right, text="Type a name and press Enter.",
                                           style="Small.TLabel", justify=tk.LEFT,
                                           wraplength=420)
        self.lbl_search_status.pack(anchor=tk.W, pady=(8, 0))

        krow = ttk.Frame(right)
        krow.pack(fill=tk.X, pady=(14, 0))
        ttk.Label(krow, text="Access key").pack(side=tk.LEFT)
        self.var_access_key = tk.StringVar(value=self.config_data.access_key or "")
        self.entry_access_key = ttk.Entry(krow, textvariable=self.var_access_key)
        self.entry_access_key.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        self.var_access_key.trace_add("write", lambda *_: self._on_access_key_typed())

        self.lbl_selected = ttk.Label(right, text=NO_SELECTION, style="Strong.TLabel",
                                      justify=tk.LEFT, wraplength=420)
        self.lbl_selected.pack(anchor=tk.W, pady=(12, 0))
        self.lbl_url = ttk.Label(right, text="", style="Small.TLabel", justify=tk.LEFT,
                                 wraplength=420)
        self.lbl_url.pack(anchor=tk.W, pady=(2, 0))

        # --- actions ------------------------------------------------------
        theme.rule(self.container).pack(fill=tk.X)
        bar = ttk.Frame(self.container)
        bar.pack(fill=tk.X, padx=22, pady=14)
        self.btn_supervise = ttk.Button(bar, text="Supervise iPhone", style="Primary.TButton",
                                        command=self.on_supervise)
        self.btn_supervise.pack(side=tk.LEFT)
        self.btn_remove = ttk.Button(bar, text="Remove supervision", command=self.on_remove)
        self.btn_remove.pack(side=tk.LEFT, padx=(10, 0))
        self.btn_remote = ttk.Button(bar, text="Remote iPhone…", command=self.on_remote_iphone)
        self.btn_remote.pack(side=tk.LEFT, padx=(10, 0))
        self.btn_log = ttk.Button(bar, text="Show log", style="Link.TButton",
                                  command=self._toggle_log)
        self.btn_log.pack(side=tk.RIGHT)
        ttk.Label(bar, text="© AB Brachfeld",
                  style="Small.TLabel").pack(side=tk.RIGHT, padx=(0, 18))

        self.logf = tk.Frame(self.container, bg=theme.SURFACE)
        self.log = scrolledtext.ScrolledText(
            self.logf, height=9, state=tk.DISABLED, wrap=tk.WORD, bd=0, relief=tk.FLAT,
            bg=theme.SURFACE, fg=theme.TEXT, insertbackground=theme.TEXT, font=f.mono,
            highlightthickness=0, padx=14, pady=10)
        self.log.pack(fill=tk.BOTH, expand=True)
        self._log_open = False
        if self._log_lines:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, "\n".join(self._log_lines) + "\n")
            self.log.configure(state=tk.DISABLED)

    def _toggle_log(self) -> None:
        self._log_open = not self._log_open
        if self._log_open:
            self.logf.pack(fill=tk.BOTH, expand=False)
            self.btn_log.configure(text="Hide log")
            self.log.see(tk.END)
        else:
            self.logf.pack_forget()
            self.btn_log.configure(text="Show log")

    def _render_checks(self, checks: list[tuple[str, str, str]]) -> None:
        """checks: (label, value, kind) where kind is ok / warn / bad / info."""
        for w in self.checks.winfo_children():
            w.destroy()
        if not checks:
            ttk.Label(self.checks, text="No device checks yet.",
                      style="Small.TLabel").grid(row=0, column=0, sticky="w")
            return
        styles = {"ok": "Ok.TLabel", "warn": "Warn.TLabel", "bad": "Bad.TLabel"}
        for i, (label, val, kind) in enumerate(checks):
            ttk.Label(self.checks, text=label, style="Muted.TLabel").grid(
                row=i, column=0, sticky="w", pady=3)
            ttk.Label(self.checks, text=val, style=styles.get(kind, "Strong.TLabel")).grid(
                row=i, column=1, sticky="e", pady=3)

    # ------------------------------------------------------------- sign out

    def on_sign_out(self) -> None:
        if self._busy:
            messagebox.showinfo("Filttr Supervise", "Wait for the current job to finish.")
            return
        if not messagebox.askyesno("Sign out", "Sign out and lock this tool?"):
            return
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self._device = None
        self._selected = None
        self._results = []

        def done(res) -> None:
            msg = res[1] if isinstance(res, tuple) else f"Sign out failed: {res}"
            self._build_signin()
            self.lbl_signin_msg.configure(text=msg, style="Small.TLabel")

        self._bg(lambda: untrust_device(self.config_data), done)

    # ------------------------------------------------------- user selection

    def _sync_access_key(self) -> None:
        self.config_data.access_key = self.var_access_key.get().strip()

    def _refresh_url_label(self) -> None:
        self._sync_access_key()
        url = self.config_data.configuration_url_for()
        self.lbl_url.configure(
            text=f"Enrollment link: {url}" if url
            else "Enrollment link: pick a user or paste an access key.")

    def _update_supervise_enabled(self) -> None:
        self._sync_access_key()
        ok = bool(self.config_data.access_key) and not self._busy
        self.btn_supervise.configure(state=tk.NORMAL if ok else tk.DISABLED)
        relay_ok = bool(self.config_data.relay_url) and bool(self.config_data.relay_token) and not self._busy
        self.btn_remote.configure(state=tk.NORMAL if relay_ok else tk.DISABLED)

    def _on_access_key_typed(self) -> None:
        key = self.var_access_key.get().strip()
        if self._selected and self._selected.access_key != key:
            self._selected = None
        if not self._selected:
            self.lbl_selected.configure(
                text=f"Access key typed by hand: {key}" if key else NO_SELECTION)
        self._refresh_url_label()
        self._update_supervise_enabled()

    def _apply_selected(self, user: PortalUser) -> None:
        self._selected = user
        self.config_data.access_key = user.access_key
        self.var_access_key.set(user.access_key)
        meta = [b for b in (user.email, f"user ID {user.user_id}" if user.user_id else "") if b]
        tail = ("  ·  " + "  ·  ".join(meta)) if meta else ""
        self.lbl_selected.configure(text=(user.name or "(no name)") + tail)
        self._refresh_url_label()
        self._update_supervise_enabled()
        self.append_log(f"Selected {user.label()}")

    def on_pick_user(self, _event=None) -> None:
        sel = self.tree_users.selection()
        if not sel:
            return
        idx = self.tree_users.index(sel[0])
        if 0 <= idx < len(self._results):
            self._apply_selected(self._results[idx])

    def _handle_auth_msg(self, msg: str) -> bool:
        if "unauthorized" in msg.lower() or "unlock the tool" in msg.lower():
            self.lbl_search_status.configure(
                text="The server rejected this session. Sign out and sign in again, or paste "
                     "the access key below.", style="Error.TLabel")
            self.entry_access_key.focus_set()
            return True
        return False

    def on_search(self) -> None:
        if self._busy:
            return
        q = self.var_query.get().strip()
        self.lbl_search_status.configure(text="Searching…", style="Small.TLabel")
        self.tree_users.delete(*self.tree_users.get_children())
        self._results = []

        def done(res) -> None:
            if isinstance(res, Exception):
                res = ([], f"Search failed: {res}")
            users, msg = res
            self._results = users
            self.tree_users.delete(*self.tree_users.get_children())
            for u in users:
                self.tree_users.insert(
                    "", tk.END, values=(u.name or "(no name)", u.email or "", u.user_id or ""))
            self.lbl_search_status.configure(text=msg, style="Small.TLabel")
            self.append_log(f"Search {q!r}: {msg}")
            self._handle_auth_msg(msg)

        self._bg(lambda: search_users(self.config_data, q), done)

    def on_lookup(self) -> None:
        if self._busy:
            return
        uid = self.var_userid.get().strip()

        def done(res) -> None:
            if isinstance(res, Exception):
                res = (None, f"Lookup failed: {res}")
            user, msg = res
            self.lbl_search_status.configure(text=msg, style="Small.TLabel")
            self.append_log(f"Look up {uid!r}: {msg}")
            if self._handle_auth_msg(msg):
                return
            if user:
                self._apply_selected(user)

        self._bg(lambda: lookup_user_id(self.config_data, uid), done)

    # -------------------------------------------------------------- device

    def _on_watcher_info(self, info: DeviceInfo) -> None:
        """Called on the watcher thread — hand back to Tk via after()."""
        try:
            self.after(0, lambda: None if self._busy else self._apply_device(info))
        except (tk.TclError, RuntimeError):
            pass

    def refresh_device(self) -> None:
        if self._busy or self._watcher is None:
            return
        self.lbl_device_status.configure(text="Reading the phone…", style="Warn.TLabel")
        self._watcher.refresh_now()

    def _apply_device(self, info: DeviceInfo) -> None:
        if not self._signed_in or not hasattr(self, "lbl_device_status"):
            return
        self._device = info
        if not info.connected:
            self.lbl_device_status.configure(text="No iPhone connected", style="Bad.TLabel")
            self.lbl_device_name.configure(text="")
            self.lbl_device_meta.configure(
                text=(info.error or "No iPhone found over USB.")
                + "\nConnect one with Apple Devices or iTunes installed.")
            self._render_checks([])
            return
        if info.paired:
            self.lbl_device_status.configure(text="Connected and trusted", style="Ok.TLabel")
        else:
            self.lbl_device_status.configure(text="Connected — tap Trust on the phone",
                                             style="Warn.TLabel")
        self.lbl_device_name.configure(text=info.name or "(no name)")
        self.lbl_device_meta.configure(
            text=(f"{info.product_type}  ·  iOS {info.product_version} "
                  f"({info.build_version})\nSerial {info.serial}\nUDID {info.udid}"))

        def yn(val, good):
            if val is None:
                return "Unknown", "info"
            return ("Yes" if val else "No"), ("ok" if val == good else "bad")

        act = info.activation_state or "unknown"
        sup = info.supervised
        self._render_checks([
            ("Trusted by this PC", *yn(info.paired, True)),
            ("Activation", act.title(), "ok" if act.lower() == "activated" else "warn"),
            ("Supervised now", "Unknown" if sup is None else ("Yes" if sup else "No"), "info"),
            ("Find My", *yn(info.find_my_enabled, False)),
            ("Encrypted backup", "Unknown" if info.will_encrypt_backup is None
             else ("Yes" if info.will_encrypt_backup else "No"),
             "warn" if info.will_encrypt_backup else "info"),
        ])
        lines = ["Before you start: turn Find My off, unlock the phone, and tap Trust."]
        if info.notes:
            lines.extend(f"• {n}" for n in info.notes)
        self.lbl_prep.configure(text="\n".join(lines))

    # --------------------------------------------------------------- flows

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for name in ("btn_refresh", "btn_remove", "btn_search", "btn_lookup",
                     "entry_query", "entry_access_key", "tree_users"):
            w = getattr(self, name, None)
            if w is None:
                continue
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self._update_supervise_enabled()

    # -------------------------------------------------------- remote iPhone ----

    def on_remote_iphone(self) -> None:
        """Start a relay session. Shows the user a one-time code to enter in
        the Filttr Agent program on their own PC, then supervises that phone
        as if it were plugged in locally."""
        if self._busy:
            return
        if not self.config_data.relay_url or not self.config_data.relay_token:
            messagebox.showerror(
                "Relay not configured",
                "Set relay_url and relay_token in config.json to use this feature.",
            )
            return
        _RemoteSessionDialog(self, self.config_data, self._selected)

    # -------------------------------------------------------- supervise ------

    def on_supervise(self) -> None:
        if self._busy:
            return
        self._sync_access_key()
        err = self.config_data.require_access_key()
        if err:
            messagebox.showerror("Pick a user first", err)
            return
        url = self.config_data.configuration_url_for()
        who = self.lbl_selected.cget("text")
        if not messagebox.askyesno(
            "Supervise this iPhone",
            f"{who}\n\n"
            "The phone will restart and come back supervised.\n"
            "Nothing on it is erased.\n\n"
            f"Enrollment link:\n{url}\n\n"
            "Find My must be off. Continue?",
        ):
            return
        self._run_flow(remove=False)

    def on_remove(self) -> None:
        if self._busy:
            return
        if not messagebox.askyesno(
            "Remove supervision",
            "Turn supervision off and make the MDM profile removable.\n"
            "Nothing on the phone is erased.\n\n"
            "This has not been tested on a real iPhone yet, and it does not unenroll "
            "the phone — you remove the MDM profile in Settings afterwards.\n\n"
            "Continue?",
        ):
            return
        self._run_flow(remove=True)

    def _run_flow(self, *, remove: bool) -> None:
        self._sync_access_key()
        selected = None if remove else self._selected
        self._set_busy(True)
        if self._watcher is not None:
            self._watcher.pause()
        if not self._log_open:
            self._toggle_log()
        self.append_log("─" * 44)
        self.append_log("Removing supervision…" if remove else "Supervising…")
        if not remove:
            self.append_log(f"Enrollment link: {self.config_data.configuration_url_for()}")

        def work() -> None:
            def progress(line: str) -> None:
                self.after(0, lambda l=line: self.append_log(l))

            try:
                result = supervise_device(self.config_data, remove=remove, progress=progress,
                                          selected_user=selected)
            except Exception as exc:  # never leave the window stuck busy
                from filttr_supervise.supervise import SuperviseResult

                result = SuperviseResult(ok=False, message=f"Unexpected error: {exc}")
                progress(result.message)

            def done() -> None:
                self._set_busy(False)
                if self._watcher is not None:
                    self._watcher.resume()
                if result.ok:
                    extra = ""
                    if result.enrollment_url and not remove:
                        extra = f"\n\nEnrollment link:\n{result.enrollment_url}"
                    self._show_done(
                        "Supervision removed" if remove else "Phone is restarting supervised",
                        (result.message or "Done") + extra)
                else:
                    messagebox.showerror(
                        "Filttr Supervise",
                        result.message or "It failed — open the log to see why.")
                self.refresh_device()

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _show_done(self, headline: str, detail: str) -> None:
        win = tk.Toplevel(self)
        win.title("Filttr Supervise")
        win.configure(bg=theme.BG)
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win)
        body.pack(fill=tk.BOTH, expand=True, padx=26, pady=22)
        ttk.Label(body, text=headline, style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(body, text=detail, style="Muted.TLabel", justify=tk.LEFT,
                  wraplength=430).pack(anchor=tk.W, pady=(8, 18))
        ttk.Button(body, text="Done", style="Primary.TButton",
                   command=win.destroy).pack(anchor=tk.E)
        try:
            self.lbl_device_status.configure(text=headline, style="Ok.TLabel")
        except tk.TclError:
            pass
        win.grab_set()


class _RemoteSessionDialog:
    """Modal dialog that manages a remote iPhone session via the USB relay.

    Flow:
      1. Creates a ShareHost session → relay returns a one-time code.
      2. Shows the code to the admin ("Give this to the user").
      3. User enters the code in their Filttr Agent program → relay pairs them.
      4. Admin clicks "Supervise" → runs the normal supervise flow over the tunnel.
      5. On close / Disconnect, tears down the tunnel.
    """

    def __init__(
        self,
        parent: FilttrSuperviseApp,
        config: AppConfig,
        selected,
    ) -> None:
        from filttr_supervise.usbtunnel import ShareHost, set_usbmux_override

        self._parent = parent
        self._config = config
        self._selected = selected
        self._host: Optional[ShareHost] = None
        self._port: Optional[int] = None
        self._set_usbmux = set_usbmux_override

        win = tk.Toplevel(parent)
        win.title("Remote iPhone")
        win.configure(bg=theme.BG)
        win.transient(parent)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._win = win

        body = ttk.Frame(win)
        body.pack(fill=tk.BOTH, expand=True, padx=26, pady=22)

        ttk.Label(body, text="Remote iPhone", style="Title.TLabel").pack(anchor=tk.W)
        self._lbl_status = ttk.Label(
            body, text="Starting…", style="Muted.TLabel", justify=tk.LEFT, wraplength=360
        )
        self._lbl_status.pack(anchor=tk.W, pady=(6, 0))

        self._code_frame = ttk.Frame(body)
        self._code_frame.pack(anchor=tk.W, pady=(14, 0))
        ttk.Label(self._code_frame, text="Give this code to the user:",
                  style="Small.TLabel").pack(anchor=tk.W)
        self._lbl_code = ttk.Label(
            self._code_frame, text="", style="Strong.TLabel",
            font=("Courier New", 18, "bold"),
        )
        self._lbl_code.pack(anchor=tk.W, pady=(4, 0))
        self._code_frame.pack_forget()   # hidden until we have a code

        btn_row = ttk.Frame(body)
        btn_row.pack(anchor=tk.E, pady=(20, 0))
        self._btn_supervise = ttk.Button(
            btn_row, text="Supervise now", style="Primary.TButton",
            command=self._on_supervise, state=tk.DISABLED,
        )
        self._btn_supervise.pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Disconnect", command=self._on_close).pack(
            side=tk.LEFT, padx=(10, 0)
        )

        win.grab_set()
        self._start()

    def _status(self, text: str) -> None:
        def _do():
            try:
                self._lbl_status.configure(text=text)
            except tk.TclError:
                pass
        self._parent.after(0, _do)

    def _on_code(self, code: str) -> None:
        def _do():
            try:
                self._lbl_code.configure(text=code)
                self._code_frame.pack(anchor=tk.W, pady=(14, 0))
                self._lbl_status.configure(
                    text="Waiting for the user to enter the code on their PC…"
                )
            except tk.TclError:
                pass
        self._parent.after(0, _do)

    def _on_ready(self, port: int) -> None:
        self._port = port
        self._set_usbmux(("127.0.0.1", port))
        def _do():
            try:
                self._lbl_status.configure(text="✅ User's phone is connected.")
                self._btn_supervise.configure(state=tk.NORMAL)
            except tk.TclError:
                pass
        self._parent.after(0, _do)

    def _on_end(self, reason: str) -> None:
        self._set_usbmux(None)
        def _do():
            try:
                if reason and reason != "The connection was closed.":
                    self._lbl_status.configure(text=f"Disconnected: {reason}")
                    self._btn_supervise.configure(state=tk.DISABLED)
                else:
                    try:
                        self._win.destroy()
                    except tk.TclError:
                        pass
            except tk.TclError:
                pass
        self._parent.after(0, _do)

    def _start(self) -> None:
        from filttr_supervise.usbtunnel import ShareHost

        self._host = ShareHost(
            self._config.relay_token,
            on_status=self._status,
            on_end=self._on_end,
            on_code=self._on_code,
            on_ready=self._on_ready,
            relay_url=self._config.relay_url,
        )
        self._host.start()

    def _on_supervise(self) -> None:
        if self._port is None:
            return
        self._parent._sync_access_key()
        err = self._config.require_access_key()
        if err:
            import tkinter.messagebox as mb
            mb.showerror("Configuration missing", err, parent=self._win)
            return
        self._btn_supervise.configure(state=tk.DISABLED)
        try:
            self._win.destroy()
        except tk.TclError:
            pass
        # run the normal supervise flow — pymobiledevice3 is already redirected
        self._parent._run_flow(remove=False)

    def _on_close(self) -> None:
        if self._host is not None:
            self._host.stop()
        self._set_usbmux(None)
        try:
            self._win.destroy()
        except tk.TclError:
            pass


def run_gui(config: Optional[AppConfig] = None) -> None:
    theme.enable_dpi_awareness()
    app = FilttrSuperviseApp(config=config)
    app.mainloop()
