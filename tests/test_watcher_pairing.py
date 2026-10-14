import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from filttr_supervise.device import DeviceInfo, usbmux_error_message
from filttr_supervise.supervise import DeviceStepError, SuperviseResult, _connect_and_pair
from filttr_supervise.watcher import DeviceWatcher
import filttr_supervise.supervise as sup


def test_watcher_delivers_on_change_and_pauses():
    got = []
    udids = [[]]
    w = DeviceWatcher(got.append, list_fn=lambda: udids[0],
                      info_fn=lambda: DeviceInfo(connected=bool(udids[0]), udid="".join(udids[0])))
    w.poll_once(0, force=True)
    assert len(got) == 1 and not got[0].connected
    udids[0] = ["U1"]
    w.poll_once(1)
    assert got[-1].connected and got[-1].udid == "U1"
    w.pause()
    udids[0] = []
    assert w.poll_once(2) is None
    assert len(got) == 2


def test_watcher_survives_errors():
    def boom():
        raise RuntimeError("x")
    got = []
    w = DeviceWatcher(got.append, list_fn=boom, info_fn=boom)
    w.poll_once(0, force=True)
    assert "x" in got[0].error


class SessionActiveError(Exception):
    pass


class PairingDialogResponsePendingError(Exception):
    pass


def test_pair_times_out_without_hanging(monkeypatch):
    monkeypatch.setattr(sup, "PAIR_TOTAL_TIMEOUT", 0.5)
    ld = MagicMock(paired=False)
    ld.pair = AsyncMock(side_effect=PairingDialogResponsePendingError())
    ld.close = AsyncMock()
    with patch("pymobiledevice3.lockdown.create_using_usbmux", new=AsyncMock(return_value=ld)), \
         patch("asyncio.sleep", new=AsyncMock()):
        r = SuperviseResult(ok=False)
        try:
            asyncio.run(_connect_and_pair("U", r, None))
            assert False
        except DeviceStepError as e:
            assert "Pairing not accepted" in str(e)
    assert any("tap Trust" in l for l in r.log)
    ld.close.assert_awaited()


def test_pair_then_validate_ok():
    ld = MagicMock(paired=False)
    ld.pair = AsyncMock(side_effect=[PairingDialogResponsePendingError(), None])
    ld.validate_pairing = AsyncMock(return_value=True)
    with patch("pymobiledevice3.lockdown.create_using_usbmux", new=AsyncMock(return_value=ld)), \
         patch("asyncio.sleep", new=AsyncMock()):
        r = SuperviseResult(ok=False)
        assert asyncio.run(_connect_and_pair("U", r, None)) is ld
    assert "Paired OK." in r.log


def test_session_active_retries_then_succeeds():
    ld = MagicMock(paired=True)
    create = AsyncMock(side_effect=[SessionActiveError(), ld])
    with patch("pymobiledevice3.lockdown.create_using_usbmux", new=create), \
         patch("asyncio.sleep", new=AsyncMock()):
        r = SuperviseResult(ok=False)
        assert asyncio.run(_connect_and_pair("U", r, None)) is ld
    assert any("iTunes" in l for l in r.log)


def test_usbmux_missing_message(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert "Apple Mobile Device Service" in usbmux_error_message(ConnectionRefusedError())
