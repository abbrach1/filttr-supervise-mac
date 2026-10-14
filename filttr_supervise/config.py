"""Load and validate Filttr Supervise config.json."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# USB / Configurator-style enroll uses cfg=1 (PayloadRemovalDisallowed).
# k= is the portal access key (Firestore portals/{k}).
DEFAULT_ENROLLMENT_URL_TEMPLATE = (
    "https://www.filttr.org/api/mdm-enroll?k={access_key}&cfg=1"
)

DEFAULT_CONFIG: dict[str, Any] = {
    "api_base": "https://www.filttr.org",
    "access_key": "",
    "enrollment_url": "",
    "enrollment_url_template": DEFAULT_ENROLLMENT_URL_TEMPLATE,
    "organization_name": "Filttr",
    "is_mdm_unremovable": True,
    "api_token": "",
    "tool_token": "",
    "complete_path": "/api/supervision/complete",
    "configuration_url": "",
    "configuration_url_template": "",
    "supervisor_cert": "assets/filttr-supervisor.der",
    "method": "backup_restore",
    "users_search_path": "/api/search-users",
    "assign_path": "/api/supervision/assign",
}


@dataclass
class AppConfig:
    api_base: str = "https://www.filttr.org"
    access_key: str = ""
    enrollment_url: str = ""
    enrollment_url_template: str = DEFAULT_ENROLLMENT_URL_TEMPLATE
    organization_name: str = "Filttr"
    is_mdm_unremovable: bool = True
    api_token: str = ""
    tool_token: str = ""
    session_token: str = ""  # runtime only; from admin-2fa unlock
    complete_path: str = "/api/supervision/complete"
    configuration_url: str = ""
    configuration_url_template: str = ""
    supervisor_cert: str = "assets/filttr-supervisor.der"
    method: str = "backup_restore"  # only sparse MobileBackup2 restore (never erase)
    users_search_path: str = "/api/search-users"
    assign_path: str = "/api/supervision/assign"
    raw: dict[str, Any] = field(default_factory=dict)

    def expand_url(
        self,
        template: str,
        *,
        udid: Optional[str] = None,
        access_key: Optional[str] = None,
    ) -> str:
        """Expand ``{access_key}`` / ``{udid}`` placeholders in a URL template."""
        from filttr_supervise.plist_builder import resolve_configuration_url

        key = access_key if access_key is not None else self.access_key
        return resolve_configuration_url(template, udid=udid, access_key=key)

    def configuration_url_for(
        self,
        udid: Optional[str] = None,
        access_key: Optional[str] = None,
    ) -> str:
        """URL written into CloudConfigurationDetails ``ConfigurationURL``.

        Priority:
          1. explicit ``configuration_url``
          2. ``enrollment_url_template`` (Filttr MDM with cfg=1) when access_key set
          3. ``configuration_url_template``
          4. static ``enrollment_url``
        """
        key = (access_key if access_key is not None else self.access_key) or ""

        if self.configuration_url:
            return self.expand_url(self.configuration_url, udid=udid, access_key=key)

        template = self.enrollment_url_template or ""
        if template and "{access_key}" in template:
            if not key:
                return ""
            return self.expand_url(template, udid=udid, access_key=key)
        if template:
            return self.expand_url(template, udid=udid, access_key=key)

        if self.configuration_url_template:
            return self.expand_url(
                self.configuration_url_template, udid=udid, access_key=key
            )

        if self.enrollment_url:
            return self.expand_url(self.enrollment_url, udid=udid, access_key=key)
        return ""

    def enrollment_url_for(
        self,
        udid: Optional[str] = None,
        access_key: Optional[str] = None,
    ) -> str:
        """URL to show/install after supervise (same MDM enroll endpoint by default)."""
        url = self.configuration_url_for(udid=udid, access_key=access_key)
        if url:
            return url
        if self.enrollment_url:
            return self.expand_url(
                self.enrollment_url,
                udid=udid,
                access_key=access_key if access_key is not None else self.access_key,
            )
        return ""

    def require_access_key(self) -> Optional[str]:
        """Return an error message if access_key is required but missing."""
        template = self.enrollment_url_template or ""
        needs_key = "{access_key}" in template or "{access_key}" in (
            self.configuration_url_template or ""
        ) or "{access_key}" in (self.configuration_url or "")
        if needs_key and not (self.access_key or "").strip():
            return (
                "Portal access key is required. Enter it in the GUI or set "
                "access_key in config.json (Firestore portals/{k})."
            )
        if not self.configuration_url_for():
            if needs_key:
                return (
                    "Portal access key is required to build the MDM ConfigurationURL."
                )
            return (
                "No ConfigurationURL: set access_key + enrollment_url_template, "
                "or configuration_url / enrollment_url in config.json."
            )
        return None

    def complete_url(self) -> str:
        base = self.api_base.rstrip("/")
        path = (
            self.complete_path
            if self.complete_path.startswith("/")
            else f"/{self.complete_path}"
        )
        return f"{base}{path}"

    def resolve_cert_path(self, base_dir: Optional[Path] = None) -> Path:
        path = Path(self.supervisor_cert)
        if path.is_absolute():
            return path
        root = base_dir or Path.cwd()
        candidate = root / path
        if candidate.exists():
            return candidate
        import sys

        if getattr(sys, "frozen", False):
            meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            for root2 in (Path(sys.executable).parent, meipass):
                alt = root2 / path
                if alt.exists():
                    return alt
                alt2 = root2 / "assets" / Path(path).name
                if alt2.exists():
                    return alt2
        return candidate


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load config.json; missing file yields defaults."""
    cfg_path = Path(path) if path else _default_config_path()
    data = dict(DEFAULT_CONFIG)
    if cfg_path.is_file():
        with open(cfg_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise ValueError(f"config must be a JSON object: {cfg_path}")
        data.update(loaded)
    def _str(key: str, default: str) -> str:
        if key in data and data[key] is not None:
            return str(data[key])
        return default

    return AppConfig(
        api_base=_str("api_base", DEFAULT_CONFIG["api_base"]) or DEFAULT_CONFIG["api_base"],
        access_key=_str("access_key", ""),
        enrollment_url=_str("enrollment_url", ""),
        enrollment_url_template=_str(
            "enrollment_url_template", DEFAULT_CONFIG["enrollment_url_template"]
        ),
        organization_name=_str("organization_name", "Filttr") or "Filttr",
        is_mdm_unremovable=bool(data.get("is_mdm_unremovable", True)),
        api_token=_str("api_token", ""),
        tool_token=_str("tool_token", "") or _str("api_token", ""),
        complete_path=_str("complete_path", DEFAULT_CONFIG["complete_path"])
        or DEFAULT_CONFIG["complete_path"],
        configuration_url=_str("configuration_url", ""),
        configuration_url_template=_str("configuration_url_template", ""),
        supervisor_cert=_str("supervisor_cert", DEFAULT_CONFIG["supervisor_cert"])
        or DEFAULT_CONFIG["supervisor_cert"],
        method=_str("method", "backup_restore") or "backup_restore",
        users_search_path=_str("users_search_path", "/api/search-users")
        or "/api/search-users",
        assign_path=_str("assign_path", "/api/supervision/assign")
        or "/api/supervision/assign",
        raw=data,
    )


def _default_config_path() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.json"
    return Path(__file__).resolve().parent.parent / "config.json"


def save_default_config(path: str | Path) -> None:
    Path(path).write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
