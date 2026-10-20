"""Robust TCP connection to the RS485 gateway (e.g. EW11A).

Reconnects forever with back-off, and treats a silent link (no bytes for `idle_timeout` seconds) as dead, because a
half-open TCP connection to a Wi-Fi gateway otherwise just hangs. Frames are 0x7E LEN ... 0x7E.
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable

PACKET_MARKER = 0x7E
MAX_PAYLOAD_LENGTH = 128
BACKOFF = (2, 5, 10, 30, 60)


class SpaConnection:
    def __init__(self, host: str, port: int, on_frame: Callable[[bytes], Awaitable[None]],
                 on_link: Callable[[bool], None] = lambda up: None, idle_timeout: float = 30.0,
                 logger: logging.Logger = None):
        self.host = host
        self.port = int(port)
        self.on_frame = on_frame
        self.on_link = on_link
        self.idle_timeout = idle_timeout
        self.logger = logger or logging.getLogger("spa2mqtt.connection")
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.connected = False
        self.frames_received = 0
        self.frames_sent = 0
        self.reconnects = 0
        self.last_frame_at: float | None = None

    async def run_forever(self):
        attempt = 0
        while True:
            try:
                self.logger.info("Connecting to spa gateway %s:%s", self.host, self.port)
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), timeout=10)
                self.connected = True
                attempt = 0
                self.logger.info("Connected to spa gateway")
                self.on_link(True)
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any failure means: reconnect
                self.logger.warning("Spa connection problem: %s: %s", type(e).__name__, e)
            finally:
                if self.connected:
                    self.on_link(False)
                self.connected = False
                await self._close()
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            self.reconnects += 1
            self.logger.info("Reconnecting in %s s", delay)
            await asyncio.sleep(delay)

    async def _read_byte(self) -> int:
        b = await asyncio.wait_for(self.reader.read(1), timeout=self.idle_timeout)
        if not b:
            raise ConnectionError("gateway closed the connection")
        return b[0]

    async def _read_exactly(self, n: int) -> bytes:
        return await asyncio.wait_for(self.reader.readexactly(n), timeout=5)

    async def _read_loop(self):
        prev = None
        while True:
            cur = await self._read_byte()
            # start of frame: a marker followed by a non-marker (the length byte)
            if not (prev == PACKET_MARKER and cur != PACKET_MARKER):
                prev = cur
                continue
            prev = None
            length = cur
            if length == 0 or length > MAX_PAYLOAD_LENGTH:
                continue
            data = await self._read_exactly(length)
            if data[-1] != PACKET_MARKER:
                prev = data[-1]
                continue
            frame = bytes([PACKET_MARKER, length]) + data
            self.frames_received += 1
            self.last_frame_at = time.monotonic()
            try:
                await self.on_frame(frame)
            except Exception as e:  # noqa: BLE001 - a bad frame must never kill the link
                self.logger.debug("Frame handler error %s on %s", e, frame.hex())
            prev = PACKET_MARKER  # the closing marker can be the next frame's opening marker

    async def send(self, raw: bytes) -> bool:
        if not self.connected or self.writer is None:
            self.logger.debug("Not connected; dropping %s", raw.hex())
            return False
        self.writer.write(raw)
        await self.writer.drain()
        self.frames_sent += 1
        return True

    async def _close(self):
        if self.writer is not None:
            try:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), timeout=3)
            except Exception:  # noqa: BLE001
                pass
        self.reader = self.writer = None
