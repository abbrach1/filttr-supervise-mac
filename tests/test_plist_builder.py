"""Unit tests for cloud-configuration structure vs the reference key set."""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from filttr_supervise.plist_builder import (
    REFERENCE_KEYS,
    SKIP_SETUP,
    build_cloud_configuration,
    build_cloud_configuration_details_plist,
    build_removal_configuration,
    load_supervisor_cert_der,
    resolve_configuration_url,
)

ROOT = Path(__file__).resolve().parents[1]
CERT_DER = ROOT / "assets" / "filttr-supervisor.der"
CERT_CRT = ROOT / "assets" / "filttr-supervisor.crt"
REFERENCE = Path(
    "/home/box/agent-data/agents/1141a5dd-d44b-465a-ab7b-b4c2b8b83d4a/attachments/"
    "0b54af614d64885bfb0c7a6c36620d0032dddaebb8cca21931639ee9b7721bf8.plist"
)


@pytest.fixture(scope="module")
def cert_der() -> bytes:
    assert CERT_DER.is_file(), "assets/filttr-supervisor.der missing"
    return load_supervisor_cert_der(CERT_DER)


def test_load_cert_from_pem_and_der_match(cert_der: bytes) -> None:
    from_pem = load_supervisor_cert_der(CERT_CRT)
    assert from_pem == cert_der
    assert len(cert_der) > 100


def test_keys_match_reference(cert_der: bytes) -> None:
    cfg = build_cloud_configuration(
        cert_der,
        organization_name="Filttr",
        configuration_url="https://example.test/enroll",
        is_mdm_unremovable=True,
    )
    assert set(cfg.keys()) == REFERENCE_KEYS

    if REFERENCE.is_file():
        with open(REFERENCE, "rb") as fh:
            ref = plistlib.load(fh)
        assert set(ref.keys()) == REFERENCE_KEYS
        assert list(cfg["SkipSetup"]) == list(ref["SkipSetup"])


def test_filttr_values(cert_der: bytes) -> None:
    cfg = build_cloud_configuration(
        cert_der,
        organization_name="Filttr",
        configuration_url="https://filttr.org/enroll/demo",
        is_mdm_unremovable=True,
    )
    assert cfg["OrganizationName"] == "Filttr"
    assert cfg["IsSupervised"] is True
    assert cfg["IsMandatory"] is True
    assert cfg["IsMDMUnremovable"] is True
    assert cfg["AllowPairing"] is True
    assert cfg["AwaitDeviceConfigured"] is False
    assert cfg["CloudConfigurationUIComplete"] is True
    assert cfg["ConfigurationWasApplied"] is True
    assert cfg["PostSetupProfileWasInstalled"] is True
    assert cfg["ConfigurationSource"] == 2
    assert cfg["ConfigurationURL"] == "https://filttr.org/enroll/demo"
    assert cfg["SkipSetup"] == SKIP_SETUP
    assert len(SKIP_SETUP) == 78
    assert cfg["SupervisorHostCertificates"] == [cert_der]


def test_binary_plist_roundtrip(cert_der: bytes) -> None:
    cfg = build_cloud_configuration(cert_der, configuration_url="x")
    raw = build_cloud_configuration_details_plist(cfg)
    loaded = plistlib.loads(raw)
    assert loaded["IsSupervised"] is True
    assert loaded["SupervisorHostCertificates"][0] == cert_der


def test_resolve_configuration_url_template() -> None:
    assert resolve_configuration_url("https://x/{udid}/y", udid="ABC") == "https://x/ABC/y"
    assert resolve_configuration_url("https://x/y", udid="ABC") == "https://x/y"
    assert resolve_configuration_url("", udid="ABC") == ""
    assert (
        resolve_configuration_url(
            "https://filttr.org/api/mdm-enroll?k={access_key}&cfg=1",
            access_key="portal123",
        )
        == "https://filttr.org/api/mdm-enroll?k=portal123&cfg=1"
    )


def test_removal_payload_keys() -> None:
    cfg = build_removal_configuration()
    assert cfg["IsSupervised"] is False
    assert set(cfg.keys()) == REFERENCE_KEYS
