"""Offline tests: frame reader (replayed capture over a local TCP server), reconnect, and the setpoint closed loop.

Run: PYTHONPATH=spa2mqtt_addon python -m pytest tests  (or python tests/test_runtime.py)
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "spa2mqtt_addon"))

import yaml  # noqa: E402

from spa2mqtt.connection import SpaConnection  # noqa: E402
from spa2mqtt.spas.jacuzzi_encrypted import protocol as P  # noqa: E402
from spa2mqtt.spas.jacuzzi_encrypted.packet import JacuzziEncryptedPacket  # noqa: E402

HERE = os.path.dirname(__file__)
CAPTURE = os.path.join(HERE, "capture_j235.bin")
CFG = yaml.safe_load(open(os.path.join(HERE, "..", "spa2mqtt_addon", "spa2mqtt", "spa_definitions",
                                       "jacuzzi_j235_encrypted.yml"), encoding="utf-8"))["message_configuration"]


def test_replay_capture_and_reconnect():
    data = open(CAPTURE, "rb").read()
    statuses, links = [], []

    async def run():
        served = {"n": 0}

        async def handle(reader, writer):
            served["n"] += 1
            writer.write(data)  # then close -> the client must reconnect
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        proto = P.JacuzziProtocol(send=lambda raw: asyncio.sleep(0), message_configuration=CFG,
                                  on_status=statuses.append)
        conn = SpaConnection("127.0.0.1", port, on_frame=proto.handle_frame, on_link=links.append, idle_timeout=2)
        import spa2mqtt.connection as C
        C.BACKOFF = (0.1,)
        task = asyncio.create_task(conn.run_forever())
        await asyncio.sleep(1.5)
        task.cancel()
        server.close()
        return conn, proto, served["n"]

    conn, proto, connections = asyncio.run(run())
    assert connections >= 2, "client did not reconnect after the gateway closed the connection"
    assert True in links and False in links
    assert statuses and statuses[0]["current_temperature"] == 38.5 and statuses[0]["setpoint_temperature"] == 38.5
    assert 0x10 in proto.cts_channels and 0x10 in proto.active_channels


class FakeProto(P.JacuzziProtocol):
    """Drives the control loop without a bus: presses are 'sent' instantly; the fake spa applies them."""

    def __init__(self, ignore_first=0, stuck=False):
        super().__init__(send=None, message_configuration={}, on_status=lambda s: None)
        self.channel = 0x11
        self.sp = 38.5
        self.ignore = ignore_first
        self.stuck = stuck
        self.sent = 0
        P.PRESS_GAP_S = 0.0
        P.BATCH_SETTLE_S = 0.0

    def run(self, rounds=200):
        for _ in range(rounds):
            while self.queue:  # the bus sends everything; the spa reacts
                btn = _decoded_button(self.queue.popleft()[0])
                self.sent += 1
                if self.ignore:
                    self.ignore -= 1
                elif not self.stuck:
                    self.sp = max(18.5, min(40.0, self.sp + (0.5 if btn == P.B.BTN_TEMP_UP else -0.5)))
            self.status = {"setpoint_temperature": self.sp}
            self.sp_changed_at = 0.0
            self._control_step()
            if self.target is None:
                return


def _decoded_button(raw: bytes) -> int:
    pkt = JacuzziEncryptedPacket.from_raw(raw)
    return pkt.body[0] ^ pkt.body[1]


def test_setpoint_burst_reaches_target_without_overshoot():
    p = FakeProto()
    p.set_target(18.4)  # rounds/clamps to 18.5
    assert p.target == 18.5
    p.run()
    assert p.sp == 18.5 and p.command_result == "ok" and p.sent == 40  # exactly the needed presses


def test_wake_press_is_made_up():
    p = FakeProto(ignore_first=1)
    p.set_target(37.0)
    p.run()
    assert p.sp == 37.0 and p.command_result == "ok" and p.sent == 4  # 3 needed + 1 ignored


def test_gives_up_without_effect():
    p = FakeProto(stuck=True)
    p.set_target(40)
    p.run()
    assert p.command_result == "failed"


def test_cancel_stops_command():
    p = FakeProto()
    p.set_target(20)
    p.status = {"setpoint_temperature": p.sp}
    p._control_step()
    assert p.queue
    p.cancel()
    assert not p.queue and p.target is None and p.command_result == "cancelled"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
