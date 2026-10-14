from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from filttr_supervise.auth import (
    check_trusted_device,
    checkcode,
    clear_device_record,
    load_device_record,
    save_device_record,
    send_email_code,
    untrust_device,
    verify_email_code,
)
from filttr_supervise.config import load_config


def _cfg(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"api_base": "https://www.filttr.org"}))
    return load_config(p)


def _fake_response(payload: dict, status: int = 200):
    fake = MagicMock()
    fake.read.return_value = json.dumps(payload).encode()
    fake.__enter__ = lambda s: s
    fake.__exit__ = MagicMock(return_value=False)
    fake.status = status
    return fake


def test_device_record_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "device.json"
    save_device_record("Admin@Example.com", "devtoken", path=path)
    rec = load_device_record(path=path)
    assert rec is not None
    assert rec.email == "admin@example.com"
    assert rec.device == "devtoken"
    clear_device_record(path=path)
    assert load_device_record(path=path) is None


def test_checkcode_sets_session(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"ok": True, "token": "sess1"}),
    ):
        ok, msg, token = checkcode(cfg, "abbrachfeld@gmail.com", "SECRET")
    assert ok and token == "sess1"
    assert cfg.session_token == "sess1"


def test_send_and_verify_trust(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    store = tmp_path / "device.json"
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"ok": True}),
    ):
        ok, msg = send_email_code(cfg, "abbrachfeld@gmail.com")
    assert ok
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response(
            {"ok": True, "token": "sess2", "device": "dev99", "trustDays": 60}
        ),
    ):
        ok, msg, token = verify_email_code(
            cfg, "abbrachfeld@gmail.com", "123456", trust=True, store_path=store
        )
    assert ok and token == "sess2"
    rec = load_device_record(path=store)
    assert rec and rec.device == "dev99"


def test_check_trusted_device(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    store = tmp_path / "device.json"
    save_device_record("abbrachfeld@gmail.com", "dev99", path=store)
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"ok": True, "token": "sess3"}),
    ):
        ok, msg = check_trusted_device(cfg, store_path=store)
    assert ok
    assert cfg.session_token == "sess3"


def test_untrust(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    store = tmp_path / "device.json"
    save_device_record("abbrachfeld@gmail.com", "dev99", path=store)
    cfg.session_token = "x"
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"ok": True}),
    ):
        ok, msg = untrust_device(cfg, store_path=store)
    assert ok
    assert load_device_record(path=store) is None
    assert cfg.session_token == ""
