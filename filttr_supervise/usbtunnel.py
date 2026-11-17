"""Remote setup: share a phone's USB connection with the admin over an outbound relay.

Two ends, both of which only ever make OUTBOUND connections to the Filttr relay
(``mdm.filttr.org``); nothing on either PC listens on the internet:

* ``ShareAgent`` (user's PC): forwards the Apple USB service on 127.0.0.1:27015 to the
  relay. It can ONLY ever reach that one local service -- the admin side sends "open a
  connection" with no address, so it cannot ask for any other host or port.
* ``ShareHost`` (admin's PC): opens a loopback-only listener and points pymobiledevice3
  at it, so the existing supervise flow sees the remote phone as if it were plugged in.

The user has to start the agent, type the one-time code shown to the admin, and can
disconnect at any time via the visible Disconnect button.

The relay sees the forwarded bytes, so it is as trusted as the MDM server it runs beside.
"""
from __future__ import annotations

import asyncio
import json
import struct
import sys
import threading
from typing import Callable, Optional, Union

DEFAULT_RELAY_URL = "ws://178.62.53.83:8765"

# On macOS usbmuxd is a Unix socket; on Windows it's a TCP service.
if sys.platform == "darwin":
    USBMUXD_ADDR: Union[str, tuple] = "/var/run/usbmuxd"
else:
    USBMUXD_ADDR = ("127.0.0.1", 27015)  # Apple Mobile Device Service on Windows

# Frame kinds
OPEN, DATA, CLOSE = 0, 1, 2
_HDR = struct.Struct(">BI")   # kind (1 byte) + connection-id (4 bytes)
CHUNK = 64 * 1024


def _pack(kind: int, cid: int, payload: bytes = b"") -> bytes:
    return _HDR.pack(kind, cid) + payload


def _unpack(frame: bytes) -> tuple[int, int, bytes]:
    kind, cid = _HDR.unpack_from(frame)
    return kind, cid, frame[_HDR.size:]


def normalize_code(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isdigit())


def pretty_code(raw: str) -> str:
    return normalize_code(raw)


# -------------------------------------------------------------------- base peer
class _Peer:
    """One relay WebSocket connection plus local TCP connections riding on it."""

    def __init__(
        self,
        on_status: Callable[[str], None],
        on_end: Callable[[str], None],
    ) -> None:
        self._on_status = on_status
        self._on_end = on_end
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._conns: dict[int, asyncio.StreamWriter] = {}
        self._ended = False

    # ---- thread control -------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._thread_main, name=type(self).__name__, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop, ws = self._loop, self._ws
        if loop and ws:
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
            except Exception:
                pass

    def join(self, timeout: float = 5.0) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _thread_main(self) -> None:
        reason = ""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            reason = self._loop.run_until_complete(self._run()) or ""
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
        finally:
            self._finish(reason)
            try:
                self._loop.close()
            except Exception:
                pass

    def _finish(self, reason: str) -> None:
        if self._ended:
            return
        self._ended = True
        try:
            self._on_end(reason)
        except Exception:
            pass

    def status(self, text: str) -> None:
        try:
            self._on_status(text)
        except Exception:
            pass

    # ---- framing helpers ------------------------------------------------------
    async def _send(self, kind: int, cid: int, payload: bytes = b"") -> None:
        ws = self._ws
        if ws is not None:
            await ws.send(_pack(kind, cid, payload))

    async def _pump_tcp_to_ws(self, cid: int, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                data = await reader.read(CHUNK)
                if not data:
                    break
                await self._send(DATA, cid, data)
        except Exception:
            pass
        finally:
            if cid in self._conns:
                self._close_conn(cid)
                try:
                    await self._send(CLOSE, cid)
                except Exception:
                    pass

    def _close_conn(self, cid: int) -> None:
        w = self._conns.pop(cid, None)
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    async def _frame_loop(self) -> str:
        """Read frames from the relay until the connection closes."""
        from websockets.exceptions import ConnectionClosed

        try:
            async for msg in self._ws:
                if isinstance(msg, str):
                    continue
                try:
                    kind, cid, payload = _unpack(msg)
                except struct.error:
                    continue
                await self._on_frame(kind, cid, payload)
        except ConnectionClosed as exc:
            return (
                (exc.rcvd.reason if exc.rcvd and exc.rcvd.reason else "")
                or "The connection was closed."
            )
        finally:
            for cid in list(self._conns):
                self._close_conn(cid)
        return "The connection was closed."

    async def _on_frame(self, kind: int, cid: int, payload: bytes) -> None:
        raise NotImplementedError

    async def _run(self) -> str:
        raise NotImplementedError


# ------------------------------------------------------------------ user side
class ShareAgent(_Peer):
    """Runs on the user's PC.

    Connects to the relay, authenticates with a one-time code, then forwards
    connections from the admin side to the local Apple Mobile Device Service
    (127.0.0.1:27015). The admin side cannot request any other address.
    """

    def __init__(
        self,
        code: str,
        on_status: Callable[[str], None],
        on_end: Callable[[str], None],
        *,
        relay_url: str = DEFAULT_RELAY_URL,
        target: Union[str, tuple] = USBMUXD_ADDR,
    ) -> None:
        super().__init__(on_status, on_end)
        self.code = normalize_code(code)
        self.relay_url = relay_url.rstrip("/")
        self.target = target
        self.paired = False

    async def _run(self) -> str:
        import websockets

        self.status("Connecting to relay…")
        try:
            async with websockets.connect(
                self.relay_url + "/agent",
                open_timeout=15,
                max_size=1 << 20,
            ) as ws:
                self._ws = ws
                await ws.send(json.dumps({"type": "hello", "code": self.code}))
                try:
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 15))
                except asyncio.TimeoutError:
                    return "The Filttr server didn't answer. Try again."
                if reply.get("type") != "paired":
                    return reply.get("message") or "That code wasn't accepted."
                self.paired = True
                self.status("Connected — your phone is being set up remotely.")
                return await self._frame_loop()
        except OSError:
            return (
                "Couldn't reach the Filttr server. "
                "Check your internet connection and try again."
            )
        except Exception as exc:  # noqa: BLE001
            return f"Connection failed ({type(exc).__name__})."

    async def _on_frame(self, kind: int, cid: int, payload: bytes) -> None:
        if kind == OPEN:
            if cid in self._conns:
                return
            try:
                if isinstance(self.target, str):
                    reader, writer = await asyncio.open_unix_connection(self.target)
                else:
                    reader, writer = await asyncio.open_connection(*self.target)
            except OSError:
                await self._send(CLOSE, cid)
                return
            self._conns[cid] = writer
            asyncio.ensure_future(self._pump_tcp_to_ws(cid, reader))
        elif kind == DATA:
            w = self._conns.get(cid)
            if w is not None:
                try:
                    w.write(payload)
                    await w.drain()
                except Exception:
                    self._close_conn(cid)
                    await self._send(CLOSE, cid)
        elif kind == CLOSE:
            self._close_conn(cid)


