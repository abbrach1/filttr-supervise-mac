"""Build a minimal MobileBackup2 package containing only CloudConfigurationDetails.

Layout mirrors Tech Lockdown's supervise.exe sparse-restore template exactly
(verified by hex-dumping TL's actual Manifest.mbdb and Manifest.plist):

* ``Manifest.mbdb``: 14 entries — 8 directory + 6 file entries, all inode=0,
  uid=501, gid=501, flags=4.
* ``Manifest.plist``: only BackupKeyBag, Lockdown={}, SystemDomainsVersion=20.0,
  Version=9.1.  No Date/IsEncrypted/WasPasscodeSet.
* ``Status.plist``: Version=2.4, Date=epoch, UUID=zeros, BackupState=new,
  IsFullBackup=false, SnapshotState=finished.
* ``Info.plist``: ``{"Applications": {}}``.
* No Manifest.db (TL has none).
* Content at ``<id>`` (mbdb layout), id = SHA-1(domain + "-" + relative_path).
"""
from __future__ import annotations

import base64
import hashlib
import plistlib
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from filttr_supervise import mbdb
from filttr_supervise.plist_builder import (
    CLOUD_CONFIG_DOMAIN,
    CLOUD_CONFIG_RELATIVE_PATH,
    CLOUD_CONFIG_SET_ASIDE_RELATIVE_PATH,
    HOME_DOMAIN,
    MANAGED_PREFS_DOMAIN,
    MANAGED_PURPLEBUDDY_RELATIVE_PATH,
    MC_META_RELATIVE_PATH,
    PURPLEBUDDY_RELATIVE_PATH,
    SETUP_ASSISTANT_PRIVACY_RELATIVE_PATH,
    build_cloud_configuration_details_plist,
    build_mc_meta_plist,
    build_purplebuddy_plist,
    build_setup_assistant_privacy_plist,
)

# Generic unencrypted-backup keybag, identical to the one in Tech Lockdown's
# Manifest.plist template (supervise.exe offset ~0x1088a90).
BACKUP_KEYBAG_B64 = (
    "VkVSUwAAAAQAAAAFVFlQRQAAAAQAAAABVVVJRAAAABCYq0VGyj5N+J1lTCnzL81eSE1DSwAAACgA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAV1JBUAAAAAQAAAAAU0FMVAAA"
    "ABSs6K9B5rlhtxvQ38+9KCIxP43I1UlURVIAAAAEAAAnEFVVSUQAAAAQ8rjmhXLnTGalRlTZxYbV"
    "HkNMQVMAAAAEAAAAC1dSQVAAAAAEAAAAA0tUWVAAAAAEAAAAAFdQS1kAAAAoeCWZBCd7EClTJ4Mz"
    "gx75G+ydRzLT9fLb+ln8soTpI/nN2ecTsI3661VVSUQAAAAQnQgPBaKBRCO2GfLLTOU/qENMQVMA"
    "AAAEAAAACldSQVAAAAAEAAAAA0tUWVAAAAAEAAAAAFdQS1kAAAAosX7N+7om3YJF2JyY72iJUdja"
    "41CtrMYU8knh//E735QpDYtz51D4E1VVSUQAAAAQOVrU20tHSqSsKxKE8dAHRkNMQVMAAAAEAAAA"
    "CVdSQVAAAAAEAAAAA0tUWVAAAAAEAAAAAFdQS1kAAAAoElMoFFwwAvmS0dK/co1kGe1Zaxs4cs0m"
    "+YnsYCXFIScdTk0xzh24hFVVSUQAAAAQ6M38AOjOSwWiSEm7E+QohUNMQVMAAAAEAAAACFdSQVAA"
    "AAAEAAAAA0tUWVAAAAAEAAAAAFdQS1kAAAAoLobsw+Se8ERz7Mv9j7NOFVM1WIGW0hWjzur+waVD"
    "x8SmAoLh2RU6RlVVSUQAAAAQuomG2eQbTu+D49w8KJsVQ0NMQVMAAAAEAAAAB1dSQVAAAAAEAAAA"
    "A0tUWVAAAAAEAAAAAFdQS1kAAAAoRPPNOf6ixZkdshuRY+Hm39WM16PBEA3AWy7ZtM9x0T1G1tDK"
    "xDi3t1VVSUQAAAAQakWMLPXMTGqC5E+xTyTVYUNMQVMAAAAEAAAABldSQVAAAAAEAAAAA0tUWVAA"
    "AAAEAAAAAFdQS1kAAAAoOMwr3SL9K92NqpGuDulV/eLUSCvc/rxO8pqdDaEZIiOoTlt2TTc81FVV"
    "SUQAAAAQV929ZrXYR3e0eY7LL859OUNMQVMAAAAEAAAABVdSQVAAAAAEAAAAA0tUWVAAAAAEAAAA"
    "AFdQS1kAAAAohu7fpUBLBsGgK4MDHYI86zwfbuvOWY16RRq6pf+5o5VYTqCVoRx+xVVVSUQAAAAQ"
    "G+nRZjDpQiqQ7wID2i3SXkNMQVMAAAAEAAAABFdSQVAAAAAEAAAAAktUWVAAAAAEAAAAAFdQS1kA"
    "AAAoOJETsNaK3IA+zIxtDl+8nRB9Fi5DmZNxEG55mwqV2CcnHxTyC1i6cFVVSUQAAAAQ9UQvw49f"
    "TBOxOfg03lSGmUNMQVMAAAAEAAAAA1dSQVAAAAAEAAAAAktUWVAAAAAEAAAAAFdQS1kAAAAoGxwQ"
    "OTkd1oUoTxQBEa86y9t0L9SQ15QxbZoALQwddnHUXDOxrEG4hFVVSUQAAAAQwuLIXO/gTAqTpI7U"
    "5A0XV0NMQVMAAAAEAAAAAldSQVAAAAAEAAAAAktUWVAAAAAEAAAAAFdQS1kAAAAoFhUMF5jI9ILh"
    "gELI5ONo/8xAe03dfB5Y9M5yFM6NGNqpl136Bk04GFVVSUQAAAAQy+0CmcngRf2EVb2TTEUogUNM"
    "QVMAAAAEAAAAAVdSQVAAAAAEAAAAAktUWVAAAAAEAAAAAFdQS1kAAAAo2zByZ/juFGa5aePVV8Np"
    "Un6tTUd2TYCH3aLjCtlXS/L67IEmKzmJUQ=="
)

