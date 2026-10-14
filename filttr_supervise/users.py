"""Portal user search and User ID lookup.

Search::
    GET {api_base}/api/search-users?q=<name>
    Header: x-tool-token: <session token from admin-2fa>

Lookup::
    GET {api_base}/api/lookup?userId=<id>
    Same token gate; returns { ok, accessKey, name, removalPassword }.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Optional

from .auth import effective_tool_token
from .config import AppConfig

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_PATH = "/api/search-users"
DEFAULT_LOOKUP_PATH = "/api/lookup"


@dataclass
class PortalUser:
    name: str
    email: str
    access_key: str
    user_id: str = ""

    def label(self) -> str:
        bits = [b for b in (self.name, self.email, self.user_id) if b]
        return " — ".join(bits) if bits else self.access_key

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _normalize_user(raw: Any) -> Optional[PortalUser]:
    if not isinstance(raw, dict):
        return None
    key = raw.get("accessKey") or raw.get("access_key") or raw.get("k") or ""
    key = str(key).strip()
    if not key:
        return None
    name = str(raw.get("name") or raw.get("displayName") or "").strip()
    email = str(raw.get("email") or raw.get("Email") or "").strip()
    user_id = str(raw.get("userId") or raw.get("user_id") or raw.get("id") or "").strip()
    return PortalUser(name=name, email=email, access_key=key, user_id=user_id)


def _extract_list(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("users", "results", "items", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return val
    return []


class UnauthorizedError(Exception):
    """Tool token rejected (401) — UI should re-prompt unlock."""


def _request_json(config: AppConfig, url: str, *, timeout: float = 15.0) -> Any:
    token = effective_tool_token(config)
    if not token:
        raise UnauthorizedError(
            "Unlock the tool first (Admin code or email code), "
            "or set optional tool_token in config.json."
        )
    headers = {
        "Accept": "application/json",
        "User-Agent": "FilttrSupervise/0.1",
        "x-tool-token": token,
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        if exc.code == 401:
            raise UnauthorizedError(
                "unauthorized — unlock the tool first"
                + (f" ({detail})" if detail else "")
            ) from exc
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def search_users(
    config: AppConfig,
    query: str,
    *,
    timeout: float = 15.0,
) -> tuple[list[PortalUser], str]:
    q = (query or "").strip()
    if len(q) < 2:
        return [], "Type at least 2 characters to search."
    path = (config.users_search_path or DEFAULT_SEARCH_PATH).strip()
    if not path.startswith("/"):
        path = "/" + path
    base = (config.api_base or "https://filttr.org").rstrip("/")
    token = effective_tool_token(config)
    url = f"{base}{path}?q={urllib.parse.quote(q)}"
    if token:
        url += f"&token={urllib.parse.quote(token)}"
    try:
        data = _request_json(config, url, timeout=timeout)
    except UnauthorizedError as exc:
        return [], str(exc)
    except Exception as exc:
        return [], f"User search failed: {exc}. Paste access key as fallback."

    if isinstance(data, dict) and data.get("ok") is False:
        return [], str(data.get("error") or data.get("message") or "search not ok")

    users: list[PortalUser] = []
    for item in _extract_list(data):
        u = _normalize_user(item)
        if u:
            users.append(u)
    if not users:
        return [], f"No users matched {q!r}."
    return users, f"Found {len(users)} user(s)."


def lookup_user_id(
    config: AppConfig,
    user_id: str,
    *,
    timeout: float = 15.0,
) -> tuple[Optional[PortalUser], str]:
    """GET /api/lookup?userId=… → PortalUser with accessKey."""
    uid = (user_id or "").strip()
    if not uid:
        return None, "Enter a User ID."
    path = "/api/lookup"
    base = (config.api_base or "https://filttr.org").rstrip("/")
    token = effective_tool_token(config)
    url = f"{base}{path}?userId={urllib.parse.quote(uid)}"
    if token:
        url += f"&token={urllib.parse.quote(token)}"
    try:
        data = _request_json(config, url, timeout=timeout)
    except UnauthorizedError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"Lookup failed: {exc}"

    if not isinstance(data, dict) or not data.get("ok"):
        return None, str((data or {}).get("error") if isinstance(data, dict) else "lookup failed")
    key = str(data.get("accessKey") or "").strip()
    if not key:
        return None, "Lookup returned no accessKey."
    user = PortalUser(
        name=str(data.get("name") or "").strip(),
        email="",
        access_key=key,
        user_id=uid,
    )
    return user, f"Found {user.label()}"
