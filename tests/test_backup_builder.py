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
