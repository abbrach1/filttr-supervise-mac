from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from filttr_supervise.config import load_config
from filttr_supervise.users import PortalUser, _normalize_user, search_users


def test_normalize_accessKey() -> None:
    u = _normalize_user({"userId": "u1", "name": "Ada", "accessKey": "keyABC"})
    assert u is not None
    assert u.access_key == "keyABC"
    assert u.user_id == "u1"
    assert u.name == "Ada"


def test_search_requires_token(tmp_path) -> None:
    cfg = load_config(tmp_path / "missing.json")
    users, msg = search_users(cfg, "ada")
    assert users == []
    assert "Unlock" in msg or "tool_token" in msg or "unauthorized" in msg.lower()


def test_search_parses_ok_response(tmp_path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"tool_token": "tok123", "api_base": "https://filttr.org"}))
    # also exercise session_token preference

    cfg = load_config(p)
    payload = {
        "ok": True,
        "users": [
            {"userId": "id1", "name": "Ada Lovelace", "accessKey": "ak1"},
            {"userId": "id2", "name": "Bob", "accessKey": "ak2"},
        ],
    }
    fake = MagicMock()
    fake.read.return_value = json.dumps(payload).encode()
    fake.__enter__ = lambda s: s
    fake.__exit__ = MagicMock(return_value=False)
    fake.status = 200

    with patch("urllib.request.urlopen", return_value=fake) as m:
        users, msg = search_users(cfg, "Ada")
    assert len(users) == 2
    assert users[0].access_key == "ak1"
    assert "Found 2" in msg
    req = m.call_args[0][0]
    assert req.get_header("X-tool-token") == "tok123" or req.headers.get("x-tool-token") == "tok123"
    assert "/api/search-users" in req.full_url
    assert "q=Ada" in req.full_url


def test_portal_user_label() -> None:
    u = PortalUser(name="N", email="e@x", access_key="k", user_id="id")
    assert "N" in u.label() and "id" in u.label()
