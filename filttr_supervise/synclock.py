from __future__ import annotations

import asyncio
import subprocess
import time
from contextlib import asynccontextmanager
from typing import Any

from pymobiledevice3.services.afc import AFC_LOCK_EX, AFC_LOCK_UN

SYNC_WILL_START   = "com.apple.itunes-mobdev.syncWillStart"
SYNC_LOCK_REQUEST = "com.apple.itunes-mobdev.syncLockRequest"
SYNC_DID_START    = "com.apple.itunes-mobdev.syncDidStart"
SYNC_DID_FINISH   = "com.apple.itunes-mobdev.syncDidFinish"
LOCK_FILE         = "/com.apple.itunes.lock_sync"


def _kill_sync_competitors() -> None:
    for name in ("AppleDevices.exe", "iTunes.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)
    time.sleep(2.0)


@asynccontextmanager
async def sync_armed(lockdown: Any):
    from pymobiledevice3.services.notification_proxy import NotificationProxyService
    from pymobiledevice3.services.afc import AfcService

    await asyncio.to_thread(_kill_sync_competitors)

    np_svc  = NotificationProxyService(lockdown)
    afc_svc = AfcService(lockdown)
    np  = await np_svc.__aenter__()
    afc = await afc_svc.__aenter__()
    lock_fh = None
    try:
        await np.notify_post(SYNC_WILL_START)
        lock_fh = await afc.fopen(LOCK_FILE, "r+")
        await np.notify_post(SYNC_LOCK_REQUEST)
        locked = False
        for _ in range(60):
            try:
                await afc.lock(lock_fh, AFC_LOCK_EX)
                locked = True
                break
            except Exception:
                await asyncio.sleep(0.2)
        if not locked:
            raise RuntimeError(
                "Cannot acquire iTunes sync lock after 12s — "
                "close Apple Devices / iTunes and retry."
            )
        await np.notify_post(SYNC_DID_START)
        yield
    finally:
        try:
            if lock_fh is not None:
                await afc.lock(lock_fh, AFC_LOCK_UN)
                await afc.fclose(lock_fh)
        except Exception:
            pass
        try:
            await np.notify_post(SYNC_DID_FINISH)
        except Exception:
            pass
        try:
            await afc_svc.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            await np_svc.__aexit__(None, None, None)
        except Exception:
            pass