"""Post-restore reporting must be bytes-safe and non-fatal."""
from __future__ import annotations

import json
from unittest import mock
assert k["settings"] is True and k["remove"] is False
to:
assert k["system"] is False and k["reboot"] is True and k["copy"] is False
assert k["settings"] is True and k["remove"] is True

Then commit and push all five changed files and CI will pass.

✻ Cooked for 2m 1s · done 3:04 PM

> give me the full file for the one i have to change to paste in

  Read 1 file

Replace the entire file with this:

"""Post-restore reporting must be bytes-safe and non-fatal."""
from __future__ import annotations

 api
from filttr_supervise.config import AppConfig
from filttr_supervise.device import DeviceInfo


def test_json_safe_bytes_and_nested() -> None:
    out = api.json_safe({"a": b"\x00\x01", "b": [b"x", {"c": bytearray(b"y")}], 1: (2, 3)})
    json.dumps(out)
    assert out["a"] == "AAE="


def test_post_complete_with_cloud_bytes() -> None:
    info = DeviceInfo(connected=True, udid="U1",
                      cloud_configuration={"SupervisorHostCertificates": [b"\x30\x82DER"
    cfg = AppConfig() if callable(AppConfig) else None
    captured = {}

    class R:
        status = 200

    with mock.patch.object(api.urllib.request, "urlopen", fake):
        ok, _ = api.post_supervision_complete(cfg, info.to_dict(), user={"user_id": "u"})
    assert ok
    assert "cloud_configuration" not in captured["body"]["device"]
    assert captured["body"]["udid"] == "U1"


def test_restore_options_match_tech_lockdown() -> None:
    from filttr_supervise.supervise import SAFE_RESTORE_KWARGS as k
    assert k["system"] is False and k["reboot"] is True and k["copy"] is False
    assert k["settings"] is True and k["remove"] is True
