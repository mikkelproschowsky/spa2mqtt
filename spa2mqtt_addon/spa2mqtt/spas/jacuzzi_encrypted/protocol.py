"""Jacuzzi "encrypted" RS485 protocol (J235 and similar 2018+ boards) – bus client and setpoint control.

The board polls each client with CLEAR_TO_SEND (0x06) on the client's channel. A client may only transmit right after
its own CTS – sending at any other time collides with other bus traffic. Topside panels send CC_REQ (0xCC) button
packets; the body is a pair (a, b) where a ^ b is the button code (a is varied by the panels).

Channel: we reuse a channel the board polls but that nobody answers on ("dormant"), as the original spa2mqtt did; if
none is found, we ask the board for a new one in response to its new-client CTS (0x00) and use what it assigns.

Setpoint: there is no direct "set temperature" message, so we press TEMP UP/DOWN one at a time and verify each press
against the next status updates (closed loop). The first press may only wake the display, so a few presses without
effect are tolerated before giving up.
"""
import asyncio
import logging
import random
import time
from collections import deque
from typing import Callable

from spa2mqtt.spas.jacuzzi_encrypted.messages.JacuzziEncryptedMessage import *  # noqa: F401,F403 (registers parsers)
from spa2mqtt.spas.jacuzzi_encrypted.messages.JacuzziEncryptedMessageFactory import JacuzziEncryptedMessageFactory
from spa2mqtt.spas.jacuzzi_encrypted.packet import JacuzziEncryptedPacket
from spa2mqtt.spas.jacuzzi_encrypted.packet_types import JacuzziEncryptedPacketType as T
from spa2mqtt.spas.jacuzzi_encrypted.packet_types import JacuzziTopsideControllerButton as B

CLIENT_MID = 0xBF
DORMANT_CONFIDENCE = 40          # CC_REQ packets observed before picking a dormant channel
PRESS_SETTLE_S = 4.0             # how long to wait for a press to show up in the status
MAX_PRESSES_WITHOUT_EFFECT = 6
SETPOINT_MIN, SETPOINT_MAX, SETPOINT_STEP = 18.5, 40.0, 0.5  # J235: measured 2026-10-20 (it stops at 18.5 °C)


