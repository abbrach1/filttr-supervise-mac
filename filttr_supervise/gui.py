"""Tkinter GUI for Filttr Supervise — unlock, user search, sparse supervise."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

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
from filttr_supervise.device import DeviceInfo, get_device_info
from filttr_supervise.supervise import supervise_device
from filttr_supervise.users import PortalUser, UnauthorizedError, lookup_user_id, search_users


class UnlockDialog(tk.Toplevel):
    """Modal unlock: admin code OR email code (+ trust this PC)."""

    def __init__(self, master: tk.Tk, config: AppConfig) -> None:
        super().__init__(master)
        self.config_data = config
        self.result_ok = False
        self.title("Unlock Filttr Supervise")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        pad = {"padx": 10, "pady": 6}

        ttk.Label(
            self,
            text="Unlock to search portal users (session token ~8 hours).",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(self, text="Admin email:").grid(row=1, column=0, sticky="e", **pad)
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

        ttk.Label(self, text="Code:").grid(row=4, column=0, sticky="e", **pad)
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

        self.lbl = ttk.Label(self, text="", wraplength=420)
        self.lbl.grid(row=6, column=0, columnspan=3, sticky="w", **pad)

        btns = ttk.Frame(self)
        btns.grid(row=7, column=0, columnspan=3, **pad)
        ttk.Button(btns, text="Unlock", command=self._unlock).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=4)

        self._toggle()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _toggle(self) -> None:
        email_mode = self.mode.get() == "email"
        state = tk.NORMAL if email_mode else tk.DISABLED
        self.btn_send.configure(state=state)
        self.chk_trust.configure(state=state)

    def _send(self) -> None:
        email = self.var_email.get().strip()
        ok, msg = send_email_code(self.config_data, email)
        self.lbl.configure(text=msg)
        if not ok:
            messagebox.showerror("Send failed", msg, parent=self)

    def _unlock(self) -> None:
        email = self.var_email.get().strip()
        code = self.var_code.get().strip()
        if not code:
            messagebox.showerror("Code required", "Enter the code.", parent=self)
            return
        if self.mode.get() == "admin":
            ok, msg, _tok = checkcode(self.config_data, email, code)
        else:
            ok, msg, _tok = verify_email_code(
                self.config_data, email, code, trust=self.var_trust.get()
            )
        self.lbl.configure(text=msg)
        if ok:
            self.result_ok = True
            self.destroy()
        else:
            messagebox.showerror("Unlock failed", msg, parent=self)

    def _cancel(self) -> None:
        self.result_ok = False
        self.destroy()


class FilttrSuperviseApp(tk.Tk):
    def __init__(self, config: Optional[AppConfig] = None) -> None:
        super().__init__()
        self.config_data = config or load_config()
        self.title("Filttr Supervise")
        self.geometry("800x740")
        self.minsize(700, 620)
        self._busy = False
        self._device: Optional[DeviceInfo] = None
        self._selected: Optional[PortalUser] = None
        self._results: list[PortalUser] = []
        self._build()
        # Sign-in is optional for now: never block on unlock at launch.
        self.after(100, self._startup_check_trust)
        self.after(300, self.refresh_device)
        self._update_supervise_enabled()

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(frm)
        top.pack(fill=tk.X, **pad)
        self.lbl_lock = ttk.Label(top, text="Not signed in (optional)", font=("", 10, "bold"))
        self.lbl_lock.pack(side=tk.LEFT)
        ttk.Button(top, text="Sign in (optional)…", command=self.open_unlock).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="Sign out / untrust this PC", command=self.on_sign_out).pack(
            side=tk.LEFT, padx=4
        )

        info = ttk.LabelFrame(frm, text="Device")
        info.pack(fill=tk.X, **pad)
        self.lbl_device = ttk.Label(info, text="Looking for USB iPhone…", justify=tk.LEFT)
        self.lbl_device.pack(anchor=tk.W, **pad)

        pre = ttk.LabelFrame(frm, text="Prechecks")
        pre.pack(fill=tk.X, **pad)
        self.lbl_pre = ttk.Label(pre, text="", justify=tk.LEFT)
        self.lbl_pre.pack(anchor=tk.W, **pad)

        userf = ttk.LabelFrame(frm, text="Portal user (applied to phone)")
        userf.pack(fill=tk.BOTH, **pad)

        row = ttk.Frame(userf)
        row.pack(fill=tk.X, **pad)
        ttk.Label(row, text="Search name:").pack(side=tk.LEFT)
        self.var_query = tk.StringVar()
        self.entry_query = ttk.Entry(row, textvariable=self.var_query, width=28)
        self.entry_query.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)
        self.entry_query.bind("<Return>", lambda _e: self.on_search())
        self.btn_search = ttk.Button(row, text="Search", command=self.on_search)
        self.btn_search.pack(side=tk.LEFT, padx=4)

        rowu = ttk.Frame(userf)
        rowu.pack(fill=tk.X, **pad)
        ttk.Label(rowu, text="User ID:").pack(side=tk.LEFT)
        self.var_userid = tk.StringVar()
        ttk.Entry(rowu, textvariable=self.var_userid, width=16).pack(side=tk.LEFT, padx=6)
        ttk.Button(rowu, text="Look up by User ID", command=self.on_lookup).pack(
            side=tk.LEFT, padx=4
        )

        self.lst_users = tk.Listbox(userf, height=5, exportselection=False)
        self.lst_users.pack(fill=tk.X, padx=8, pady=4)
        self.lst_users.bind("<<ListboxSelect>>", self.on_pick_user)

        self.lbl_selected = ttk.Label(
            userf,
            text="Selected: (none — search, look up, or paste access key)",
            justify=tk.LEFT,
            font=("", 11, "bold"),
            wraplength=740,
        )
        self.lbl_selected.pack(anchor=tk.W, **pad)
        self.lbl_search_status = ttk.Label(userf, text="", justify=tk.LEFT, wraplength=740)
        self.lbl_search_status.pack(anchor=tk.W, padx=8)

        row2 = ttk.Frame(userf)
        row2.pack(fill=tk.X, **pad)
        ttk.Label(row2, text="Access key fallback:").pack(side=tk.LEFT)
        self.var_access_key = tk.StringVar(value=self.config_data.access_key or "")
        self.entry_access_key = ttk.Entry(row2, textvariable=self.var_access_key, width=40)
        self.entry_access_key.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)
        self.var_access_key.trace_add("write", lambda *_: self._on_access_key_typed())

        self.lbl_cfg = ttk.Label(userf, text="", justify=tk.LEFT, wraplength=740)
        self.lbl_cfg.pack(anchor=tk.W, **pad)
        self._refresh_cfg_label()

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, **pad)
        self.btn_refresh = ttk.Button(btns, text="Refresh device", command=self.refresh_device)
        self.btn_refresh.pack(side=tk.LEFT, padx=4)
        self.btn_supervise = ttk.Button(btns, text="Supervise", command=self.on_supervise)
        self.btn_supervise.pack(side=tk.LEFT, padx=4)
        self.btn_remove = ttk.Button(btns, text="Remove supervision", command=self.on_remove)
        self.btn_remove.pack(side=tk.LEFT, padx=4)

        logf = ttk.LabelFrame(frm, text="Log")
        logf.pack(fill=tk.BOTH, expand=True, **pad)
        self.log = scrolledtext.ScrolledText(logf, height=11, state=tk.DISABLED, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def _startup_check_trust(self) -> None:
        """Silently reuse a trusted-PC session if one exists; never prompt."""
        try:
            ok, msg = check_trusted_device(self.config_data)
            if ok:
                self.append_log(msg)
        except Exception:
            pass
        self._refresh_lock_label()
        self.append_log(
            "Sign-in not required. Search users or paste an access key, then Supervise."
        )

    def _refresh_lock_label(self) -> None:
        if effective_tool_token(self.config_data):
            self.lbl_lock.configure(text="Unlocked")
        else:
            self.lbl_lock.configure(text="Not signed in (optional)")

    def open_unlock(self) -> None:
        dlg = UnlockDialog(self, self.config_data)
        self.wait_window(dlg)
        self._refresh_lock_label()
        if dlg.result_ok:
            self.append_log("Tool unlocked.")
        self._refresh_cfg_label()

    def on_sign_out(self) -> None:
        ok, msg = untrust_device(self.config_data)
        self.append_log(msg)
        self._refresh_lock_label()
        messagebox.showinfo("Signed out", msg)

    def _sync_access_key(self) -> None:
        self.config_data.access_key = self.var_access_key.get().strip()

    def _refresh_cfg_label(self) -> None:
        self._sync_access_key()
        preview = self.config_data.configuration_url_for()
        tok = "yes" if effective_tool_token(self.config_data) else "no"
        self.lbl_cfg.configure(
            text=(
                f"Signed in: {tok} (optional)  |  Does NOT erase the phone (sparse MobileBackup2 only)\n"
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
            self.lbl_selected.configure(text=f"Selected: (manual key) k={key[:12]}…")
        elif not self._selected and key:
            self.lbl_selected.configure(text=f"Selected: (manual key) k={key}")
        elif not key:
            self.lbl_selected.configure(
                text="Selected: (none — search, look up, or paste access key)"
            )
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
            users, msg = search_users(self.config_data, q)

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
            user, msg = lookup_user_id(self.config_data, uid)

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
        parts = [user.name or "(no name)"]
        if user.user_id:
            parts.append(f"userId={user.user_id}")
        if user.email:
            parts.append(user.email)
        parts.append(f"k={user.access_key}")
        self.lbl_selected.configure(text="Selected: " + " | ".join(parts))
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

        def work() -> None:
            info = get_device_info()
            self.after(0, lambda: self._apply_device(info))

        threading.Thread(target=work, daemon=True).start()

    def _apply_device(self, info: DeviceInfo) -> None:
        self._device = info
        if not info.connected:
            self.lbl_device.configure(text=info.error or "No USB iPhone connected")
            self.lbl_pre.configure(
                text="Connect an iPhone with Apple Devices / iTunes drivers installed."
            )
            return
        self.lbl_device.configure(
            text=(
                f"{info.name or '(unnamed)'}\n"
                f"UDID: {info.udid}\n"
                f"Serial: {info.serial}\n"
                f"Model: {info.product_type}  iOS: {info.product_version} ({info.build_version})"
            )
        )
        checks = [
            ("Paired / Trusted", info.paired),
            ("Activation", info.activation_state or "unknown"),
            ("Supervised (current)", info.supervised),
            ("Find My", info.find_my_enabled),
            ("Encrypted backup", info.will_encrypt_backup),
        ]
        lines = []
        for label, val in checks:
            if val is True:
                mark = "YES"
            elif val is False:
                mark = "no"
            else:
                mark = str(val)
            lines.append(f"• {label}: {mark}")
        lines.append(
            "Prep: Find My OFF, unlocked, Trust this PC. "
            "This tool never factory-resets the phone."
        )
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
        who = self.lbl_selected.cget("text")
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
        self.append_log("—" * 40)
        self.append_log("Starting removal…" if remove else "Starting sparse supervise…")
        if not remove:
            self.append_log(f"ConfigurationURL: {self.config_data.configuration_url_for()}")

        def work() -> None:
            def progress(line: str) -> None:
                self.after(0, lambda l=line: self.append_log(l))

            result = supervise_device(
                self.config_data,
                remove=remove,
                progress=progress,
                selected_user=selected,
            )

            def done() -> None:
                self._set_busy(False)
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
    app = FilttrSuperviseApp(config=config)
    app.mainloop()
