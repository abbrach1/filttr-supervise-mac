"""Tests for the minimal MobileBackup2 package builder."""
from __future__ import annotations

import plistlib
from pathlib import Path

from filttr_supervise.backup_builder import (
    build_supervision_backup,
    file_id_for,
)
from filttr_supervise.plist_builder import (
    CLOUD_CONFIG_DOMAIN,
    CLOUD_CONFIG_RELATIVE_PATH,
    build_cloud_configuration,
    load_supervisor_cert_der,
)

ROOT = Path(__file__).resolve().parents[1]


def test_dry_run_backup_layout(tmp_path: Path) -> None:
    cert = load_supervisor_cert_der(ROOT / "assets" / "filttr-supervisor.der")
    cfg = build_cloud_configuration(cert, configuration_url="https://example/enroll")
    device_dir = build_supervision_backup(tmp_path, cfg, source_identifier="TESTUDID")

    assert device_dir == tmp_path / "TESTUDID"
    for name in ("Info.plist", "Manifest.plist", "Status.plist", "Manifest.mbdb"):
        assert (device_dir / name).is_file(), name

    fid = file_id_for(CLOUD_CONFIG_DOMAIN, CLOUD_CONFIG_RELATIVE_PATH)
    content_path = device_dir / fid
    assert content_path.is_file()
    loaded = plistlib.loads(content_path.read_bytes())
    assert loaded["OrganizationName"] == "Filttr"
    assert loaded["IsSupervised"] is True

    mbdb = (device_dir / "Manifest.mbdb").read_bytes()
    assert mbdb.startswith(b"mbdb\x05\x00")
    assert CLOUD_CONFIG_DOMAIN.encode() in mbdb

    status = plistlib.loads((device_dir / "Status.plist").read_bytes())
    assert status["SnapshotState"] == "finished"


def test_dot_source_writes_into_root(tmp_path: Path) -> None:
    cert = load_supervisor_cert_der(ROOT / "assets" / "filttr-supervisor.der")
    cfg = build_cloud_configuration(cert)
    device_dir = build_supervision_backup(tmp_path, cfg, source_identifier=".")
    assert device_dir == tmp_path
    assert (tmp_path / "Manifest.mbdb").is_file()


def test_manifest_db_matches_tech_lockdown_layout(tmp_path: Path) -> None:
    import sqlite3

    cert = load_supervisor_cert_der(ROOT / "assets" / "filttr-supervisor.der")
    cfg = build_cloud_configuration(cert, configuration_url="https://example/enroll")
    info = {"BuildVersion": "24A1", "ProductType": "iPhone14,5",
            "SerialNumber": "SN1", "UniqueDeviceID": "UDID1"}
    d = build_supervision_backup(tmp_path, cfg, source_identifier=".", lockdown_info=info)
    fid = file_id_for(CLOUD_CONFIG_DOMAIN, CLOUD_CONFIG_RELATIVE_PATH)
    assert fid == "1e6c0783f9b33d00b152067a0661c8fc8841073f"
    assert (d / fid[:2] / fid).is_file()
    con = sqlite3.connect(d / "Manifest.db")
    rows = {r[2]: r for r in con.execute("select * from Files")}
    assert set(rows) == {"", "Library", "Library/ConfigurationProfiles", CLOUD_CONFIG_RELATIVE_PATH}
    assert rows[CLOUD_CONFIG_RELATIVE_PATH][3] == 1 and rows["Library"][3] == 2
    mb = plistlib.loads(rows[CLOUD_CONFIG_RELATIVE_PATH][4])
    obj = mb["$objects"][1]
    assert mb["$objects"][3]["$classname"] == "MBFile"
    assert (obj["Mode"], obj["UserID"], obj["GroupID"], obj["ProtectionClass"]) == (33188, 501, -2, 4)
    assert obj["Size"] == len((d / fid).read_bytes())
    m = plistlib.loads((d / "Manifest.plist").read_bytes())
    assert m["Version"] == "10.0" and m["SystemDomainsVersion"] == "24.0"
    assert m["WasPasscodeSet"] is True and m["IsEncrypted"] is False
    assert m["Lockdown"]["UniqueDeviceID"] == "UDID1"
    assert m["BackupKeyBag"].startswith(b"VERS")
    st = plistlib.loads((d / "Status.plist").read_bytes())
    assert st["Version"] == "3.2" and st["BackupState"] == "new" and st["IsFullBackup"] is False
    assert plistlib.loads((d / "Info.plist").read_bytes()) == {"Applications": {}}
