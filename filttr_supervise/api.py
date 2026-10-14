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
    url = config.complete_url()
    payload: dict[str, Any] = {
        "organization_name": config.organization_name,
        "device": device_info,
        "access_key": config.access_key,
        "udid": device_info.get("udid") or "",
    }
    if user:
        payload["user"] = user
        payload["userId"] = user.get("user_id") or user.get("userId") or ""
        payload["accessKey"] = user.get("access_key") or user.get("accessKey") or config.access_key
    body = json.dumps(payload).encode("utf-8")
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
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=_auth_headers(config), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return True, f"POST {url} -> HTTP {resp.status}: {text[:200]}"
    except Exception as exc:
        msg = f"Assign/link POST {url} failed (non-fatal): {exc}"
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
