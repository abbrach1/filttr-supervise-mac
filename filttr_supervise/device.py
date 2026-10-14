"""Detect and inspect a USB-connected iPhone via pymobiledevice3."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    connected: bool = False
    name: str = ""
    udid: str = ""
    serial: str = ""
    product_type: str = ""
    product_version: str = ""
    build_version: str = ""
    model: str = ""
    paired: bool = False
    trusted: bool = False
    activation_state: str = ""
    supervised: Optional[bool] = None
    find_my_enabled: Optional[bool] = None
    will_encrypt_backup: Optional[bool] = None
    cloud_configuration: Optional[dict[str, Any]] = None
    error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _read_device_async(udid: Optional[str] = None) -> DeviceInfo:
    info = DeviceInfo()
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.usbmux import list_devices
    except Exception as exc:
        info.error = f"pymobiledevice3 import failed: {exc}"
        return info

    try:
        devices = await asyncio.wait_for(list_devices(), timeout=10)
    except Exception as exc:
        info.error = usbmux_error_message(exc)
        return info

    if not devices:
        info.error = "No USB device connected"
        return info

    target = None
    if udid:
        for d in devices:
            if getattr(d, "serial", None) == udid or str(d) == udid:
                target = d
                break
        if target is None:
            info.error = f"No device with UDID {udid}"
            return info
    else:
        target = devices[0]

    serial = getattr(target, "serial", None)
    lockdown = None
    last_exc: Optional[BaseException] = None
    # Never autopair here (that blocks on the Trust dialog). The pairing step
    # lives in supervise._connect_and_pair with explicit timeouts.
    for attempt in range(3):
        try:
            lockdown = await asyncio.wait_for(
                create_using_usbmux(serial=serial, autopair=False), timeout=20
            )
            break
        except asyncio.TimeoutError:
            last_exc = TimeoutError("lockdown connect timed out (20s)")
        except Exception as exc:
            last_exc = exc
            if "SessionActive" in type(exc).__name__ or "SessionActive" in str(exc):
                await asyncio.sleep(2)
                continue
            break
    if lockdown is None:
        info.error = f"Lockdown connection failed: {last_exc}"
        info.connected = True
        info.udid = serial or ""
        if last_exc is not None and "SessionActive" in (type(last_exc).__name__ + str(last_exc)):
            info.notes.append(
                "SessionActive: close iTunes / Apple Devices app and retry."
            )
        return info

    info.connected = True
    values: dict[str, Any] = {}
    try:
        values = dict(getattr(lockdown, "all_values", {}) or {})
    except Exception:
        values = {}

    if not values:
        for key in (
            "DeviceName",
            "UniqueDeviceID",
            "SerialNumber",
            "ProductType",
            "ProductVersion",
            "BuildVersion",
            "HardwareModel",
            "ActivationState",
        ):
            try:
                values[key] = await lockdown.get_value(key=key)
            except Exception:
                pass

    info.name = str(values.get("DeviceName") or "")
    info.udid = str(values.get("UniqueDeviceID") or getattr(lockdown, "udid", "") or "")
    info.serial = str(values.get("SerialNumber") or "")
    info.product_type = str(values.get("ProductType") or "")
    info.product_version = str(
        values.get("ProductVersion") or getattr(lockdown, "product_version", "") or ""
    )
    info.build_version = str(values.get("BuildVersion") or "")
    info.model = str(values.get("HardwareModel") or "")
    info.activation_state = str(values.get("ActivationState") or "")

    # create_using_usbmux already ran validate_pairing() and started a session
    # when a valid pair record exists; calling it again yields SessionActive.
    info.paired = bool(getattr(lockdown, "paired", False))
    info.trusted = info.paired
    if not info.paired:
        info.notes.append("Not paired yet — unlock the iPhone and tap Trust when prompted.")

    try:
        will_enc = await asyncio.wait_for(
            lockdown.get_value(domain="com.apple.mobile.backup", key="WillEncrypt"),
            timeout=10,
        )
        info.will_encrypt_backup = bool(will_enc)
    except Exception:
        info.will_encrypt_backup = None

    try:
        from pymobiledevice3.services.mobile_config import MobileConfigService

        if not info.paired:
            raise RuntimeError("device not paired")

        async def _cloud() -> Any:
            async with MobileConfigService(lockdown) as mc:
                return await mc.get_cloud_configuration()

        cloud = await asyncio.wait_for(_cloud(), timeout=15)
        info.cloud_configuration = cloud
        if isinstance(cloud, dict):
            info.supervised = bool(cloud.get("IsSupervised"))
        else:
            info.supervised = False
    except Exception as exc:
        info.notes.append(f"CloudConfiguration unavailable: {exc}")

    info.find_my_enabled = _guess_find_my(values, info.cloud_configuration)

    try:
        await asyncio.wait_for(lockdown.close(), timeout=5)
    except Exception:
        pass
    return info


def usbmux_error_message(exc: BaseException) -> str:
    """User-facing message for a failure to reach usbmuxd."""
    import sys

    text = f"{type(exc).__name__}: {exc}"
    unreachable = any(
        k in text
        for k in ("ConnectionFailedToUsbmuxd", "ConnectionRefused", "10061", "No such file", "TimeoutError")
    ) or isinstance(exc, (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError))
    if unreachable:
        if sys.platform.startswith("win"):
            return (
                "Cannot reach the Apple Mobile Device Service (usbmuxd). Install the "
                "'Apple Devices' app (Microsoft Store) or iTunes, make sure the 'Apple "
                "Mobile Device Service' is running (services.msc), then replug the iPhone."
            )
        return "Cannot reach usbmuxd — install/start usbmuxd and replug the iPhone."
    return f"usbmux list failed: {text}"


def _guess_find_my(
    values: dict[str, Any], cloud: Optional[dict[str, Any]]
) -> Optional[bool]:
    for key in ("FindMyiPhone", "IsFMiPEnabled", "FMiPProtectionState"):
        if key in values:
            return bool(values[key])
    return None


def get_device_info(udid: Optional[str] = None) -> DeviceInfo:
    """Synchronous wrapper used by the GUI and CLI."""
    try:
        return asyncio.run(_read_device_async(udid))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_read_device_async(udid))
        finally:
            loop.close()


async def _list_udids_async() -> list[str]:
    from pymobiledevice3.usbmux import list_devices

    devices = await asyncio.wait_for(list_devices(), timeout=10)
    return [getattr(d, "serial", str(d)) for d in devices]


def list_connected_udids() -> list[str]:
    try:
        return asyncio.run(_list_udids_async())
    except Exception:
        return []