class JacuzziProtocol:
    def __init__(self, send: Callable, message_configuration: dict, on_status: Callable[[dict], None],
                 on_command_state: Callable[[dict], None] = lambda s: None, logger: logging.Logger = None):
        self.send = send
        self.message_configuration = message_configuration
        self.on_status = on_status
        self.on_command_state = on_command_state
        self.logger = logger or logging.getLogger("spa2mqtt.protocol")

        self.channel: int | None = None
        self.cts_channels: set[int] = set()
        self.active_channels: set[int] = set()
        self.cc_seen = 0
        self.want_new_channel = False
        self.channel_requested_at = 0.0
        self.queue: deque[bytes] = deque()

        self.status: dict = {}
        self.raw_status: list[int] = []
        self.status_at: float | None = None

        # setpoint control
        self.target: float | None = None
        self.press_sent_at: float | None = None
        self.setpoint_at_press: float | None = None
        self.presses_without_effect = 0
        self.command_result = "idle"

    # ---------- public API ----------
    def reset_link(self):
        """Called after a reconnect: the bus state is unknown again."""
        self.queue.clear()
        self.press_sent_at = None
        self.cc_seen = 0
        self.active_channels.clear()
        self.cts_channels.clear()
        # keep self.channel – the board normally still polls it

    def press(self, button: int):
        a = random.randint(0, 255)
        body = bytearray([a, a ^ int(button)])
        pkt = JacuzziEncryptedPacket.construct_with_params(mid=CLIENT_MID, channel=self.channel or 0,
                                                            packet_type=T.CC_REQ, body=body)
        self.queue.append(bytes(pkt.raw))

    def set_target(self, value: float):
        v = round(float(value) * 2) / 2
        v = max(SETPOINT_MIN, min(SETPOINT_MAX, v))
        self.target = v
        self.presses_without_effect = 0
        self.press_sent_at = None
        self.command_result = "running"
        self.logger.info("New target setpoint %.1f °C (current %s)", v, self.status.get("setpoint_temperature"))
        self._publish_command_state()

    def diagnostics(self) -> dict:
        return {"channel": self.channel, "cts_channels": sorted(self.cts_channels),
                "panel_channels": sorted(self.active_channels), "queue": len(self.queue),
                "target": self.target, "command_result": self.command_result,
                "raw_status": " ".join(f"{b:02x}" for b in self.raw_status)}

    # ---------- frame handling ----------
    async def handle_frame(self, frame: bytes):
        pkt = JacuzziEncryptedPacket.from_raw(frame)  # raises on bad checksum
        t = pkt.as_enum()
        if t == T.CLEAR_TO_SEND:
            self.cts_channels.add(pkt.channel)
            if pkt.channel == self.channel:
                await self._on_our_cts()
        elif t == T.NOTHING_TO_SEND:
            self.active_channels.add(pkt.channel)
        elif t in (T.CC_REQ, T.CC_REQ_ALT_17):
            self.active_channels.add(pkt.channel)
            self.cc_seen += 1
            if self.channel is None:
                self._maybe_pick_channel()
        elif t == T.CLIENT_CLEAR_TO_SEND:
            if self.channel is None and self.want_new_channel and time.monotonic() - self.channel_requested_at > 10:
                await self._request_channel()
        elif t == T.CHANNEL_ASSIGNMENT_RESPONSE:
            # only take an assignment that answers our own request (body: channel, F1, 73)
            if (self.want_new_channel and pkt.body and time.monotonic() - self.channel_requested_at < 5
                    and bytes(pkt.body[1:3]) in (bytes([0xF1, 0x73]), b"")):
                ch = pkt.body[0]
                self.logger.info("Board assigned channel 0x%02x", ch)
                await self.send(JacuzziEncryptedPacket.construct_with_params(
                    mid=CLIENT_MID, channel=ch, packet_type=T.CHANNEL_ASSIGNMENT_ACK, body=bytearray()).raw)
                self.channel = ch
                self.want_new_channel = False
        elif t == T.STATUS_UPDATE:
            msg = JacuzziEncryptedMessageFactory.from_packet(pkt, message_configuration=self.message_configuration)
            parsed = msg.parse()
            self.raw_status = list(pkt.data)
            self.status = parsed
            self.status_at = time.monotonic()
            self._control_step()
            self.on_status(parsed)

    def _maybe_pick_channel(self):
        if self.cc_seen < DORMANT_CONFIDENCE or self.want_new_channel:
            return
        dormant = sorted(self.cts_channels - self.active_channels)
        if dormant:
            self.channel = dormant[0]
            self.logger.info("Using dormant channel 0x%02x (polled: %s, panels: %s)", self.channel,
                             [hex(c) for c in sorted(self.cts_channels)], [hex(c) for c in sorted(self.active_channels)])
        else:
            self.logger.info("No dormant channel; will ask the board for one")
            self.want_new_channel = True

    async def _request_channel(self):
        self.channel_requested_at = time.monotonic()
        # Balboa new-client request: channel 0xFE (broadcast), mid 0xBF, type 0x01, body 02 F1 73 (device type + id).
        # (The original spa2mqtt had channel/mid swapped and no 0x02, so the board never assigned a channel.)
        pkt = JacuzziEncryptedPacket.construct_with_params(mid=CLIENT_MID, channel=0xFE,
                                                           packet_type=T.CHANNEL_ASSIGNMENT_REQ,
                                                           body=bytearray([0x02, 0xF1, 0x73]))
        self.logger.info("Requesting a channel")
        await self.send(pkt.raw)

    async def _on_our_cts(self):
        if not self.queue:
            return
        raw = bytearray(self.queue.popleft())
        if raw[2] != self.channel:  # queued before we had a channel: re-address and re-checksum
            raw[2] = self.channel
            raw[-2] = JacuzziEncryptedPacket.calculate_checksum(raw[1:-2])
        await self.send(bytes(raw))
        if self.press_sent_at is not None:
            self.press_sent_at = time.monotonic()  # settle time counts from the actual transmission

    # ---------- setpoint closed loop ----------
    def _control_step(self):
        if self.target is None or self.channel is None:
            return
        sp = self.status.get("setpoint_temperature")
        if sp is None:
            return
        now = time.monotonic()
        if abs(sp - self.target) < 0.01:
            self.logger.info("Setpoint reached: %.1f °C", sp)
            self.target = None
            self.press_sent_at = None
            self.command_result = "ok"
            self._publish_command_state()
            return
        if self.press_sent_at is not None:
            if sp != self.setpoint_at_press:
                self.presses_without_effect = 0
                self.press_sent_at = None
            elif now - self.press_sent_at < PRESS_SETTLE_S:
                return  # still waiting for the press to show up
            else:
                self.presses_without_effect += 1
                self.press_sent_at = None
                if self.presses_without_effect > MAX_PRESSES_WITHOUT_EFFECT:
                    self.logger.error("Setpoint did not follow %d presses – giving up (target %.1f, setpoint %.1f)",
                                      self.presses_without_effect, self.target, sp)
                    self.target = None
                    self.command_result = "failed"
                    self._publish_command_state()
                    return
        if self.press_sent_at is None and not self.queue:
            self.press(B.BTN_TEMP_UP if self.target > sp else B.BTN_TEMP_DOWN)
            self.press_sent_at = now
            self.setpoint_at_press = sp

    def _publish_command_state(self):
        self.on_command_state({"target": self.target, "result": self.command_result})
