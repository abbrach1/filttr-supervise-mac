"""End-user setup GUI for Filttr — complete guided flow.

Welcome → Setup Code → Find My Check → Processing → Enrollment → Success → Self-delete
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import scrolledtext, ttk
from typing import Optional

from filttr_supervise import theme
from filttr_supervise.config import AppConfig, load_config
from filttr_supervise.device import DeviceInfo, get_device_info
from filttr_supervise.supervise import supervise_device
from filttr_supervise.watcher import DeviceWatcher

COPYRIGHT = "© AB Brachfeld"
LOG_FONT_SIZE = 13
VERIFY_TIMEOUT = 15 * 60  # seconds to wait for the phone to restart, unlock and report supervised
NO_DEVICE_HINT_AFTER_MS = 15_000  # show cable/port tips if no iPhone shows up for this long
APPLE_DEVICES_URL = "https://apps.microsoft.com/detail/9np83lwlpz9k"  # Apple Devices app (USB driver)

_HIDDEN = re.compile(r"ConfigurationURL|configuration[_ ]url|enrollment (link|url)|access[_ ]?key|https?://|mdm-enroll|open this url|auto-enroll", re.I)


def _scrub(text: str) -> str:
    """Drop lines that expose the enrollment link / access key (end users never see these)."""
    kept = [ln for ln in (text or "").splitlines() if not _HIDDEN.search(ln)]
    return "\n".join(kept).strip()


# The engine emits technical progress lines for the admin tool. End users only see these
# friendly versions; any line that isn't translated below (and isn't already a friendly,
# emoji-led message from the engine) is dropped.
_FRIENDLY = [
    (re.compile(r"^Unlock the iPhone and tap Trust", re.I), "🔓 Unlock your iPhone and tap “Trust” if it asks."),
    (re.compile(r"^If the phone asks for its passcode", re.I), "🔑 If your iPhone asks for its passcode, enter it."),
    (re.compile(r"^Restore progress:\s*(\d+)%", re.I), r"⏳ Setting up… \1%"),
    (re.compile(r"^Restore completed successfully", re.I), "✓ Settings applied."),
    (re.compile(r"^Waiting up to \d+s for the phone to restart", re.I), "📲 Your iPhone will restart in a moment…"),
    (re.compile(r"^Phone took the restore but did not restart", re.I), "📲 Restarting your iPhone…"),
    (re.compile(r"^Phone is restarting", re.I), "📲 Your iPhone is restarting. Unlock it once it's back on."),
    (re.compile(r"^Phone is back", re.I), "🔓 Your iPhone is back. Unlock it so we can finish checking."),
    (re.compile(r"^The phone did not restart", re.I), "⚠ Your iPhone didn't restart. Please restart it yourself."),
]
_FRIENDLY_PREFIX = re.compile(r"^[📱⚙✅❌⚠⚡📲✓⏳🔓🔑⏱]")


def _friendly(line: str) -> str:
    """Translate one engine progress line into user-friendly text ('' = don't show)."""
    line = _scrub(line)
    out = []
    for ln in line.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        for rx, text in _FRIENDLY:
            if rx.search(ln):
                out.append(rx.sub(text, ln) if "\\" in text else text)
                break
        else:
            if _FRIENDLY_PREFIX.match(ln):
                out.append(ln)
    return "\n".join(out)


class FilttrUserApp(tk.Tk):
    def __init__(self, config: Optional[AppConfig] = None) -> None:
        super().__init__()
        self.config_data = config or load_config()
        self.title("Filttr Setup")
        self.fonts = theme.apply_theme(self)
        theme.set_window_icon(self)
        self.geometry("460x620")
        self.resizable(False, False)

        self._busy = False
        self._done = False
        self._device: Optional[DeviceInfo] = None
        self._watcher: Optional[DeviceWatcher] = None
        self._screen = "welcome"  # welcome → code → findmy → processing → enroll → success
        self._udid: Optional[str] = None
        self._confirmed = False  # True only once the phone reports IsSupervised
        self._verify_gen = 0
        self._details: list[str] = []  # scrubbed technical log, for "Copy details for admin"
        self._last_info: Optional[DeviceInfo] = None
        self._fail_kind = "phone"  # "code" (setup code rejected) or "phone" (anything else)
        self._nodev_job: Optional[str] = None

        self._img_mark = theme.load_image("filttr-mark-40.png")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_welcome()
        self.after(0, self._show_screen)

    def _clear(self) -> None:
        """Clear the container."""
        for w in self.winfo_children():
            w.destroy()

    # ================================================================ Support details
    def _note(self, line: str) -> None:
        """Remember a technical line for the support details (links / access key never kept)."""
        clean = _scrub(line)
        if clean:
            self._details.append(f"[{time.strftime('%H:%M:%S')}] " + clean.replace("\n", "\n           "))

    def _details_text(self) -> str:
        info = self._last_info
        lines = [
            "Filttr Setup details",
            time.strftime("%Y-%m-%d %H:%M:%S"),
            f"Windows: {platform.platform()}",
            f"Screen: {self._screen}",
        ]
        if info is not None:
            lines += [
                f"iPhone: {info.product_type or '?'}  iOS {info.product_version or '?'}  UDID {info.udid or '?'}",
                f"Connected={info.connected} Paired={info.paired} Supervised={info.supervised} "
                f"FindMy={info.find_my_enabled} Activation={info.activation_state or '?'}",
            ]
            if info.error:
                lines.append(f"Device error: {_scrub(info.error)}")
            lines += [f"Note: {_scrub(n)}" for n in info.notes if _scrub(n)]
        lines += ["", "--- log ---"] + self._details
        return "\n".join(lines)

    def _copy_details(self, btn) -> None:
        text = self._details_text()
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
        except tk.TclError:
            pass
        try:
            with open(os.path.join(tempfile.gettempdir(), "Filttr-Setup-details.txt"), "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass
        try:
            btn.configure(text="✓ Copied. Paste it to your admin.")
        except tk.TclError:
            pass

    def _add_header(self, parent) -> None:
        """Add בס"ד to top right."""
        header = ttk.Frame(parent)
        header.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(header, text="בס״ד", style="Small.TLabel", foreground=theme.MUTED).pack(side=tk.RIGHT)

    def _show_screen(self) -> None:
        """Render the current screen."""
        self._clear()
        if self._screen == "welcome":
            self._build_welcome()
        elif self._screen == "code":
            self._build_code()
            if not self._watcher:
                self._watcher = DeviceWatcher(self._on_watcher_info)
                self._watcher.start()
            elif self._last_info is not None:
                self._update_phone_status(self._last_info)  # screen was rebuilt: restore the status row
                self._watcher.refresh_now()
        elif self._screen == "findmy":
            self._build_findmy()
        elif self._screen == "processing":
            self._build_processing()
        elif self._screen == "enroll":
            self._build_enroll()
        elif self._screen == "success":
            self._build_success()

    # ================================================================ Welcome
    def _build_welcome(self) -> None:
        self._add_header(self)
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=32, pady=20)

        if self._img_mark:
            ttk.Label(outer, image=self._img_mark).pack(pady=(0, 16))

        ttk.Label(outer, text="Welcome to Filttr", style="Title.TLabel").pack()
        ttk.Label(outer, text="Content filtering for iOS",
                  style="Muted.TLabel").pack(pady=(4, 20))

        info = ttk.Frame(outer)
        info.pack(fill=tk.X, pady=(0, 20))
        for icon, text, strong in [
            ("🛡", "No data will be lost. Nothing is erased.", True),
            ("⚡", "Takes about 5 minutes", False),
            ("📱", "Works while you use your iPhone", False),
        ]:
            row = ttk.Frame(info)
            row.pack(fill=tk.X, pady=4)
            tk.Label(row, text=icon, bg=theme.BG, font=(self.fonts.body[0], 14)).pack(side=tk.LEFT, padx=(0, 8))
            if strong:
                tk.Label(row, text=text, bg=theme.BG, fg=theme.OK,
                         font=(self.fonts.body[0], self.fonts.body[1], "bold")).pack(side=tk.LEFT)
            else:
                ttk.Label(row, text=text, style="Small.TLabel").pack(side=tk.LEFT)

        ttk.Label(outer, text="Your admin gave you a setup code.\nIf you don't have one, ask them now.",
                  style="Small.TLabel", justify=tk.LEFT, wraplength=390).pack(anchor=tk.W, pady=(20, 0))

        theme.rule(outer).pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 0))
        ttk.Label(outer, text=COPYRIGHT, style="Small.TLabel").pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))

        ttk.Button(outer, text="Let's get started →", style="Primary.TButton",
                   command=lambda: self._go_to("code")).pack(fill=tk.X, pady=(20, 0), side=tk.BOTTOM)

    # ================================================================ Code Entry
    def _build_code(self) -> None:
        self._add_header(self)
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=32, pady=16)

        ttk.Label(outer, text="Enter setup code", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 4))

        self.var_pin = tk.StringVar()
        self.entry_pin = ttk.Entry(outer, textvariable=self.var_pin, font=self.fonts.body, show="•")
        self.entry_pin.pack(fill=tk.X, pady=(0, 4))
        self.entry_pin.bind("<Return>", lambda _: self._do_setup())
        self.var_pin.trace_add("write", lambda *_: self._update_code_btn())

        self.lbl_err = ttk.Label(outer, text="", style="Error.TLabel", wraplength=390)
        self.lbl_err.pack(anchor=tk.W)

        theme.rule(outer).pack(fill=tk.X, pady=(16, 16), side=tk.BOTTOM)

        # Live phone status
        status_frame = ttk.Frame(outer)
        status_frame.pack(fill=tk.X, pady=(14, 0), side=tk.BOTTOM)
        self._dot = tk.Label(status_frame, text="●", fg=theme.FAINT, bg=theme.BG, font=(self.fonts.body[0], 12))
        self._dot.pack(side=tk.LEFT)
        self.lbl_phone = ttk.Label(status_frame, text="Connect iPhone with USB cable",
                                   style="Muted.TLabel")
        self.lbl_phone.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_setup = ttk.Button(outer, text="Set up →", style="Primary.TButton", command=self._do_setup)
        self.btn_setup.pack(fill=tk.X, pady=(12, 0), side=tk.BOTTOM)

        # Shown only when needed (packed later, so they sit above the Set up button).
        self.lbl_hint = ttk.Label(outer, text="", style="Small.TLabel", foreground=theme.WARN,
                                  wraplength=390, justify=tk.LEFT)
        self.btn_driver = ttk.Button(outer, text="Get the Apple Devices app",
                                     command=lambda: webbrowser.open(APPLE_DEVICES_URL))
        self.btn_resume = ttk.Button(outer, text="Finish setup →", style="Primary.TButton",
                                     command=self._resume_existing)

        self.entry_pin.focus_set()

    def _update_code_btn(self) -> None:
        pin = self.var_pin.get().strip()
        ok = (len(pin) == 6 and pin.isdigit()) or (len(pin) == 8 and pin.replace(pin[0], '').replace(pin[-1], ''))
        self.btn_setup.configure(state=tk.NORMAL if ok and not self._busy else tk.DISABLED)

    def _on_watcher_info(self, info: DeviceInfo) -> None:
        self._last_info = info
        try:
            self.after(0, lambda: self._update_phone_status(info) if self._screen == "code" else None)
        except (tk.TclError, RuntimeError):
            pass

    def _is_already_ours(self, info: DeviceInfo) -> bool:
        """The phone is already supervised by Filttr (e.g. setup was closed half-way)."""
        if info.supervised is not True:
            return False
        org = str((info.cloud_configuration or {}).get("OrganizationName") or "").strip().lower()
        return not org or org == (self.config_data.organization_name or "Filttr").strip().lower()

    def _set_extras(self, hint: str = "", driver: bool = False, resume: bool = False) -> None:
        """Show/hide the hint line and the driver / finish buttons on the code screen."""
        try:
            for w in (self.lbl_hint, self.btn_driver, self.btn_resume):
                w.pack_forget()
            if resume:
                self.btn_resume.pack(fill=tk.X, pady=(12, 0), side=tk.BOTTOM)
            if driver:
                self.btn_driver.pack(fill=tk.X, pady=(12, 0), side=tk.BOTTOM)
            if hint:
                self.lbl_hint.configure(text=hint)
                self.lbl_hint.pack(anchor=tk.W, pady=(12, 0), side=tk.BOTTOM)
        except (tk.TclError, AttributeError):
            pass

    def _cancel_nodevice_hint(self) -> None:
        if self._nodev_job is not None:
            try:
                self.after_cancel(self._nodev_job)
            except (tk.TclError, ValueError):
                pass
            self._nodev_job = None

    def _nodevice_hint_fire(self) -> None:
        self._nodev_job = None
        info = self._last_info
        if self._screen == "code" and (info is None or not info.connected):
            self._set_extras(hint="Not detected? Try a different USB cable or port, "
                                  "and unlock your iPhone.")

    def _update_phone_status(self, info: DeviceInfo) -> None:
        if not self.winfo_exists():
            return
        self._last_info = info
        name = info.name or "iPhone"
        if info.error and "Apple Mobile Device Service" in info.error:
            self._cancel_nodevice_hint()
            self._dot.configure(fg=theme.BAD)
            self.lbl_phone.configure(text="Apple's USB driver is missing")
            self._set_extras(hint="Windows needs Apple's free “Apple Devices” app to talk to your iPhone. "
                                  "Install it, then unplug and replug your iPhone.", driver=True)
        elif not info.connected:
            self._dot.configure(fg=theme.FAINT)
            self.lbl_phone.configure(text="Connect iPhone with USB cable")
            self._set_extras()
            if self._nodev_job is None:
                self._nodev_job = self.after(NO_DEVICE_HINT_AFTER_MS, self._nodevice_hint_fire)
        elif info.error:  # plugged in but unreadable
            self._cancel_nodevice_hint()
            self._dot.configure(fg=theme.WARN)
            self.lbl_phone.configure(text=f"{name} — can't read it yet")
            self._set_extras(hint="Unlock your iPhone and tap “Trust” if it asks. "
                                  "If iTunes or the Apple Devices app is open, close it.")
        elif not info.paired:
            self._cancel_nodevice_hint()
            self._dot.configure(fg=theme.WARN)
            self.lbl_phone.configure(text=f"{name} — tap Trust")
            self._set_extras(hint="Unlock your iPhone, tap “Trust” on its screen, and enter your passcode.")
        else:
            self._cancel_nodevice_hint()
            self._dot.configure(fg=theme.OK)
            ios = info.product_version or ""
            self.lbl_phone.configure(text=f"{name}  ·  iOS {ios}" if ios else name)
            if self._is_already_ours(info):
                self._set_extras(hint="✓ This iPhone is already set up with Filttr. "
                                      "Tap Finish to complete the last step.", resume=True)
            else:
                self._set_extras()

    def _resume_existing(self) -> None:
        """Phone is already supervised by Filttr: skip the code and go to the final check."""
        info = self._last_info
        if info is None or not self._is_already_ours(info):
            return
        self._udid = info.udid or None
        self._confirmed = True
        self._note("Resumed: iPhone was already supervised by Filttr.")
        self._go_to("enroll")

    # ================================================================ Find My
    def _build_findmy(self) -> None:
        self._add_header(self)
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=32, pady=16)

        ttk.Label(outer, text="Turn off Find My", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(outer, text="Required for setup. You can turn it back on once setup is finished.",
                  style="Muted.TLabel", wraplength=390, justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 12))

        steps = ttk.Frame(outer)
        steps.pack(fill=tk.X)
        for i, text in enumerate([
            "Open Settings on your iPhone",
            "Tap [Your Name] at the top",
            "Go to Find My",
            "Turn off Find My iPhone",
            "Tap to confirm",
        ], 1):
            row = ttk.Frame(steps)
            row.pack(fill=tk.X, pady=6)
            tk.Label(row, text=str(i), bg=theme.INK, fg="#FFFFFF", font=(self.fonts.body[0], 10),
                     width=2, relief=tk.FLAT).pack(side=tk.LEFT, padx=(0, 10))
            ttk.Label(row, text=text, style="Small.TLabel").pack(side=tk.LEFT)

        tk.Label(outer, text="🛡 No data will be lost. Your photos, apps and messages are not touched.",
                 bg=theme.BG, fg=theme.OK, font=(self.fonts.body[0], self.fonts.body[1], "bold"),
                 wraplength=390, justify=tk.LEFT).pack(anchor=tk.W, pady=(20, 0))
        ttk.Label(outer, text="📲 Your iPhone will restart during setup. When it turns back on, "
                              "swipe up to upgrade.",
                  style="Small.TLabel", wraplength=390, justify=tk.LEFT).pack(anchor=tk.W, pady=(10, 0))
        ttk.Label(outer, text="⏱ This can take up to a minute.",
                  style="Small.TLabel", foreground=theme.WARN).pack(anchor=tk.W, pady=(8, 0))

        theme.rule(outer).pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 0))
        ttk.Label(outer, text=COPYRIGHT, style="Small.TLabel").pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))

        ttk.Button(outer, text="Done, continue →", style="Primary.TButton",
                   command=lambda: self._go_to("processing")).pack(fill=tk.X, pady=(16, 0), side=tk.BOTTOM)

    # ================================================================ Processing
    def _build_processing(self) -> None:
        self._add_header(self)
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=32, pady=16)

        ttk.Label(outer, text="Setting up…", style="Title.TLabel").pack(anchor=tk.W)
        tk.Label(outer, text="🛡 No data will be lost. Nothing is being erased.",
                 bg=theme.BG, fg=theme.OK, font=(self.fonts.body[0], self.fonts.body[1], "bold"),
                 wraplength=390, justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))

        self.lbl_status = ttk.Label(outer, text="", style="Small.TLabel", wraplength=390)
        self.lbl_status.pack(anchor=tk.W, pady=(8, 16))

        self.log = scrolledtext.ScrolledText(
            outer, height=7, state=tk.DISABLED, wrap=tk.WORD,
            bd=0, relief=tk.FLAT, bg=theme.SURFACE, fg=theme.TEXT,
            font=(self.fonts.body[0], LOG_FONT_SIZE), highlightthickness=0, padx=14, pady=12,
            spacing1=3, spacing3=3)
        self.log.pack(fill=tk.BOTH, expand=True)
        self.log.tag_config("info", foreground=theme.TEXT)
        self.log.tag_config("success", foreground=theme.OK, font=(self.fonts.body[0], LOG_FONT_SIZE, "bold"))
        self.log.tag_config("error", foreground=theme.BAD, font=(self.fonts.body[0], LOG_FONT_SIZE, "bold"))

        # Shown only if setup fails.
        self._fail_frame = ttk.Frame(outer)
        self.btn_retry = ttk.Button(self._fail_frame, text="Try again", style="Primary.TButton",
                                    command=self._retry)
        self.btn_retry.pack(fill=tk.X, pady=(0, 6))
        self.btn_details = ttk.Button(self._fail_frame, text="Copy details for admin",
                                      command=lambda: self._copy_details(self.btn_details))
        self.btn_details.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(self._fail_frame, text="Close", command=self._on_close).pack(fill=tk.X)

        if self._screen == "processing":
            self._run_setup()

    def _append_log(self, line: str) -> None:
        try:
            self.log.configure(state=tk.NORMAL)
            idx_start = self.log.index(tk.END)
            self.log.insert(tk.END, line + "\n")
            if "✅" in line or "✓" in line:
                self.log.tag_add("success", idx_start, f"{idx_start}+{len(line)}c")
            elif "❌" in line:
                self.log.tag_add("error", idx_start, f"{idx_start}+{len(line)}c")
            else:
                self.log.tag_add("info", idx_start, f"{idx_start}+{len(line)}c")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _set_status(self, text: str) -> None:
        try:
            self.lbl_status.configure(text=text)
        except tk.TclError:
            pass

    def _do_setup(self) -> None:
        if self._busy:
            return
        pin = self.var_pin.get().strip()
        if not pin or (len(pin) != 6 and len(pin) != 8):
            self.lbl_err.configure(text="Enter a valid setup code (6 or 8 characters)")
            self.entry_pin.focus_set()
            return
        self.lbl_err.configure(text="")
        self.config_data.access_key = ""  # a freshly typed code is exchanged for a new key
        self._go_to("findmy")

    def _run_setup(self) -> None:
        """Run setup. The code is single-use, so its access key is kept in memory after it is
        accepted; a retry skips the code and goes straight back to the phone step."""
        self._busy = True
        pin = self.var_pin.get().strip()

        def work() -> None:
            from filttr_supervise.api import validate_pin_code
            from filttr_supervise.supervise import SuperviseResult

            def progress(line: str) -> None:
                self._note(line)
                shown = _friendly(line)
                if shown:
                    self.after(0, lambda l=shown: (self._append_log(l), self._set_status(l)))

            kind = "phone"
            try:
                if not self.config_data.access_key:
                    progress("📱 Validating setup code…")
                    ok, msg = validate_pin_code(self.config_data, pin)
                    if ok:
                        self.config_data.access_key = msg
                    else:
                        kind = "code"
                        result = SuperviseResult(ok=False, message=msg or "Code validation failed.")
                if kind != "code":
                    progress("⚙ Configuring your phone…")
                    result = supervise_device(self.config_data, remove=False, progress=progress)
            except Exception as exc:
                result = SuperviseResult(ok=False, message=f"Error: {exc}")

            self._fail_kind = kind
            self.after(0, lambda: self._finish_setup(result))

        threading.Thread(target=work, daemon=True).start()

    def _finish_setup(self, result) -> None:
        self._busy = False
        if result.ok:
            self._udid = getattr(getattr(result, "device", None), "udid", None)
            self._confirmed = result.supervised_after is True
            self._go_to("enroll")
        else:
            self._note(f"FAILED: {result.message}")
            if getattr(result, "device", None) is not None:
                self._last_info = result.device
            self._append_log(f"❌ {_scrub(result.message) or 'Something went wrong.'}")
            self._show_failure()

    def _show_failure(self) -> None:
        try:
            if self._fail_kind == "code":
                self._set_status("We couldn't use that setup code.")
                self.btn_retry.configure(text="Enter code again", command=self._back_to_code)
            else:
                self._set_status("Setup didn't finish. No data was lost, and you can try again.")
                self.btn_retry.configure(text="Try again", command=self._retry)
            self.btn_details.configure(text="Copy details for admin")
            self._fail_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(12, 0), before=self.log)
        except (tk.TclError, AttributeError):
            pass

    def _retry(self) -> None:
        """Run the phone step again. The setup code is not needed a second time."""
        if self._busy:
            return
        try:
            self._fail_frame.pack_forget()
            self.log.configure(state=tk.NORMAL)
            self.log.delete("1.0", tk.END)
            self.log.configure(state=tk.DISABLED)
        except (tk.TclError, AttributeError):
            pass
        self._set_status("")
        self._note("--- retry ---")
        self._run_setup()

    def _back_to_code(self) -> None:
        self._go_to("code")

    # ================================================================ Enroll
    def _build_enroll(self) -> None:
        self._add_header(self)
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=32, pady=20)

        if self._img_mark:
            ttk.Label(outer, image=self._img_mark).pack(pady=(0, 16))

        ttk.Label(outer, text="Your phone is restarting…", style="Title.TLabel").pack()
        ttk.Label(outer, text="It will take a few minutes to come back on.",
                  style="Muted.TLabel").pack(pady=(4, 24))

        # Instructions
        steps = ttk.Frame(outer)
        steps.pack(fill=tk.X)
        for i, text in enumerate([
            "Wait for your iPhone to restart",
            "Unlock it when it's back on",
            "Tap “Enroll in Filttr” when it appears",
            "Follow the prompts (may ask for iCloud password)",
        ], 1):
            row = ttk.Frame(steps)
            row.pack(fill=tk.X, pady=6)
            tk.Label(row, text=str(i), bg=theme.INK, fg="#FFFFFF", font=(self.fonts.body[0], 10),
                     width=2, relief=tk.FLAT).pack(side=tk.LEFT, padx=(0, 10))
            ttk.Label(row, text=text, style="Small.TLabel", wraplength=350).pack(side=tk.LEFT)

        theme.rule(outer).pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 0))
        ttk.Label(outer, text=COPYRIGHT, style="Small.TLabel").pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))

        self.btn_finish = ttk.Button(outer, text="Finish →", style="Primary.TButton",
                                     state=tk.DISABLED, command=lambda: self._go_to("success"))
        self.btn_finish.pack(fill=tk.X, pady=(12, 0), side=tk.BOTTOM)
        self.btn_recheck = ttk.Button(outer, text="Check again", command=self._start_verify)
        self.btn_details_v = ttk.Button(outer, text="Copy details for admin",
                                        command=lambda: self._copy_details(self.btn_details_v))

        vrow = ttk.Frame(outer)
        vrow.pack(fill=tk.X, pady=(16, 0), side=tk.BOTTOM)
        self._vdot = tk.Label(vrow, text="●", fg=theme.WARN, bg=theme.BG, font=(self.fonts.body[0], 12))
        self._vdot.pack(side=tk.LEFT)
        self.lbl_verify = ttk.Label(vrow, text="Waiting for your iPhone to restart…",
                                    style="Muted.TLabel", wraplength=340, justify=tk.LEFT)
        self.lbl_verify.pack(side=tk.LEFT, padx=(8, 0))
        self._start_verify()

    def _set_verify(self, text: str, color: str) -> None:
        try:
            self._vdot.configure(fg=color)
            self.lbl_verify.configure(text=text)
        except (tk.TclError, AttributeError):
            pass

    def _start_verify(self) -> None:
        """Poll the phone until it reports supervised. Never finishes (or deletes) without this."""
        self._verify_gen += 1
        gen = self._verify_gen
        try:
            self.btn_recheck.pack_forget()
            self.btn_details_v.pack_forget()
        except (tk.TclError, AttributeError):
            pass
        if self._confirmed:
            self._verified_ok()
            return
        self._set_verify("Waiting for your iPhone to restart…", theme.WARN)

        def poll() -> None:
            deadline = time.time() + VERIFY_TIMEOUT
            last_note = ""
            while time.time() < deadline and gen == self._verify_gen and self._screen == "enroll":
                try:
                    info = get_device_info(self._udid)
                except Exception:
                    info = None
                if info is not None:
                    self._last_info = info
                state = ("no device" if info is None or not info.connected
                         else f"supervised={info.supervised}")
                if state != last_note:  # keep the support log short: only state changes
                    self._note(f"Verify: {state}")
                    last_note = state
                if info is None or not info.connected:
                    msg = "Waiting for your iPhone to restart…"
                elif info.supervised is True:
                    self.after(0, lambda: gen == self._verify_gen and self._verified_ok())
                    return
                elif info.supervised is None:
                    msg = "Unlock your iPhone with your passcode so we can finish checking."
                else:
                    msg = "Almost there — checking your iPhone…"
                self.after(0, lambda m=msg: gen == self._verify_gen and self._set_verify(m, theme.WARN))
                time.sleep(4)
            if gen == self._verify_gen and self._screen == "enroll":
                self.after(0, self._verify_timed_out)

        threading.Thread(target=poll, daemon=True).start()

    def _verified_ok(self) -> None:
        self._confirmed = True
        self._set_verify("✓ Confirmed — your iPhone is supervised.", theme.OK)
        try:
            self.btn_finish.configure(state=tk.NORMAL)
        except (tk.TclError, AttributeError):
            pass

    def _verify_timed_out(self) -> None:
        self._set_verify("Couldn't confirm yet. Keep the iPhone unlocked and plugged in, then tap Check again.",
                         theme.BAD)
        self._note("Verify: timed out")
        try:
            self.btn_details_v.configure(text="Copy details for admin")
            self.btn_recheck.pack(fill=tk.X, pady=(8, 0), side=tk.BOTTOM, before=self.btn_finish)
            self.btn_details_v.pack(fill=tk.X, pady=(8, 0), side=tk.BOTTOM, before=self.btn_finish)
        except (tk.TclError, AttributeError):
            pass

    # ================================================================ Success
    def _build_success(self) -> None:
        self._add_header(self)
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=40, pady=28)

        if self._img_mark:
            ttk.Label(outer, image=self._img_mark).pack(pady=(0, 20))

        ttk.Label(outer, text="All set!", style="Title.TLabel").pack()
        ttk.Label(outer, text="Your phone is now protected.\n\n"
                            "Your admin will push your filtering config soon.\n\n"
                            "You can close this window.",
                  style="Muted.TLabel", justify=tk.CENTER, wraplength=360).pack(pady=(12, 0))

        tk.Label(outer, text="🔔 Reminder: turn Find My back on.\n"
                             "Settings → [Your Name] → Find My → Find My iPhone",
                 bg=theme.BG, fg=theme.WARN, font=(self.fonts.body[0], self.fonts.body[1], "bold"),
                 justify=tk.CENTER, wraplength=360).pack(pady=(20, 0))

        ttk.Button(outer, text="Close", style="Primary.TButton",
                   command=self._close_and_cleanup).pack(pady=(28, 0))

        theme.rule(outer).pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 0))
        ttk.Label(outer, text=COPYRIGHT, style="Small.TLabel").pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))

    # ================================================================ Navigation & Cleanup
    def _go_to(self, screen: str) -> None:
        self._screen = screen
        self._show_screen()

    def _close_and_cleanup(self) -> None:
        self._on_close()

    def _schedule_deletion(self) -> None:
        """Delete the exe this app was launched from, once Windows releases it.

        Only runs after supervision was confirmed and the success screen is closed.
        """
        if not getattr(sys, "frozen", False) or not self._confirmed:
            return
        exe_path = sys.executable
        try:
            bat = os.path.join(tempfile.gettempdir(), f"filttr-cleanup-{os.getpid()}.bat")
            with open(bat, "w", newline="") as fh:
                fh.write(
                    '@echo off\r\n'
                    'for /l %%i in (1,1,60) do (\r\n'
                    f'  del /f /q "{exe_path}" >nul 2>&1\r\n'
                    f'  if not exist "{exe_path}" goto :done\r\n'
                    '  ping -n 2 127.0.0.1 >nul\r\n'
                    ')\r\n'
                    ':done\r\n'
                    '(goto) 2>nul & del "%~f0"\r\n'
                )
            subprocess.Popen(
                ["cmd", "/c", bat],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def _on_close(self) -> None:
        """Closing from the success screen (verified) deletes the exe; closing earlier never does."""
        done = self._screen == "success" and self._confirmed
        self._verify_gen += 1
        if self._watcher:
            self._watcher.stop()
        self.destroy()
        if done:
            self._schedule_deletion()


def run_user_gui(config: Optional[AppConfig] = None) -> None:
    theme.enable_dpi_awareness()
    app = FilttrUserApp(config=config)
    app.mainloop()
