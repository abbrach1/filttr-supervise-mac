"""Build a minimal MobileBackup2 package containing only CloudConfigurationDetails.

The package is restored over USB with ``Mobilebackup2Service.restore`` so the
device picks up supervision without a full wipe. Layout matches what open-source
MobileBackup2 clients expect: Info/Status/Manifest plists, Manifest.mbdb, and
content files named by SHA-1(domain + "-" + relative_path).
"""
from __future__ import annotations

import hashlib
import plistlib
import time
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


def _now_ts() -> int:
    return int(time.time())


def _entry_to_record(entry: BackupEntry) -> mbdb.MbdbRecord:
    ts = _now_ts()
    if entry.contents is None:
        return mbdb.MbdbRecord(
            domain=entry.domain,
            filename=entry.relative_path,
            mode=entry.mode if entry.mode is not None else mbdb.DEFAULT_DIR_MODE,
            inode=0,
            user_id=entry.owner,
            group_id=entry.group,
            mtime=ts,
            atime=ts,
            ctime=ts,
            size=0,
            flags=4,
        )
    digest = hashlib.sha1(entry.contents).digest()
    return mbdb.MbdbRecord(
        domain=entry.domain,
        filename=entry.relative_path,
        hash=digest,
        mode=entry.mode if entry.mode is not None else mbdb.DEFAULT_FILE_MODE,
        inode=int.from_bytes(digest[:8], "big"),
        user_id=entry.owner,
        group_id=entry.group,
        mtime=ts,
        atime=ts,
        ctime=ts,
        size=len(entry.contents),
        flags=4,
    )


def build_cloud_config_entries(config: dict[str, Any]) -> list[BackupEntry]:
    """Entries for the CloudConfigurationDetails.plist and its parent dirs."""
    plist_bytes = build_cloud_configuration_details_plist(config)
    domain = CLOUD_CONFIG_DOMAIN
    return [
        BackupEntry(domain=domain, relative_path="", owner=0, group=0),
        BackupEntry(domain=domain, relative_path="Library", owner=0, group=0),
        BackupEntry(
            domain=domain,
            relative_path="Library/ConfigurationProfiles",
            owner=0,
            group=0,
        ),
        BackupEntry(
            domain=domain,
            relative_path=CLOUD_CONFIG_RELATIVE_PATH,
            contents=plist_bytes,
            owner=0,
            group=0,
        ),
    ]


def write_backup_package(
    output_dir: Path,
    entries: list[BackupEntry],
    *,
    source_identifier: str = ".",
) -> Path:
    """Write a restoreable MobileBackup2 folder.

    :param output_dir: parent directory (a ``source_identifier`` subfolder is used).
    :param entries: files/dirs to include.
    :param source_identifier: subdirectory name; use ``"."`` to write directly
        into ``output_dir`` (matches pymobiledevice3 ``restore(..., source='.')``).
    :returns: path to the device backup folder that contains Manifest/Status/Info.
    """
    output_dir = Path(output_dir)
    if source_identifier in ("", "."):
        device_dir = output_dir
    else:
        device_dir = output_dir / source_identifier
    device_dir.mkdir(parents=True, exist_ok=True)

    records: list[mbdb.MbdbRecord] = []
    for entry in entries:
        records.append(_entry_to_record(entry))
        if entry.contents is not None:
            dest = device_dir / file_id_for(entry.domain, entry.relative_path)
            dest.write_bytes(entry.contents)

    (device_dir / "Manifest.mbdb").write_bytes(mbdb.Mbdb(records=records).to_bytes())

    status = {
        "BackupState": "new",
        "Date": datetime(1970, 1, 1, tzinfo=timezone.utc),
        "IsFullBackup": False,
        "SnapshotState": "finished",
        "UUID": "00000000-0000-0000-0000-000000000000",
        "Version": "2.4",
    }
    (device_dir / "Status.plist").write_bytes(plistlib.dumps(status))

    manifest = {
        "BackupKeyBag": b"",
        "IsEncrypted": False,
        "Lockdown": {},
        "SystemDomainsVersion": "20.0",
        "Version": "9.1",
        "WasPasscodeSet": False,
    }
    (device_dir / "Manifest.plist").write_bytes(plistlib.dumps(manifest))
    (device_dir / "Info.plist").write_bytes(plistlib.dumps({}))

    return device_dir


def build_supervision_backup(
    output_dir: Path,
    config: dict[str, Any],
    *,
    source_identifier: str = ".",
) -> Path:
    """Convenience: build a backup that only restores CloudConfigurationDetails."""
    return write_backup_package(
        output_dir,
        build_cloud_config_entries(config),
        source_identifier=source_identifier,
    )
