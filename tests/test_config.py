from __future__ import annotations

import json
from pathlib import Path

from filttr_supervise.config import DEFAULT_ENROLLMENT_URL_TEMPLATE, load_config


def test_load_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.api_base == "https://www.filttr.org"
    assert cfg.organization_name == "Filttr"
    assert cfg.complete_path == "/api/supervision/complete"
    assert cfg.is_mdm_unremovable is True
    assert cfg.enrollment_url_template == DEFAULT_ENROLLMENT_URL_TEMPLATE
    assert "{access_key}" in cfg.enrollment_url_template
    assert "cfg=1" not in cfg.enrollment_url_template


def test_enrollment_template_requires_access_key(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({}))
    cfg = load_config(p)
    assert cfg.require_access_key() is not None
    assert cfg.configuration_url_for() == ""

    cfg.access_key = "abcKEY"
    assert cfg.require_access_key() is None
    assert (
        cfg.configuration_url_for()
        == "https://www.filttr.org/api/mdm-enroll?k=abcKEY"
    )


def test_explicit_configuration_url_wins(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "access_key": "k1",
                "configuration_url": "https://custom.example/enroll",
            }
        )
    )
    cfg = load_config(p)
    assert cfg.configuration_url_for() == "https://custom.example/enroll"


def test_udid_template_still_works(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "enrollment_url_template": "",
                "configuration_url_template": "https://www.filttr.org/enroll/{udid}",
            }
        )
    )
    cfg = load_config(p)
    assert cfg.configuration_url_for("ABC123") == "https://www.filttr.org/enroll/ABC123"