# ----------------------------------------------------------------- admin side
class ShareHost(_Peer):
    """Runs on the admin's PC.

    Creates a relay session, receives a one-time code to give the user,
    then exposes the remote phone on a loopback TCP port so pymobiledevice3
    treats it as a locally connected device.
    """

    def __init__(
        self,
        token: str,
        on_status: Callable[[str], None],
        on_end: Callable[[str], None],
        *,
        on_code: Callable[[str], None],
        on_ready: Callable[[int], None],
        relay_url: str = DEFAULT_RELAY_URL,
    ) -> None:
        super().__init__(on_status, on_end)
        self.token = token
        self.relay_url = relay_url.rstrip("/")
        self._on_code = on_code
        self._on_ready = on_ready
        self.port = 0
        self._next_id = 1
        self._server = None

    async def _run(self) -> str:
        import websockets

        self.status("Contacting the relay…")
        try:
            async with websockets.connect(
                self.relay_url + "/admin",
                open_timeout=15,
                max_size=1 << 20,
                additional_headers={"Authorization": "Bearer " + self.token},
            ) as ws:
                self._ws = ws
                await ws.send(json.dumps({"type": "create"}))
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
                except asyncio.TimeoutError:
                    return "The relay didn't respond."
                if msg.get("type") != "session":
                    return msg.get("message") or "The relay refused the session."
                self._on_code(msg["code"])
                self.status("Waiting for the user to enter the code…")
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 300))
                except asyncio.TimeoutError:
                    return "Nobody entered the code within 5 minutes."
                if msg.get("type") != "paired":
                    return msg.get("message") or "Pairing failed."
                self._server = await asyncio.start_server(
                    self._on_local, "127.0.0.1", 0
                )
                self.port = self._server.sockets[0].getsockname()[1]
                self._on_ready(self.port)
                self.status("Connected")
                try:
                    return await self._frame_loop()
                finally:
                    self._server.close()
        except OSError:
            return "Couldn't reach the relay. Check your internet connection."
        except Exception as exc:  # noqa: BLE001
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (401, 403):
                return "The relay rejected the admin token."
            return f"Connection failed ({type(exc).__name__})."

    async def _on_local(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        cid = self._next_id
        self._next_id += 1
        self._conns[cid] = writer
        try:
            await self._send(OPEN, cid)
        except Exception:
            self._close_conn(cid)
            return
        await self._pump_tcp_to_ws(cid, reader)

    async def _on_frame(self, kind: int, cid: int, payload: bytes) -> None:
        if kind == DATA:
            w = self._conns.get(cid)
            if w is not None:
                try:
                    w.write(payload)
                    await w.drain()
                except Exception:
                    self._close_conn(cid)
                    await self._send(CLOSE, cid)
        elif kind == CLOSE:
            self._close_conn(cid)


# ---------------------------------------- point pymobiledevice3 at the tunnel
_override: Optional[tuple[str, int]] = None
_patched = False


def set_usbmux_override(addr: Optional[tuple[str, int]]) -> None:
    """Make pymobiledevice3 talk to ``addr`` instead of the PC's own USB service.

    Call with the loopback port that ``ShareHost`` opened once it's ready,
    and with ``None`` to restore normal behaviour after the session ends.
    """
    global _override, _patched
    _override = addr
    if _patched:
        return
    import socket
    from pymobiledevice3.usbmux import MuxConnection

    _orig = MuxConnection._resolve_usbmux_address

    def _resolve(usbmux_address=None):
        if usbmux_address is None and _override is not None:
            return _override, socket.AF_INET
        return _orig(usbmux_address)

    MuxConnection._resolve_usbmux_address = staticmethod(_resolve)
    _patched = True


def usbmux_override() -> Optional[tuple[str, int]]:
    """Return the current override address, or None if not active."""
    return _override


def local_usb_service_up(addr: Union[str, tuple] = USBMUXD_ADDR, timeout: float = 1.5) -> bool:
    """Return True if the Apple Mobile Device Service is reachable."""
    import os, socket
    if isinstance(addr, str):
        return os.path.exists(addr)
    try:
        with socket.create_connection(addr, timeout=timeout):
            return True
    except OSError:
        return False
