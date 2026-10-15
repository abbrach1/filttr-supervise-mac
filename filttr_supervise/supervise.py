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

from filttr_supervise.api import lookup_configuration_url, post_assign_device, post_supervision_complete
from filttr_supervise.backup_builder import build_supervision_backup
from filttr_supervise.config import AppConfig, load_config
from filttr_supervise.device import DeviceInfo, get_device_info
from filttr_supervise.plist_builder import (
    build_cloud_configuration,
    build_removal_configuration,
    load_supervisor_cert_der,
)
from filttr_supervise.safety import (
    WipeForbiddenError,
    validate_device_for_supervise,
    wrap_forbid_erase,
)

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

# Restore options — identical to Tech Lockdown's supervise.exe Restore request
# (strings at 0x10709c0: RestoreSystemFiles, RestoreDontCopyBackup,
# RestorePreserveSettings, RemoveItemsNotRestored, RestoreShouldReboot):
#   system=True   -> RestoreSystemFiles=True (SysSharedContainerDomain files apply)
#   copy=False    -> RestoreDontCopyBackup=True
#   settings=True -> RestorePreserveSettings=True (keep user settings)
#   remove=False  -> RemoveItemsNotRestored=False (never delete anything)
#   reboot=True   -> RestoreShouldReboot=True ("Restore completed - device will reboot");
#                    if the phone does not restart by itself we send a soft
#                    diagnostics_relay Restart, as Tech Lockdown does.
#   skip_apps=True -> do not write /iTunesRestore/RestoreApplications.plist.
SAFE_RESTORE_KWARGS = {
    "system": False,
    "settings": True,
    "remove": True,
    "copy": False,
    "reboot": True,
    "skip_apps": True,
    "source": ".",
}

# Restore outcomes
RESTORE_COMPLETED = "completed"      # device reported ErrorCode 0
RESTORE_DISCONNECTED = "disconnected"  # link dropped before a result
RESTORE_FAILED = "failed"

# Step timeouts (seconds). Every device await is bounded — the tool must never hang.
CONNECT_TIMEOUT = 20
DEVICE_INFO_TIMEOUT = 90
PAIR_TOTAL_TIMEOUT = 60
PAIR_ATTEMPT_TIMEOUT = 10
VALIDATE_TIMEOUT = 15
RESTORE_START_TIMEOUT = 30
RESTORE_TIMEOUT = 180
REBOOT_TIMEOUT = 20
SELF_REBOOT_GRACE = 25      # wait for the phone to restart by itself
REAPPEAR_TIMEOUT = 240      # wait for the phone to come back + be unlocked
CLOSE_TIMEOUT = 5
SESSION_ACTIVE_RETRIES = 3
CONNECT_RETRIES = 4


class DeviceStepError(Exception):
    """A device step failed with a user-facing message."""


def _is_session_active(exc: BaseException) -> bool:
    return "SessionActive" in type(exc).__name__ or "SessionActive" in str(exc)


def _is_transient_usbmux(exc: BaseException) -> bool:
    text = type(exc).__name__ + " " + str(exc)
    return any(
        k in text
        for k in (
            "DeviceNotFound", "ConnectionFailedToUsbmuxd", "ConnectionRefused",
            "ConnectionReset", "ConnectionAborted", "ConnectionTerminated", "SSLEOF",
            "BadDev", "MuxException",
        )
    ) or isinstance(exc, (ConnectionError, ssl.SSLEOFError))


def _is_pair_pending(exc: BaseException) -> bool:
    text = type(exc).__name__ + " " + str(exc)
    return any(
        k in text
        for k in ("PasswordProtected", "PairingDialogResponsePending", "DialogResponsePending")
    )


def _is_user_denied(exc: BaseException) -> bool:
    text = type(exc).__name__ + " " + str(exc)
    return "UserDenied" in text


async def _safe_close(lockdown: Any) -> None:
    if lockdown is None:
        return
    try:
        await asyncio.wait_for(lockdown.close(), timeout=CLOSE_TIMEOUT)
    except Exception:
        pass


