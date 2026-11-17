"""Guards: never erase; supervise call sequence is backup-restore only."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from filttr_supervise.safety import (
    WipeForbiddenError,
    assert_not_erase_call,
    validate_device_for_supervise,
    wrap_forbid_erase,
)
from filttr_supervise.supervise import SAFE_RESTORE_KWARGS, supervise_device
from filttr_supervise.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def test_assert_not_erase_call_blocks() -> None:
    with pytest.raises(WipeForbiddenError):
        assert_not_erase_call("erase_device")
    with pytest.raises(WipeForbiddenError):
        assert_not_erase_call("MobileConfigService.EraseDevice")
    with pytest.raises(WipeForbiddenError):
        assert_not_erase_call("obliterate")
    assert_not_erase_call("Mobilebackup2Service.restore")  # allowed


def test_wrap_forbid_erase() -> None:
    class Svc:
        async def erase_device(self):
            return "nope"

    s = Svc()
    wrap_forbid_erase(s, "erase_device")
    with pytest.raises(WipeForbiddenError):
        s.erase_device()


def test_validate_find_my_and_activation() -> None:
    assert validate_device_for_supervise(
        find_my_enabled=True, activation_state="Activated", paired=True
    )
    assert validate_device_for_supervise(
        find_my_enabled=False, activation_state="Unactivated", paired=True
    )
    assert not validate_device_for_supervise(
        find_my_enabled=False, activation_state="Activated", paired=True
    )


def test_supervise_source_has_no_erase_or_mcinstall_set() -> None:
    src = (ROOT / "filttr_supervise" / "supervise.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.append(node.func.id)
    forbidden = {
        "erase_device",
        "EraseDevice",
        "set_cloud_configuration",
        "supervise",  # pymobiledevice3 MobileConfigService.supervise
        "obliterate",
    }
    hit = forbidden.intersection(calls)
    assert not hit, f"forbidden calls in supervise.py: {hit}"
    assert "restore" in calls
    assert "set_cloud_configuration" not in src
    # Docstring may mention EraseDevice as something we NEVER call
    assert "await" not in src or "erase_device" not in {
        n.func.attr for n in __import__("ast").walk(__import__("ast").parse(src))
        if isinstance(n, __import__("ast").Call) and isinstance(n.func, __import__("ast").Attribute)
    }


def test_safe_restore_kwargs_preserve_data() -> None:
    # remove=True is scoped to our one backup domain — it is what makes the device
    # actually apply the injected file instead of no-oping the restore.
    assert SAFE_RESTORE_KWARGS["remove"] is True
    assert SAFE_RESTORE_KWARGS["settings"] is True  # RestorePreserveSettings
    assert SAFE_RESTORE_KWARGS["skip_apps"] is True
    assert SAFE_RESTORE_KWARGS["copy"] is False
    # system=False: our file lives in a shared-container domain, not a system-files
    # domain. True triggers a device-wide system restore and can sign iCloud out.
    assert SAFE_RESTORE_KWARGS["system"] is False


def test_supervise_flow_never_calls_erase(tmp_path: Path) -> None:
    """Mock device layer: restore is called with safe kwargs; erase never is."""
    from filttr_supervise.device import DeviceInfo

    info = DeviceInfo(
        connected=True,
        name="iPhone",
        udid="UDID1",
        serial="S1",
        product_version="18.0",
        paired=True,
        trusted=True,
        activation_state="Activated",
        find_my_enabled=False,
        supervised=False,
    )
    cfg = load_config()
    cfg.access_key = "akTEST"
    # point cert at real assets
    cfg.supervisor_cert = str(ROOT / "assets" / "filttr-supervisor.der")

    restore_calls: list[dict] = []
    erase_calls: list[str] = []

    class FakeMB:
        def __init__(self, lockdown):
            self.lockdown = lockdown

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def restore(self, backup_directory, **kwargs):
            restore_calls.append(kwargs)
            if hasattr(self, "erase_device"):
                # ensure wrap would block — we record if anyone calls it
                pass

        async def erase_device(self, *a, **k):
            erase_calls.append("erase_device")

    class FakeDiag:
        def __init__(self, lockdown):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def restart(self):
            return None

    lockdown = MagicMock()
    lockdown.close = AsyncMock()

    async def fake_create(**kwargs):
        return lockdown

    presence = iter([False, True])

    async def fake_present(udid):
        return next(presence, True)

    async def fake_supervised(udid):
        return True

    with (
        patch("filttr_supervise.supervise._device_present", new=fake_present),
        patch("filttr_supervise.supervise._read_supervised", new=fake_supervised),
        patch("filttr_supervise.supervise.get_device_info", return_value=info),
        patch("pymobiledevice3.lockdown.create_using_usbmux", new=fake_create),
        patch(
            "pymobiledevice3.services.mobilebackup2.Mobilebackup2Service",
            FakeMB,
        ),
        patch(
            "pymobiledevice3.services.diagnostics.DiagnosticsService",
            FakeDiag,
        ),
        patch(
            "filttr_supervise.supervise.post_supervision_complete",
            return_value=(True, "ok"),
        ),
        patch(
            "filttr_supervise.supervise.post_assign_device",
            return_value=(True, "ok"),
        ),
    ):
        result = supervise_device(cfg)

    assert result.ok, result.log
    assert restore_calls, "restore was not called"
    kwargs = restore_calls[0]
    assert kwargs.get("remove") is True
    assert kwargs.get("settings") is True
    assert kwargs.get("system") is False
    assert kwargs.get("skip_apps") is True
    assert erase_calls == []
    assert result.method == "backup_restore"
    assert result.supervised_after is True and result.rebooted is True
    assert "restarted supervised" in result.message


def test_disconnect_without_reboot_is_not_success() -> None:
    """Field bug: link dropped, phone never restarted -> must NOT report success."""
    import asyncio
    from filttr_supervise import supervise as sv

    async def present(udid):
        return True

    async def not_sup(udid):
        return False

    restarts = []

    async def fake_reboot(lockdown, result, cb):
        restarts.append(1)

    res = sv.SuperviseResult(ok=False)
    with (
        patch.object(sv, "_device_present", new=present),
        patch.object(sv, "_read_supervised", new=not_sup),
        patch.object(sv, "_reboot", new=fake_reboot),
        patch.object(sv, "SELF_REBOOT_GRACE", 0.1),
    ):
        rebooted, supervised = asyncio.run(
            sv._finish_and_verify(MagicMock(), "U", sv.RESTORE_DISCONNECTED, True, res, None)
        )
    assert restarts == [1]  # explicit soft restart attempted, like Tech Lockdown
    assert rebooted is False and supervised is False
