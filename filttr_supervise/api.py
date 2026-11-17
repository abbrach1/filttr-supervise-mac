"""Filttr API helpers: completion POST, optional assign/link, config lookup."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from filttr_supervise.auth import effective_tool_token
from filttr_supervise.config import AppConfig

logger = logging.getLogger(__name__)


def json_safe(value: Any) -> Any:
    """Recursively convert to JSON-serializable data.

    bytes -> base64 str (e.g. SupervisorHostCertificates DER), datetime -> ISO,
    sets/tuples -> lists, unknown objects -> str. Never raises.
    """
    import base64
    import datetime as _dt

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    return str(value)


def safe_json_bytes(payload: Any) -> bytes:
    return json.dumps(json_safe(payload)).encode("utf-8")


def device_payload(device_info: dict[str, Any]) -> dict[str, Any]:
    """Device dict for the server: drop the raw cloud configuration (holds DER bytes)."""
    out = {k: v for k, v in (device_info or {}).items() if k != "cloud_configuration"}
    return json_safe(out)


def _auth_headers(config: AppConfig) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "FilttrSupervise/0.1",
    }
    token = effective_tool_token(config)
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["x-tool-token"] = token
    return headers


def post_supervision_complete(
    config: AppConfig,
    device_info: dict[str, Any],
    *,
    user: Optional[dict[str, Any]] = None,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """POST device + user to ``api_base + complete_path``. Non-fatal on errors."""
    try:
        url = config.complete_url()
    except Exception as exc:
        return False, f"Completion report skipped (non-fatal): {exc}"
    device_info = device_info or {}
    payload: dict[str, Any] = {
        "organization_name": config.organization_name,
        "device": device_payload(device_info),
        "access_key": config.access_key,
        "udid": device_info.get("udid") or "",
    }
    if user:
        payload["user"] = user
        payload["userId"] = user.get("user_id") or user.get("userId") or ""
        payload["accessKey"] = user.get("access_key") or user.get("accessKey") or config.access_key
    body = safe_json_bytes(payload)
    req = urllib.request.Request(url, data=body, headers=_auth_headers(config), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return True, f"POST {url} -> HTTP {resp.status}: {text[:200]}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        msg = f"POST {url} failed: HTTP {exc.code} {detail}"
        logger.warning(msg)
        return False, msg
    except Exception as exc:
        msg = f"POST {url} failed (non-fatal): {exc}"
        logger.warning(msg)
        return False, msg


def post_assign_device(
    config: AppConfig,
    *,
    udid: str,
    access_key: str,
    user: Optional[dict[str, Any]] = None,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Best-effort link device→portal user via assign_path. Non-fatal."""
    path = (getattr(config, "assign_path", None) or "/api/supervision/assign").strip()
    if not path.startswith("/"):
        path = "/" + path
    url = f"{config.api_base.rstrip('/')}{path}"
    payload = {
        "udid": udid,
        "access_key": access_key,
        "accessKey": access_key,
        "user": user or {},
    }
    body = safe_json_bytes(payload)
    req = urllib.request.Request(url, data=body, headers=_auth_headers(config), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return True, f"POST {url} -> HTTP {resp.status}: {text[:200]}"
    except Exception as exc:
        msg = f"Assign/link POST {url} failed (non-fatal): {exc}"
        logger.info(msg)
        return False, msg


def post_unassign_device(
    config: AppConfig,
    *,
    udid: str,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Best-effort unlink of a device that was unsupervised (DELETE assign_path). Non-fatal."""
    path = (getattr(config, "assign_path", None) or "/api/supervision/assign").strip()
    if not path.startswith("/"):
        path = "/" + path
    url = f"{config.api_base.rstrip('/')}{path}"
    body = safe_json_bytes({"udid": udid})
    req = urllib.request.Request(url, data=body, headers=_auth_headers(config), method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return True, f"DELETE {url} -> HTTP {resp.status}: {text[:200]}"
    except Exception as exc:
        msg = f"Unlink DELETE {url} failed (non-fatal): {exc}"
        logger.info(msg)
        return False, msg


def lookup_configuration_url(
    config: AppConfig,
    udid: str,
    timeout: float = 15.0,
) -> Optional[str]:
    if not config.api_base:
        return None
    base = config.api_base.rstrip("/")
    url = f"{base}/api/supervision/config?udid={urllib.parse.quote(udid)}"
    req = urllib.request.Request(url, headers=_auth_headers(config), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict):
            return (
                data.get("configuration_url")
                or data.get("ConfigurationURL")
                or data.get("enrollment_url")
            )
    except Exception as exc:
        logger.info("configuration_url lookup skipped: %s", exc)
    return None


def validate_pin_code(
    config: AppConfig,
    pin: str,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Exchange a 6-digit setup PIN for an access key.

    Returns (True, access_key) on success, or (False, error_message) on failure.
    """
    if not config.api_base or not pin:
        return False, "Configuration missing or PIN empty"
    base = config.api_base.rstrip("/")
    url = f"{base}/api/validate-pin"
    payload = safe_json_bytes({"pin": str(pin).strip()})
    req = urllib.request.Request(url, data=payload, headers=_auth_headers(config), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict):
            if data.get("ok"):
                access_key = data.get("accessKey") or ""
                return True, access_key
            return False, data.get("error") or "Validation failed"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        if exc.code in (401, 404):
            return False, ("That code isn't valid. It may have expired or already been used — "
                           "ask your admin for a new one.")
        if exc.code == 429:
            return False, "Too many attempts. Please wait a few minutes and try again."
        return False, "Couldn't check your code right now. Please try again."
    except Exception as exc:
        logger.warning("PIN validation failed: %s", exc)
        return False, "Couldn't reach Filttr. Check your internet connection and try again."
