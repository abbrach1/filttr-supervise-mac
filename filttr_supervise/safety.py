"""Hard safety guards: never erase / factory-reset an iPhone."""
from __future__ import annotations

from typing import Any, Iterable

# Substrings / attribute names that indicate a wipe. Used by static and runtime guards.
ERASE_API_MARKERS = (
    "erase_device",
    "EraseDevice",
    "obliterate",
    "Obliterate",
    "factory_reset",
    "FactoryReset",
    "eraseDevice",
    "profile erase-device",
    "MobileConfigService.erase",
)


class WipeForbiddenError(RuntimeError):
    """Raised if any code path attempts an erase / factory-reset API."""


def assert_not_erase_call(name: str) -> None:
    """Raise if ``name`` looks like an erase/obliterate API."""
    lowered = name.lower()
    for marker in ERASE_API_MARKERS:
        if marker.lower() in lowered:
            raise WipeForbiddenError(
                f"Refusing wipe API call {name!r}. "
                "Filttr Supervise never factory-resets the iPhone."
            )


def assert_no_erase_in_callable_names(names: Iterable[str]) -> None:
    for name in names:
        assert_not_erase_call(name)


def wrap_forbid_erase(obj: Any, attr: str = "erase_device") -> Any:
    """Replace ``obj.attr`` with a function that always raises WipeForbiddenError."""

    def _blocked(*_a: Any, **_k: Any) -> None:
        raise WipeForbiddenError(
            f"{type(obj).__name__}.{attr} is blocked — "
            "Filttr Supervise never erases the device."
        )

    try:
        setattr(obj, attr, _blocked)
    except Exception:
        pass
    return obj


def validate_device_for_supervise(
    *,
    find_my_enabled: bool | None,
    activation_state: str,
    paired: bool,
) -> list[str]:
    """Return blocking error messages; empty list means OK to proceed."""
    errors: list[str] = []
    if not paired:
        errors.append("Device is not paired/trusted. Unlock and tap Trust.")
    state = (activation_state or "").strip()
    if state and state.lower() not in ("activated",):
        errors.append(
            f"Device activation state is {state!r}; must be Activated "
            "(past Setup Assistant). Refusing to run."
        )
    if find_my_enabled is True:
        errors.append(
            "Find My is ON. Turn it off (Settings → [Name] → Find My) before supervising. "
            "Refusing to run — Find My blocks safe backup restore."
        )
    return errors
