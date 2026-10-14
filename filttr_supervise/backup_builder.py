"""Build a minimal MobileBackup2 package containing only CloudConfigurationDetails.

Layout mirrors Tech Lockdown's supervise.exe sparse-restore template exactly
(verified by static analysis of its embedded backup assets):

* ``Manifest.db`` (SQLite, ``Files``/``Properties`` tables) whose ``file`` column is
  an NSKeyedArchiver ``MBFile`` blob — required on modern iOS.
* ``Manifest.mbdb`` (legacy index) kept alongside, as Tech Lockdown also ships one.
* ``Manifest.plist``: BackupKeyBag, Date, IsEncrypted=false, Lockdown dict with the
  connected device's BuildVersion/ProductType/SerialNumber/UniqueDeviceID,
  SystemDomainsVersion=24.0, Version=10.0, WasPasscodeSet=true.
* ``Status.plist``: BackupState=new, IsFullBackup=false, SnapshotState=finished,
  Version=3.2.
* ``Info.plist``: ``{"Applications": {}}``.
* Content at ``<id[:2]>/<id>`` (Manifest.db layout) and ``<id>`` (mbdb layout),
  id = SHA-1(domain + "-" + relative_path).

Four entries only: the configurationprofiles domain root, Library,
Library/ConfigurationProfiles and CloudConfigurationDetails.plist. No erase.
"""
from __future__ import annotations

import base64
import hashlib
import plistlib
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from filttr_supervise import mbdb
from filttr_supervise.plist_builder import (
    CLOUD_CONFIG_DOMAIN,
    CLOUD_CONFIG_RELATIVE_PATH,
    build_cloud_configuration_details_plist,
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

MANIFEST_VERSION = "10.0"
SYSTEM_DOMAINS_VERSION = "24.0"
STATUS_VERSION = "3.2"

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


def mbfile_archive(entry: BackupEntry, ts: Optional[int] = None) -> bytes:
    """NSKeyedArchiver MBFile blob for Manifest.db (same keys as Tech Lockdown)."""
    ts = _now_ts() if ts is None else ts
    obj = {
        "LastModified": ts,
        "Flags": 0,
        "GroupID": entry.group,
        "$class": plistlib.UID(3),
        "LastStatusChange": ts,
        "RelativePath": plistlib.UID(2),
        "Birth": ts,
        "Size": 0 if entry.is_dir else len(entry.contents or b""),
        "InodeNumber": entry.inode,
        "Mode": entry.effective_mode(),
        "UserID": entry.owner,
        "ProtectionClass": entry.protection_class,
    }
    archive = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            obj,
            entry.relative_path,
            {"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]},
        ],
    }
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


def build_cloud_config_entries(config: dict[str, Any]) -> list[BackupEntry]:
    """Entries matching Tech Lockdown's Manifest.db rows (owner/mode/protection)."""
    plist_bytes = build_cloud_configuration_details_plist(config)
    d = CLOUD_CONFIG_DOMAIN
    return [
        BackupEntry(d, "", owner=0, group=0, mode=0o40755, protection_class=0, inode=266),
        BackupEntry(d, "Library", owner=-2, group=-2, mode=0o40000, protection_class=0, inode=268),
        BackupEntry(d, "Library/ConfigurationProfiles", owner=-2, group=-2, mode=0o40000,
                    protection_class=4, inode=343),
        BackupEntry(d, CLOUD_CONFIG_RELATIVE_PATH, contents=plist_bytes, owner=501, group=-2,
                    mode=0o100644, protection_class=4, inode=51802),
    ]


def _write_manifest_db(path: Path, entries: list[BackupEntry]) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    try:
        con.executescript(
            "CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, "
            "flags INTEGER, file BLOB);"
            "CREATE INDEX FilesDomainIdx ON Files(domain);"
            "CREATE INDEX FilesRelativePathIdx ON Files(relativePath);"
            "CREATE INDEX FilesFlagsIdx ON Files(flags);"
            "CREATE TABLE Properties (key TEXT PRIMARY KEY, value BLOB);"
        )
        ts = _now_ts()
        for e in entries:
            con.execute(
                "INSERT INTO Files VALUES (?,?,?,?,?)",
                (file_id_for(e.domain, e.relative_path), e.domain, e.relative_path,
                 2 if e.is_dir else 1, mbfile_archive(e, ts)),
            )
        con.commit()
    finally:
        con.close()


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
        (device_dir / fid[:2]).mkdir(exist_ok=True)
        (device_dir / fid[:2] / fid).write_bytes(entry.contents)
        (device_dir / fid).write_bytes(entry.contents)

    _write_manifest_db(device_dir / "Manifest.db", entries)
    (device_dir / "Manifest.mbdb").write_bytes(
        mbdb.Mbdb(records=[_entry_to_record(e) for e in entries]).to_bytes()
    )

    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    status = {
        "BackupState": "new",
        "Date": now,
        "IsFullBackup": False,
        "SnapshotState": "finished",
        "UUID": str(uuid.uuid4()).upper(),
        "Version": STATUS_VERSION,
    }
    (device_dir / "Status.plist").write_bytes(plistlib.dumps(status, fmt=plistlib.FMT_BINARY))

    li = lockdown_info or {}
    manifest = {
        "BackupKeyBag": base64.b64decode(BACKUP_KEYBAG_B64),
        "Date": now,
        "IsEncrypted": False,
        "Lockdown": {
            "BuildVersion": li.get("BuildVersion", ""),
            "ProductType": li.get("ProductType", ""),
            "SerialNumber": li.get("SerialNumber", ""),
            "UniqueDeviceID": li.get("UniqueDeviceID", ""),
        },
        "SystemDomainsVersion": SYSTEM_DOMAINS_VERSION,
        "Version": MANIFEST_VERSION,
        "WasPasscodeSet": True,
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
        build_cloud_config_entries(config),
        source_identifier=source_identifier,
        lockdown_info=lockdown_info,
    )
