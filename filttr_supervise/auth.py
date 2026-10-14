"""Admin unlock via POST /api/admin-2fa and per-PC trusted device storage.

Session tool tokens (8h) are kept in memory on ``AppConfig.session_token``.
Trusted-device secrets live only in a per-user file (never the admin code)::

    Windows: %APPDATA%/FilttrSupervise/device.json
    Linux:   ~/.config/FilttrSupervise/device.json
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config import AppConfig

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_EMAIL = "abbrachfeld@gmail.com"
SOURCE = "Windows supervisor tool"


def device_store_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "FilttrSupervise" / "device.json"


@dataclass
class DeviceRecord:
    email: str
    device: str

    def to_dict(self) -> dict[str, str]:
        return {"email": self.email, "device": self.device}


def load_device_record(path: Optional[Path] = None) -> Optional[DeviceRecord]:
    p = path or device_store_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    email = str(data.get("email") or "").strip().lower()
    device = str(data.get("device") or "").strip()
    if not email or not device:
        return None
    return DeviceRecord(email=email, device=device)


def save_device_record(email: str, device: str, path: Optional[Path] = None) -> Path:
    p = path or device_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"email": email.strip().lower(), "device": device.strip()}
    p.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def clear_device_record(path: Optional[Path] = None) -> None:
    p = path or device_store_path()
    try:
        p.unlink(missing_ok=True)
    except TypeError:
        if p.exists():
            p.unlink()


def effective_tool_token(config: AppConfig) -> str:
    """Session token first, then optional config tool_token / api_token override."""
    for attr in ("session_token", "tool_token", "api_token"):
        val = getattr(config, attr, None) or ""
        if str(val).strip():
            return str(val).strip()
    return ""


def set_session_token(config: AppConfig, token: str) -> None:
    config.session_token = (token or "").strip()


def _post_2fa(
    config: AppConfig,
    body: dict[str, Any],
    *,
    timeout: float = 20.0,
) -> tuple[dict[str, Any], int]:
    url = f"{config.api_base.rstrip('/')}/api/admin-2fa"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "FilttrSupervise/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else {}
            return parsed if isinstance(parsed, dict) else {"ok": False, "raw": raw}, resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail) if detail.strip() else {}
        except Exception:
            parsed = {"ok": False, "error": detail[:200]}
        if not isinstance(parsed, dict):
            parsed = {"ok": False, "error": detail[:200]}
        parsed.setdefault("ok", False)
        return parsed, exc.code
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 0


def checkcode(config: AppConfig, email: str, code: str) -> tuple[bool, str, str]:
    """Admin static code unlock. Returns (ok, message, token)."""
    data, status = _post_2fa(
        config, {"action": "checkcode", "email": email.strip().lower(), "code": code}
    )
    if data.get("ok") and data.get("token"):
        set_session_token(config, str(data["token"]))
        return True, "Unlocked with admin code (session ~8h).", str(data["token"])
    err = data.get("error") or f"HTTP {status}"
    return False, str(err), ""


def send_email_code(config: AppConfig, email: str) -> tuple[bool, str]:
    data, status = _post_2fa(
        config,
        {
            "action": "send",
            "email": email.strip().lower(),
            "source": SOURCE,
        },
    )
    if data.get("ok"):
        return True, "Code emailed — check inbox (expires in 10 minutes)."
    return False, str(data.get("error") or f"HTTP {status}")


def verify_email_code(
    config: AppConfig,
    email: str,
    code: str,
    *,
    trust: bool = True,
    store_path: Optional[Path] = None,
) -> tuple[bool, str, str]:
    """Verify emailed code. Returns (ok, message, token). Saves device if trust."""
    data, status = _post_2fa(
        config,
        {
            "action": "verify",
            "email": email.strip().lower(),
            "code": code.strip(),
            "trust": bool(trust),
        },
    )
    if not (data.get("ok") and data.get("token")):
        return False, str(data.get("error") or f"HTTP {status}"), ""
    token = str(data["token"])
    set_session_token(config, token)
    device = str(data.get("device") or "")
    days = data.get("trustDays", 60)
    if trust and device:
        save_device_record(email, device, path=store_path)
        return True, f"Unlocked. This PC trusted for ~{days} days.", token
    return True, "Unlocked (session ~8h).", token


def check_trusted_device(
    config: AppConfig,
    *,
    store_path: Optional[Path] = None,
) -> tuple[bool, str]:
    """Silent unlock using saved device token. Returns (ok, message)."""
    rec = load_device_record(path=store_path)
    if not rec:
        return False, "No trusted device on this PC."
    data, status = _post_2fa(
        config, {"action": "check", "email": rec.email, "device": rec.device}
    )
    if data.get("ok") and data.get("token"):
        set_session_token(config, str(data["token"]))
        return True, f"Unlocked via trusted device ({rec.email})."
    if status == 401:
        clear_device_record(path=store_path)
        return False, "Trusted device expired — please unlock again."
    return False, str(data.get("error") or f"HTTP {status}")


def untrust_device(
    config: AppConfig,
    *,
    store_path: Optional[Path] = None,
) -> tuple[bool, str]:
    rec = load_device_record(path=store_path)
    email = rec.email if rec else ""
    device = rec.device if rec else ""
    if email and device:
        _post_2fa(config, {"action": "untrust", "email": email, "device": device})
    clear_device_record(path=store_path)
    set_session_token(config, "")
    return True, "Signed out — this PC is no longer trusted."