# Verified against TL's actual backup (hex dump + plistlib.load)
MANIFEST_VERSION = "9.1"
SYSTEM_DOMAINS_VERSION = "20.0"
STATUS_VERSION = "2.4"

S_IFDIR = 0o040000
S_IFREG = 0o100000


def file_id_for(domain: str, relative_path: str) -> str:
    """SHA-1 hex digest used as the on-disk content filename in a backup."""
    return hashlib.sha1(f"{domain}-{relative_path}".encode("utf-8")).hexdigest()


@dataclass
class BackupEntry:
    domain: str
    relative_path: str
    contents: Optional[bytes] = None  # None => directory
    owner: int = 0
    group: int = 0
    mode: Optional[int] = None
    protection_class: int = 0
    inode: int = 0

    @property
    def is_dir(self) -> bool:
        return self.contents is None

    def effective_mode(self) -> int:
        if self.mode is not None:
            return self.mode
        return (S_IFDIR | 0o755) if self.is_dir else (S_IFREG | 0o644)


def _now_ts() -> int:
    return int(time.time())


def _entry_to_record(entry: BackupEntry) -> mbdb.MbdbRecord:
    ts = _now_ts()
    size = 0 if entry.is_dir else len(entry.contents or b"")
    digest = b"" if entry.is_dir else hashlib.sha1(entry.contents or b"").digest()
    return mbdb.MbdbRecord(
        domain=entry.domain,
        filename=entry.relative_path,
        hash=digest,
        mode=entry.effective_mode(),
        inode=entry.inode,
        user_id=entry.owner & 0xFFFFFFFF,
        group_id=entry.group & 0xFFFFFFFF,
        mtime=ts,
        atime=ts,
        ctime=ts,
        size=size,
        flags=entry.protection_class,
    )