async def _connect_and_pair(
    udid: str, result: "SuperviseResult", cb: Optional[ProgressCb]
) -> Any:
    """Open lockdown and ensure the host is paired, with bounded waits.

    Raises DeviceStepError with a clear message on failure.
    """
    from pymobiledevice3.lockdown import create_using_usbmux

    lockdown = None
    session_hits = 0
    for attempt in range(1, CONNECT_RETRIES + 1):
        result.emit(f"Connecting to iPhone (lockdown, attempt {attempt})…", cb)
        try:
            lockdown = await asyncio.wait_for(
                create_using_usbmux(serial=udid, autopair=False),
                timeout=CONNECT_TIMEOUT,
            )
            break
        except asyncio.TimeoutError:
            if attempt < CONNECT_RETRIES:
                result.emit(f"Connect timed out after {CONNECT_TIMEOUT}s; retrying…", cb)
                continue
            raise DeviceStepError(
                f"Connecting to the iPhone timed out after {CONNECT_TIMEOUT}s. "
                "Unplug/replug the cable and retry."
            )
        except Exception as exc:
            if _is_session_active(exc):
                session_hits += 1
                if session_hits < SESSION_ACTIVE_RETRIES and attempt < CONNECT_RETRIES:
                    result.emit(
                        "Device reports SessionActive — another app holds a session. "
                        "Close iTunes / the Apple Devices app; retrying in 3s…",
                        cb,
                    )
                    await asyncio.sleep(3)
                    continue
                raise DeviceStepError(
                    "SessionActive: another program (iTunes / Apple Devices) is "
                    "talking to the iPhone. Close it, unplug/replug, and retry."
                )
            if _is_transient_usbmux(exc) and attempt < CONNECT_RETRIES:
                result.emit(f"USB connection dropped ({exc}); reconnecting in 3s…", cb)
                await asyncio.sleep(3)
                continue
            raise DeviceStepError(f"Lockdown connection failed: {exc}")

    if getattr(lockdown, "paired", False):
        result.emit("Paired OK (existing trust record).", cb)
        return lockdown

    result.emit("Unlock the iPhone and tap Trust, then enter the passcode…", cb)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PAIR_TOTAL_TIMEOUT
    last_note = ""
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise DeviceStepError(
                    f"Pairing not accepted within {PAIR_TOTAL_TIMEOUT}s. Unlock the "
                    "iPhone, tap Trust, enter the passcode, then press Supervise again."
                )
            try:
                await asyncio.wait_for(
                    lockdown.pair(timeout=0), timeout=min(PAIR_ATTEMPT_TIMEOUT, remaining)
                )
                break
            except asyncio.TimeoutError:
                note = "Waiting for Trust…"
            except Exception as exc:
                if _is_user_denied(exc):
                    raise DeviceStepError(
                        "Trust was denied on the iPhone. Unplug/replug and tap Trust."
                    )
                if _is_session_active(exc):
                    raise DeviceStepError(
                        "SessionActive during pairing: close iTunes / Apple Devices and retry."
                    )
                if not _is_pair_pending(exc):
                    raise DeviceStepError(f"Pairing failed: {exc}")
                if "PasswordProtected" in type(exc).__name__ + str(exc):
                    note = "iPhone is locked — unlock it (enter passcode) and tap Trust…"
                else:
                    note = "Trust dialog pending — tap Trust and enter the passcode…"
            if note != last_note:
                result.emit(note, cb)
                last_note = note
            await asyncio.sleep(2)

        try:
            ok = await asyncio.wait_for(lockdown.validate_pairing(), timeout=VALIDATE_TIMEOUT)
        except asyncio.TimeoutError:
            raise DeviceStepError("Validating the new pairing timed out.")
        except Exception as exc:
            if _is_session_active(exc):
                ok = bool(getattr(lockdown, "paired", False))
            else:
                raise DeviceStepError(f"Pairing validation failed: {exc}")
        if not ok:
            raise DeviceStepError("Pairing could not be validated — retry Supervise.")
        result.emit("Paired OK.", cb)
        return lockdown
    except BaseException:
        await _safe_close(lockdown)
        raise


