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
        devices = await list_devices()
    except Exception as exc:
        info.error = f"usbmux list failed: {exc}"
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

    try:
        lockdown = await create_using_usbmux(
            serial=getattr(target, "serial", None),
            autopair=False,
        )
    except Exception:
        try:
            lockdown = await create_using_usbmux(
                serial=getattr(target, "serial", None),
                autopair=True,
            )
        except Exception as exc2:
            info.error = f"Lockdown connection failed: {exc2}"
            info.connected = True
            info.udid = getattr(target, "serial", "") or ""
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

    try:
        info.paired = bool(await lockdown.validate_pairing())
        info.trusted = info.paired
    except Exception as exc:
        info.paired = False
        info.trusted = False
        info.notes.append(f"Pairing not validated: {exc}")

    try:
        will_enc = await lockdown.get_value(
            domain="com.apple.mobile.backup", key="WillEncrypt"
        )
        info.will_encrypt_backup = bool(will_enc)
    except Exception:
        info.will_encrypt_backup = None

    try:
        from pymobiledevice3.services.mobile_config import MobileConfigService

        async with MobileConfigService(lockdown) as mc:
            cloud = await mc.get_cloud_configuration()
        info.cloud_configuration = cloud
        if isinstance(cloud, dict):
            info.supervised = bool(cloud.get("IsSupervised"))
        else:
            info.supervised = False
    except Exception as exc:
        info.notes.append(f"CloudConfiguration unavailable: {exc}")

    info.find_my_enabled = _guess_find_my(values, info.cloud_configuration)

    try:
        await lockdown.close()
    except Exception:
        pass
    return info


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

    devices = await list_devices()
    return [getattr(d, "serial", str(d)) for d in devices]


def list_connected_udids() -> list[str]:
    try:
        return asyncio.run(_list_udids_async())
    except Exception:
        return []