def build_cloud_config_entries(
    config: dict[str, Any],
    lockdown_info: Optional[dict] = None,
) -> list[BackupEntry]:
    """Entries matching Tech Lockdown's Manifest.mbdb rows exactly.

    Directory entries are required — TL's backup has 8 of them.
    All entries: inode=0, uid=501, gid=501 (mobile), flags=4.
    HomeDomain and ManagedPreferencesDomain files: mode=0o100777 (TL's value).
    SysSharedContainerDomain files: mode=0o100644.
    """
    plist_bytes = build_cloud_configuration_details_plist(config)
    d = CLOUD_CONFIG_DOMAIN
    h = HOME_DOMAIN
    m = MANAGED_PREFS_DOMAIN

    bv = (lockdown_info or {}).get("BuildVersion", "")
    pb_home = build_purplebuddy_plist(extended=True)
    pb_managed = build_purplebuddy_plist(extended=False)
    sp = build_setup_assistant_privacy_plist()

    # Directory entries (mode=0o40755), then file entries in each domain.
    # Order matches TL's Manifest.mbdb entry order.
    dirs_d = [
        BackupEntry(d, "Library", owner=501, group=501, mode=0o40755, protection_class=4, inode=0),
        BackupEntry(d, "Library/Caches", owner=501, group=501, mode=0o40755, protection_class=4, inode=0),
        BackupEntry(d, "Library/ConfigurationProfiles", owner=501, group=501, mode=0o40755, protection_class=4, inode=0),
        BackupEntry(d, "Library/ConfigurationProfiles/PublicInfo", owner=501, group=501, mode=0o40755, protection_class=4, inode=0),
        BackupEntry(d, "Library/Preferences", owner=501, group=501, mode=0o40755, protection_class=4, inode=0),
    ]
    dirs_h = [
        BackupEntry(h, "Library", owner=501, group=501, mode=0o40755, protection_class=4, inode=0),
        BackupEntry(h, "Library/Preferences", owner=501, group=501, mode=0o40755, protection_class=4, inode=0),
    ]
    dirs_m = [
        BackupEntry(m, "mobile", owner=501, group=501, mode=0o40755, protection_class=4, inode=0),
    ]
    return (
        dirs_d
        + [
            BackupEntry(d, CLOUD_CONFIG_RELATIVE_PATH, contents=plist_bytes,
                        owner=501, group=501, mode=0o100644, protection_class=4, inode=0),
            BackupEntry(d, CLOUD_CONFIG_SET_ASIDE_RELATIVE_PATH, contents=plist_bytes,
                        owner=501, group=501, mode=0o100644, protection_class=4, inode=0),
            BackupEntry(d, MC_META_RELATIVE_PATH, contents=build_mc_meta_plist(bv),
                        owner=501, group=501, mode=0o100644, protection_class=4, inode=0),
        ]
        + dirs_h
        + [
            BackupEntry(h, PURPLEBUDDY_RELATIVE_PATH, contents=pb_home,
                        owner=501, group=501, mode=0o100777, protection_class=4, inode=0),
            BackupEntry(h, SETUP_ASSISTANT_PRIVACY_RELATIVE_PATH, contents=sp,
                        owner=501, group=501, mode=0o100777, protection_class=4, inode=0),
        ]
        + dirs_m
        + [
            BackupEntry(m, MANAGED_PURPLEBUDDY_RELATIVE_PATH, contents=pb_managed,
                        owner=501, group=501, mode=0o100777, protection_class=4, inode=0),
        ]
    )


def write_backup_package(
    output_dir: Path,
    entries: list[BackupEntry],
    *,
    source_identifier: str = ".",
    lockdown_info: Optional[dict[str, str]] = None,
) -> Path:
    """Write a restoreable MobileBackup2 folder; returns the device backup folder."""
    output_dir = Path(output_dir)
    device_dir = output_dir if source_identifier in ("", ".") else output_dir / source_identifier
    device_dir.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        if entry.contents is None:
            continue
        fid = file_id_for(entry.domain, entry.relative_path)
        # Flat mbdb layout — no Manifest.db, no subdir needed.
        (device_dir / fid).write_bytes(entry.contents)

    (device_dir / "Manifest.mbdb").write_bytes(
        mbdb.Mbdb(records=[_entry_to_record(e) for e in entries]).to_bytes()
    )

    # Status.plist: match TL exactly — epoch date, zero UUID.
    status = {
        "BackupState": "new",
        "Date": datetime(1970, 1, 1, 0, 0),
        "IsFullBackup": False,
        "SnapshotState": "finished",
        "UUID": "00000000-0000-0000-0000-000000000000",
        "Version": STATUS_VERSION,
    }
    (device_dir / "Status.plist").write_bytes(plistlib.dumps(status, fmt=plistlib.FMT_BINARY))

    # Manifest.plist: only BackupKeyBag + Lockdown={} + versions (TL has no Date/IsEncrypted/WasPasscodeSet).
    manifest = {
        "BackupKeyBag": base64.b64decode(BACKUP_KEYBAG_B64),
        "Lockdown": {},
        "SystemDomainsVersion": SYSTEM_DOMAINS_VERSION,
        "Version": MANIFEST_VERSION,
    }
    (device_dir / "Manifest.plist").write_bytes(plistlib.dumps(manifest))
    (device_dir / "Info.plist").write_bytes(plistlib.dumps({"Applications": {}}))
    return device_dir


def build_supervision_backup(
    output_dir: Path,
    config: dict[str, Any],
    *,
    source_identifier: str = ".",
    lockdown_info: Optional[dict[str, str]] = None,
) -> Path:
    """Convenience: build a backup that only restores CloudConfigurationDetails."""
    return write_backup_package(
        output_dir,
        build_cloud_config_entries(config, lockdown_info=lockdown_info),
        source_identifier=source_identifier,
        lockdown_info=lockdown_info,
    )