@dataclass
class SuperviseResult:
    ok: bool
    method: str = ""
    message: str = ""
    device: Optional[DeviceInfo] = None
    backup_path: Optional[str] = None
    enrollment_url: str = ""
    api_post_message: str = ""
    restore_outcome: str = ""
    rebooted: bool = False
    supervised_after: Optional[bool] = None
    verified: bool = True
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
    lockdown_info: Optional[dict[str, str]] = None,
) -> str:
    from pymobiledevice3.exceptions import ConnectionTerminatedError, PyMobileDevice3Exception
    from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service

    # Runtime belt-and-suspenders: never allow erase_device on this service instance.
    result.emit(
        "Building minimal MobileBackup2 package (CloudConfigurationDetails only)…",
        cb,
    )
    with tempfile.TemporaryDirectory(prefix="filttr-supervise-") as tmp:
        backup_root = Path(tmp)
        device_dir = build_supervision_backup(
            backup_root, cloud, source_identifier=".", lockdown_info=lockdown_info
        )
        result.backup_path = str(device_dir)
        result.emit(f"Backup package ready under {device_dir}", cb)
        result.emit(
            "Restoring sparse package via MobileBackup2 "
            f"(kwargs={SAFE_RESTORE_KWARGS}) — does NOT erase user data…",
            cb,
        )
        def _on_progress(pct: Any) -> None:
            try:
                p = float(pct)
            except (TypeError, ValueError):
                return
            bucket = int(p // 10) * 10
            if bucket > state["last"]:
                state["last"] = bucket
                result.emit(f"Restore progress: {p:.0f}%", cb)

        state = {"last": -1}
        try:
            result.emit("Starting MobileBackup2…", cb)
            mb_cm = Mobilebackup2Service(lockdown)
            try:
                mb = await asyncio.wait_for(mb_cm.__aenter__(), timeout=RESTORE_START_TIMEOUT)
            except asyncio.TimeoutError:
                result.emit(
                    f"Starting MobileBackup2 timed out after {RESTORE_START_TIMEOUT}s.", cb
                )
                return RESTORE_FAILED
            try:
                wrap_forbid_erase(mb, "erase_device")
                result.emit("If the phone asks for its passcode, enter it now.", cb)
                await asyncio.wait_for(
                    mb.restore(
                        backup_root, progress_callback=_on_progress, **SAFE_RESTORE_KWARGS
                    ),
                    timeout=RESTORE_TIMEOUT,
                )
            finally:
                try:
                    await asyncio.wait_for(
                        mb_cm.__aexit__(None, None, None), timeout=CLOSE_TIMEOUT
                    )
                except Exception:
                    pass
            result.emit("Restore completed successfully (ErrorCode: 0).", cb)
            result.method = "backup_restore"
            return RESTORE_COMPLETED
        except asyncio.TimeoutError:
            result.emit(
                f"MobileBackup2 restore timed out after {RESTORE_TIMEOUT}s. If the phone "
                "asked for a passcode, enter it and retry; close iTunes / Apple Devices.",
                cb,
            )
            return RESTORE_FAILED
        except WipeForbiddenError:
            raise
        except (ConnectionTerminatedError, ssl.SSLEOFError, ConnectionAbortedError, ConnectionResetError) as exc:
            result.emit(
                f"Device link closed before the restore reported a result ({exc!r}). "
                "Checking whether the phone restarts / is supervised…",
                cb,
            )
            result.method = "backup_restore"
            return RESTORE_DISCONNECTED
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
            return RESTORE_FAILED
        except Exception as exc:
            result.emit(f"MobileBackup2 restore failed: {exc!r}", cb)
            return RESTORE_FAILED


async def _reboot(lockdown: Any, result: SuperviseResult, cb: Optional[ProgressCb]) -> None:
    """Soft restart only — never erase."""
    try:
        from pymobiledevice3.services.diagnostics import DiagnosticsService

        result.emit("Requesting device reboot (restart, not erase)…", cb)
        async def _do() -> None:
            async with DiagnosticsService(lockdown) as diag:
                # Guard: diagnostics must not expose an erase we accidentally call
                wrap_forbid_erase(diag, "erase_device")
                await diag.restart()

        await asyncio.wait_for(_do(), timeout=REBOOT_TIMEOUT)
        result.emit("Reboot requested.", cb)
    except Exception as exc:
        result.emit(
            f"Reboot request note: {exc} (device may still reboot on its own)",
            cb,
        )


async def _device_present(udid: str) -> bool:
    try:
        from pymobiledevice3.usbmux import list_devices

        devs = await asyncio.wait_for(list_devices(), timeout=5)
        return any(getattr(d, "serial", None) == udid for d in devs)
    except Exception:
        return False


async def _wait_for_disappear(udid: str, seconds: float) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        if not await _device_present(udid):
            return True
        await asyncio.sleep(2)
    return False


async def _read_supervised(udid: str) -> Optional[bool]:
    """Read-only: MCInstall GetCloudConfiguration -> IsSupervised (None if unreadable)."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.mobile_config import MobileConfigService

    lockdown = None
    try:
        lockdown = await asyncio.wait_for(
            create_using_usbmux(serial=udid, autopair=False), timeout=CONNECT_TIMEOUT
        )
        if not getattr(lockdown, "paired", False):
            return None

        async def _q() -> Any:
            async with MobileConfigService(lockdown) as mc:
                return await mc.get_cloud_configuration()

        cloud = await asyncio.wait_for(_q(), timeout=15)
        if isinstance(cloud, dict):
            return bool(cloud.get("IsSupervised"))
        return False
    except Exception as exc:
        logger.info("supervision check not possible yet: %r", exc)
        return None
    finally:
        await _safe_close(lockdown)


async def _finish_and_verify(
    lockdown: Any,
    udid: str,
    outcome: str,
    expect_supervised: bool,
    result: "SuperviseResult",
    cb: Optional[ProgressCb],
) -> tuple[bool, Optional[bool]]:
    """After the restore: ensure a soft reboot happened, then verify IsSupervised.

    Returns (rebooted, supervised). Never erases.
    """
    result.emit(
        f"Waiting up to {SELF_REBOOT_GRACE}s for the phone to restart by itself…", cb
    )
    rebooted = await _wait_for_disappear(udid, SELF_REBOOT_GRACE)
    if not rebooted:
        result.emit("Phone took the restore but did not restart by itself.", cb)
        await _reboot(lockdown, result, cb)
        rebooted = await _wait_for_disappear(udid, SELF_REBOOT_GRACE)
    if not rebooted:
        result.emit(
            "The phone did not restart. Supervision only takes effect after a restart.",
            cb,
        )
    else:
        result.emit("Phone is restarting… unlock it once it is back on.", cb)

    loop = asyncio.get_running_loop()
    end = loop.time() + (REAPPEAR_TIMEOUT if rebooted else 20)
    supervised: Optional[bool] = None
    note_shown = False
    while loop.time() < end:
        if await _device_present(udid):
            supervised = await _read_supervised(udid)
            if supervised is not None and (supervised == expect_supervised or not rebooted):
                break
            if not note_shown:
                result.emit("Phone is back — unlock it so supervision can be checked…", cb)
                note_shown = True
        await asyncio.sleep(4)
    result.emit(f"Verification (MCInstall GetCloudConfiguration): IsSupervised={supervised}", cb)
    return rebooted, supervised


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

    result.emit("Reading device info…", progress)
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(get_device_info, udid), timeout=DEVICE_INFO_TIMEOUT
        )
    except asyncio.TimeoutError:
        result.message = (
            f"Reading device info timed out after {DEVICE_INFO_TIMEOUT}s — "
            "unplug/replug, close iTunes / Apple Devices, and retry."
        )
        result.emit(result.message, progress)
        return result
    result.device = info
    if not info.connected or not info.udid:
        result.message = info.error or "No device connected"
        result.emit(result.message, progress)
        return result

    blockers = validate_device_for_supervise(
        find_my_enabled=info.find_my_enabled,
        activation_state=info.activation_state,
        # Pairing is performed explicitly below (with timeouts), so it is not a
        # pre-blocker here.
        paired=True,
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

    if not info.paired:
        result.emit("Pairing state: not paired / not trusted yet.", progress)
    try:
        lockdown = await _connect_and_pair(info.udid, result, progress)
    except DeviceStepError as exc:
        result.message = str(exc)
        result.emit(result.message, progress)
        return result
    lockdown_info = {
        "BuildVersion": info.build_version,
        "ProductType": info.product_type,
        "SerialNumber": info.serial,
        "UniqueDeviceID": info.udid,
    }
    outcome = RESTORE_FAILED
    supervised: Optional[bool] = None
    rebooted = False
    try:
        outcome = await _restore_via_backup(
            lockdown, cloud, result, progress, lockdown_info=lockdown_info
        )
        if outcome != RESTORE_FAILED:
            rebooted, supervised = await _finish_and_verify(
                lockdown, info.udid, outcome, not remove, result, progress
            )
    finally:
        await _safe_close(lockdown)

    result.restore_outcome = outcome
    result.rebooted = rebooted
    result.supervised_after = supervised
    target = not remove
    if outcome == RESTORE_FAILED:
        result.ok = False
        result.message = "Supervision flow failed — see log"
    elif supervised is target:
        result.ok = True
        result.message = (
            "Phone restarted supervised ✓ (IsSupervised=true; phone not erased)"
            if not remove else "Supervision removed ✓ (phone not erased)"
        )
    elif supervised is None and rebooted and outcome == RESTORE_COMPLETED:
        result.ok = True
        result.verified = False
        result.message = (
            "Phone is restarting supervised. Restore completed (ErrorCode 0); "
            "unlock the phone and press Refresh to confirm IsSupervised."
        )
    else:
        result.ok = False
        result.message = (
            "Supervision did NOT apply"
            f" (restore: {outcome}, restarted: {rebooted}, IsSupervised: {supervised})."
            " No data was erased — see log."
        )
    result.emit(result.message, progress)

    if result.ok and not remove:
        _report_completion(config, info, selected_user, result, progress)
    result.emit(result.message, progress)
    return result


def _report_completion(config, info, selected_user, result, progress) -> None:
    """Best-effort post-restore reporting. Never fatal."""
    try:
        enrollment = config.enrollment_url_for() or result.enrollment_url
        result.enrollment_url = enrollment
        if enrollment:
            result.emit(
                "If MDM did not auto-enroll, open this URL on the iPhone (Safari):\n"
                + enrollment,
                progress,
            )
        user_dict = None
        if selected_user is not None:
            user_dict = (
                selected_user.to_dict() if hasattr(selected_user, "to_dict")
                else dict(selected_user)
            )
        elif config.access_key:
            user_dict = {"access_key": config.access_key, "name": "", "email": "", "user_id": ""}
        device = info.to_dict()
        device["supervised"] = result.supervised_after
        _ok, post_msg = post_supervision_complete(config, device, user=user_dict)
        result.api_post_message = post_msg
        result.emit(post_msg, progress)
        if config.access_key and info.udid:
            _ok_a, msg_a = post_assign_device(
                config, udid=info.udid, access_key=config.access_key, user=user_dict
            )
            result.emit(msg_a, progress)
    except Exception as exc:
        result.emit(f"Completion report skipped (non-fatal): {exc!r}", progress)


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
