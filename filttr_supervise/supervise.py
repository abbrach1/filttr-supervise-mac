"""Supervise / unsupervise an iPhone over USB for Filttr MDM.

ONLY method: restore a minimal MobileBackup2 package that contains
CloudConfigurationDetails.plist under
SysSharedContainerDomain-systemgroup.com.apple.configurationprofiles/
Library/ConfigurationProfiles/, then reboot. User data stays intact.

This tool NEVER calls EraseDevice / obliterate / factory reset / MCInstall
SetCloudConfiguration (which can require erase to replace an existing config).
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .api import lookup_configuration_url, post_assign_device, post_supervision_complete
from .backup_builder import build_supervision_backup
from .config import AppConfig, load_config
from .device import DeviceInfo, get_device_info
from .plist_builder import (
    build_cloud_configuration,
    build_removal_configuration,
    load_supervisor_cert_der,
)
from .safety import (
    WipeForbiddenError,
    validate_device_for_supervise,
    wrap_forbid_erase,
)

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

# Documented safe Mobilebackup2Service.restore kwargs for a sparse inject:
#   system=True   — required so SysSharedContainerDomain files in OUR package apply;
#                   the package contains only CloudConfigurationDetails.plist, not a
#                   full system image / IPSW.
#   settings=True — maps to RestorePreserveSettings=True (keep user settings).
#   remove=False  — do NOT delete device items missing from the backup.
#   copy=False    — no need to clone the backup folder on disk.
#   reboot=False  — we reboot explicitly via DiagnosticsService.restart (not erase).
#   skip_apps=True — do not reinstall apps from Info.plist.
SAFE_RESTORE_KWARGS = {
    "system": True,
    "settings": True,
    "remove": False,
    "copy": False,
    "reboot": False,
    "skip_apps": True,
    "source": ".",
}


@dataclass
class SuperviseResult:
    ok: bool
    method: str = ""
    message: str = ""
    device: Optional[DeviceInfo] = None
    backup_path: Optional[str] = None
    enrollment_url: str = ""
    api_post_message: str = ""
    log: list[str] = field(default_factory=list)

    def emit(self, line: str, cb: Optional[ProgressCb] = None) -> None:
        self.log.append(line)
        logger.info(line)
        if cb:
            cb(line)


def _resolve_config_url(
    config: AppConfig, udid: str, result: SuperviseResult, cb: Optional[ProgressCb]
) -> str:
    err = config.require_access_key()
    if err:
        result.emit(err, cb)
        return ""
    url = config.configuration_url_for(udid)
    if url:
        result.emit(f"ConfigurationURL: {url}", cb)
        return url
    looked = lookup_configuration_url(config, udid)
    if looked:
        result.emit(f"Using ConfigurationURL from server lookup: {looked}", cb)
        return looked
    result.emit(
        "Warning: ConfigurationURL is empty — set access_key / select a portal user.",
        cb,
    )
    return ""


def dry_run_build_backup(
    output_dir: str | Path,
    config: Optional[AppConfig] = None,
    udid: str = "DRYRUN-UDID",
    *,
    remove: bool = False,
) -> Path:
    """Build the backup folder without talking to a device."""
    config = config or load_config()
    cert_path = config.resolve_cert_path()
    if remove:
        cloud = build_removal_configuration(organization_name=config.organization_name)
    else:
        cert_der = load_supervisor_cert_der(cert_path)
        cloud = build_cloud_configuration(
            supervisor_cert_der=cert_der,
            organization_name=config.organization_name,
            configuration_url=config.configuration_url_for(udid),
            is_mdm_unremovable=config.is_mdm_unremovable,
        )
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    return build_supervision_backup(out, cloud, source_identifier=udid)


async def _restore_via_backup(
    lockdown: Any,
    cloud: dict[str, Any],
    result: SuperviseResult,
    cb: Optional[ProgressCb],
    *,
    reboot: bool = True,
) -> bool:
    from pymobiledevice3.exceptions import ConnectionTerminatedError, PyMobileDevice3Exception
    from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service

    # Runtime belt-and-suspenders: never allow erase_device on this service instance.
    result.emit(
        "Building minimal MobileBackup2 package (CloudConfigurationDetails only)…",
        cb,
    )
    with tempfile.TemporaryDirectory(prefix="filttr-supervise-") as tmp:
        backup_root = Path(tmp)
        device_dir = build_supervision_backup(backup_root, cloud, source_identifier=".")
        result.backup_path = str(device_dir)
        result.emit(f"Backup package ready under {device_dir}", cb)
        result.emit(
            "Restoring sparse package via MobileBackup2 "
            f"(kwargs={SAFE_RESTORE_KWARGS}) — does NOT erase user data…",
            cb,
        )
        try:
            async with Mobilebackup2Service(lockdown) as mb:
                wrap_forbid_erase(mb, "erase_device")
                await mb.restore(backup_root, **SAFE_RESTORE_KWARGS)
            result.emit("MobileBackup2 restore finished.", cb)
            result.method = "backup_restore"
            if reboot:
                await _reboot(lockdown, result, cb)
            return True
        except WipeForbiddenError:
            raise
        except (ConnectionTerminatedError, ssl.SSLEOFError, ConnectionAbortedError, ConnectionResetError) as exc:
            result.emit(
                f"Device disconnected during restore (likely reboot): {exc}",
                cb,
            )
            result.method = "backup_restore"
            return True
        except PyMobileDevice3Exception as exc:
            msg = str(exc)
            if "Find My" in msg:
                result.emit(
                    "Restore blocked: Find My must be turned off "
                    "(Settings → [Name] → Find My).",
                    cb,
                )
            else:
                result.emit(f"MobileBackup2 restore failed: {exc}", cb)
            return False
        except Exception as exc:
            result.emit(f"MobileBackup2 restore failed: {exc}", cb)
            return False


async def _reboot(lockdown: Any, result: SuperviseResult, cb: Optional[ProgressCb]) -> None:
    """Soft restart only — never erase."""
    try:
        from pymobiledevice3.services.diagnostics import DiagnosticsService

        result.emit("Requesting device reboot (restart, not erase)…", cb)
        async with DiagnosticsService(lockdown) as diag:
            # Guard: diagnostics must not expose an erase we accidentally call
            wrap_forbid_erase(diag, "erase_device")
            await diag.restart()
    except Exception as exc:
        result.emit(
            f"Reboot request note: {exc} (device may still reboot on its own)",
            cb,
        )


async def _show_enrollment_url(
    config: AppConfig,
    result: SuperviseResult,
    cb: Optional[ProgressCb],
) -> None:
    enrollment = config.enrollment_url_for() or result.enrollment_url
    result.enrollment_url = enrollment
    if not enrollment:
        result.emit("No enrollment_url — MDM profile step skipped.", cb)
        return
    result.emit(
        f"After reboot, open this enrollment URL on the iPhone (Safari) if MDM "
        f"did not auto-pull from ConfigurationURL:\n{enrollment}",
        cb,
    )


async def _run_supervise_async(
    config: AppConfig,
    *,
    remove: bool = False,
    dry_run_dir: Optional[Path] = None,
    progress: Optional[ProgressCb] = None,
    udid: Optional[str] = None,
    selected_user: Optional[Any] = None,
) -> SuperviseResult:
    result = SuperviseResult(ok=False)

    if dry_run_dir is not None:
        path = dry_run_build_backup(
            dry_run_dir, config, udid=udid or "DRYRUN-UDID", remove=remove
        )
        result.ok = True
        result.method = "dry_run"
        result.backup_path = str(path)
        result.emit(f"Dry-run backup written to {path}", progress)
        return result

    info = get_device_info(udid)
    result.device = info
    if not info.connected or not info.udid:
        result.message = info.error or "No device connected"
        result.emit(result.message, progress)
        return result

    blockers = validate_device_for_supervise(
        find_my_enabled=info.find_my_enabled,
        activation_state=info.activation_state,
        paired=info.paired,
    )
    if blockers:
        result.message = blockers[0]
        for b in blockers:
            result.emit(b, progress)
        return result

    result.emit(
        f"Device: {info.name}  iOS {info.product_version}  UDID {info.udid}",
        progress,
    )
    if info.will_encrypt_backup:
        result.emit("Note: device has encrypted backups enabled.", progress)

    cfg_url = "" if remove else _resolve_config_url(config, info.udid, result, progress)
    result.enrollment_url = config.enrollment_url_for(info.udid) or cfg_url
    if not remove and not cfg_url:
        result.message = (
            config.require_access_key()
            or "ConfigurationURL missing — cannot supervise"
        )
        result.emit(result.message, progress)
        return result

    if remove:
        cloud = build_removal_configuration(organization_name=config.organization_name)
        result.emit("Built removal cloud-configuration (IsSupervised=false).", progress)
    else:
        cert_path = config.resolve_cert_path()
        if not cert_path.is_file():
            result.message = f"Supervisor cert not found: {cert_path}"
            result.emit(result.message, progress)
            return result
        cert_der = load_supervisor_cert_der(cert_path)
        cloud = build_cloud_configuration(
            supervisor_cert_der=cert_der,
            organization_name=config.organization_name,
            configuration_url=cfg_url,
            is_mdm_unremovable=config.is_mdm_unremovable,
        )
        result.emit(
            f"Built cloud configuration for org={config.organization_name!r} "
            f"url={cfg_url!r} mdm_unremovable={config.is_mdm_unremovable}",
            progress,
        )

    from pymobiledevice3.lockdown import create_using_usbmux

    lockdown = await create_using_usbmux(serial=info.udid, autopair=True)
    success = False
    try:
        success = await _restore_via_backup(
            lockdown, cloud, result, progress, reboot=True
        )
        if success and not remove:
            await _show_enrollment_url(config, result, progress)
            user_dict = None
            if selected_user is not None:
                user_dict = (
                    selected_user.to_dict()
                    if hasattr(selected_user, "to_dict")
                    else dict(selected_user)
                )
            elif config.access_key:
                user_dict = {
                    "access_key": config.access_key,
                    "name": "",
                    "email": "",
                    "user_id": "",
                }
            ok_post, post_msg = post_supervision_complete(
                config, info.to_dict(), user=user_dict
            )
            result.api_post_message = post_msg
            result.emit(post_msg, progress)
            if config.access_key and info.udid:
                _ok_a, msg_a = post_assign_device(
                    config,
                    udid=info.udid,
                    access_key=config.access_key,
                    user=user_dict,
                )
                result.emit(msg_a, progress)
    finally:
        try:
            await lockdown.close()
        except Exception:
            pass

    result.ok = success
    result.message = (
        ("Supervision applied (sparse backup restore; phone not erased)"
         if not remove
         else "Removal payload applied (sparse backup restore; phone not erased)")
        if success
        else "Supervision flow failed — see log"
    )
    result.emit(result.message, progress)
    return result


def supervise_device(
    config: Optional[AppConfig] = None,
    *,
    remove: bool = False,
    dry_run_dir: Optional[str | Path] = None,
    progress: Optional[ProgressCb] = None,
    udid: Optional[str] = None,
    selected_user: Optional[Any] = None,
) -> SuperviseResult:
    """Synchronous entry point for GUI / CLI."""
    config = config or load_config()
    dry = Path(dry_run_dir) if dry_run_dir else None
    try:
        return asyncio.run(
            _run_supervise_async(
                config,
                remove=remove,
                dry_run_dir=dry,
                progress=progress,
                udid=udid,
                selected_user=selected_user,
            )
        )
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _run_supervise_async(
                    config,
                    remove=remove,
                    dry_run_dir=dry,
                    progress=progress,
                    udid=udid,
                    selected_user=selected_user,
                )
            )
        finally:
            loop.close()
