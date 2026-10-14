"""Background device watcher: polls usbmux and re-reads device info on change.

Runs on its own daemon thread; never touches Tk. Results are delivered via the
``on_info`` callback, which the GUI marshals onto the Tk thread with ``after``.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from filttr_supervise.device import DeviceInfo, get_device_info, list_connected_udids


def _state_key(info: DeviceInfo) -> tuple:
    return (
        info.connected,
        info.udid,
        info.paired,
        info.activation_state,
        info.supervised,
        info.find_my_enabled,
        info.error,
    )


class DeviceWatcher:
    def __init__(
        self,
        on_info: Callable[[DeviceInfo], None],
        *,
        interval: float = 2.0,
        full_refresh_every: float = 10.0,
        list_fn: Callable[[], list[str]] = list_connected_udids,
        info_fn: Callable[[], DeviceInfo] = get_device_info,
    ) -> None:
        self._on_info = on_info
        self._interval = interval
        self._full_every = max(1, int(full_refresh_every / interval))
        self._list_fn = list_fn
        self._info_fn = info_fn
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._force = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_udids: Optional[list[str]] = None
        self._last_key: Optional[tuple] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="device-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._force.set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()
        self.refresh_now()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def refresh_now(self) -> None:
        self._last_key = None
        self._force.set()

    def poll_once(self, tick: int = 0, force: bool = False) -> Optional[DeviceInfo]:
        """One iteration (public for tests). Returns info if it was delivered."""
        if self._paused.is_set():
            return None
        try:
            udids = sorted(self._list_fn())
        except Exception:
            udids = []
        changed = udids != self._last_udids
        self._last_udids = udids
        # While unpaired/locked, re-read more often so Trust is picked up quickly.
        periodic = tick % self._full_every == 0 or (
            self._last_key is not None and udids and not self._last_key[2]
        )
        if not (changed or force or periodic):
            return None
        try:
            info = self._info_fn()
        except Exception as exc:  # never kill the watcher
            info = DeviceInfo(error=f"Device read failed: {exc}")
        if self._paused.is_set():
            return None
        key = _state_key(info)
        if key == self._last_key and not force:
            return None
        self._last_key = key
        try:
            self._on_info(info)
        except Exception:
            pass
        return info

    def _run(self) -> None:
        tick = 0
        force = True
        while not self._stop.is_set():
            self.poll_once(tick, force=force)
            tick += 1
            force = self._force.wait(self._interval)
            self._force.clear()
