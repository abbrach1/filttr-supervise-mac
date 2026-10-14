"""Build the CloudConfiguration / CloudConfigurationDetails.plist payload.

The structure mirrors a real ``Library/ConfigurationProfiles/
CloudConfigurationDetails.plist`` pulled from a supervised device, with Filttr
values. SkipSetup is the set of Apple Setup Assistant pane names and is copied
verbatim from that reference so the post-supervision experience matches.

This dictionary is written into a synthetic MobileBackup2 backup as
``SysSharedContainerDomain-systemgroup.com.apple.configurationprofiles/
Library/ConfigurationProfiles/CloudConfigurationDetails.plist`` and restored
over USB (never via MCInstall SetCloudConfiguration / erase).
"""
from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any, Optional

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

# Copied verbatim from the reference CloudConfigurationDetails.plist (78 entries).
# These are Apple Setup Assistant pane identifiers, not proprietary content.
SKIP_SETUP = [
    "WiFi", "Location", "Restore", "SIMSetup", "Android", "IntendedUser", "TOS",
    "Siri", "ScreenTime", "Diagnostics", "SoftwareUpdate", "Passcode", "Biometric",
    "Payment", "Zoom", "DisplayTone", "MessagingActivationUsingPhoneNumber",
    "HomeButtonSensitivity", "CloudStorage", "ScreenSaver", "TapToSetup", "Keyboard",
    "PreferredLanguage", "SpokenLanguage", "WatchMigration", "OnBoarding",
    "TVProviderSignIn", "TVHomeScreenSync", "Privacy", "TVRoom", "iMessageAndFaceTime",
    "AppStore", "Safety", "Multitasking", "ActionButton", "TermsOfAddress",
    "AccessibilityAppearance", "Welcome", "Appearance", "RestoreCompleted",
    "UpdateCompleted", "Display", "Tone", "LanguageAndLocale", "TouchID",
    "TrueToneDisplay", "FileVault", "iCloudStorage", "iCloudDiagnostics",
    "Registration", "DeviceToDeviceMigration", "UnlockWithWatch", "Accessibility",
    "ExpressLanguage", "Language", "N/A", "Region", "Avatar", "DeviceProtection",
    "Key", "LockdownMode", "Wallpaper", "PrivacySubtitle", "SecuritySubtitle",
    "DataSubtitle", "AppearanceSubtitle", "PreferredLang", "OnboardingSubtitle",
    "AppleTVSubtitle", "Intelligence", "WebContentFiltering", "CameraButton",
    "AdditionalPrivacySettings", "EnableLockdownMode", "OSShowcase",
    "SafetyAndHandling", "Tips", "AgeBasedSafetySettings",
]

# Top-level keys the reference file carries. Used by tests for structural parity.
REFERENCE_KEYS = {
    "AllowPairing",
    "AwaitDeviceConfigured",
    "CloudConfigurationUIComplete",
    "ConfigurationSource",
    "ConfigurationURL",
    "ConfigurationWasApplied",
    "IsMDMUnremovable",
    "IsMandatory",
    "IsSupervised",
    "OrganizationName",
    "PostSetupProfileWasInstalled",
    "SkipSetup",
    "SupervisorHostCertificates",
}

CLOUD_CONFIG_DOMAIN = (
    "SysSharedContainerDomain-systemgroup.com.apple.configurationprofiles"
)
CLOUD_CONFIG_RELATIVE_PATH = (
    "Library/ConfigurationProfiles/CloudConfigurationDetails.plist"
)


def load_supervisor_cert_der(cert_path: str | Path) -> bytes:
    """Load a supervisor certificate (PEM or DER) and return its DER bytes.

    Only the public certificate is ever read. The private key must never be
    passed here or bundled with the app.
    """
    cert_path = Path(cert_path)
    data = cert_path.read_bytes()
    try:
        cert = x509.load_pem_x509_certificate(data)
    except ValueError:
        cert = x509.load_der_x509_certificate(data)
    return cert.public_bytes(Encoding.DER)


def resolve_configuration_url(
    template: Optional[str],
    udid: Optional[str] = None,
    access_key: Optional[str] = None,
) -> str:
    """Expand ``{udid}`` / ``{access_key}`` in a URL template (or return as-is)."""
    if not template:
        return ""
    out = template
    if access_key is not None:
        out = out.replace("{access_key}", access_key)
    if udid is not None:
        out = out.replace("{udid}", udid)
    return out


def build_cloud_configuration(
    supervisor_cert_der: bytes,
    organization_name: str = "Filttr",
    configuration_url: Optional[str] = None,
    is_mdm_unremovable: bool = True,
    is_mandatory: bool = True,
    allow_pairing: bool = True,
    is_supervised: bool = True,
) -> dict[str, Any]:
    """Build the cloud configuration dict matching the reference key structure.

    :param supervisor_cert_der: DER bytes of the Filttr supervisor certificate.
    :param organization_name: organization recorded in the config (default 'Filttr').
    :param configuration_url: per-device enrollment/config URL from config or
        server lookup. Empty string keeps the key present for structure parity.
    :param is_mdm_unremovable: whether MDM is marked unremovable (configurable).
    :param is_mandatory: whether supervision is mandatory.
    :param allow_pairing: whether host pairing stays allowed.
    :param is_supervised: supervision flag (False for remove-supervision payload).
    """
    if not supervisor_cert_der and is_supervised:
        raise ValueError("supervisor_cert_der is required when supervising")

    config: dict[str, Any] = {
        "AllowPairing": bool(allow_pairing),
        "AwaitDeviceConfigured": False,
        "CloudConfigurationUIComplete": True,
        "ConfigurationSource": 2,
        "ConfigurationURL": configuration_url or "",
        "ConfigurationWasApplied": True,
        "IsMDMUnremovable": bool(is_mdm_unremovable),
        "IsMandatory": bool(is_mandatory),
        "IsSupervised": bool(is_supervised),
        "OrganizationName": organization_name,
        "PostSetupProfileWasInstalled": True,
        "SkipSetup": list(SKIP_SETUP),
        "SupervisorHostCertificates": (
            [supervisor_cert_der] if supervisor_cert_der else []
        ),
    }
    return config


def build_cloud_configuration_details_plist(config: dict[str, Any]) -> bytes:
    """Serialize the cloud configuration as a binary plist (device format)."""
    return plistlib.dumps(config, fmt=plistlib.FMT_BINARY)


def build_removal_configuration(
    organization_name: str = "Filttr",
) -> dict[str, Any]:
    """Build a cloud-configuration payload that clears supervision flags.

    Used by the remove-supervision path. Does not include a supervisor cert.
    Feasibility on a given iOS version must be verified on a real device.
    """
    return {
        "AllowPairing": True,
        "AwaitDeviceConfigured": False,
        "CloudConfigurationUIComplete": True,
        "ConfigurationSource": 0,
        "ConfigurationURL": "",
        "ConfigurationWasApplied": True,
        "IsMDMUnremovable": False,
        "IsMandatory": False,
        "IsSupervised": False,
        "OrganizationName": organization_name,
        "PostSetupProfileWasInstalled": False,
        "SkipSetup": list(SKIP_SETUP),
        "SupervisorHostCertificates": [],
    }
