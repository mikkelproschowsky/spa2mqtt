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
    def __init__(self):
        super().__init__(send=None, message_configuration={}, on_status=lambda s: None)
        self.channel = 0x11

    def tick(self, setpoint):
        self.status = {"setpoint_temperature": setpoint}
        self._control_step()


def _decoded_button(raw: bytes) -> int:
    pkt = JacuzziEncryptedPacket.from_raw(raw)
    return pkt.body[0] ^ pkt.body[1]


def test_setpoint_loop_reaches_target():
    p = FakeProto()
    p.set_target(37.6)  # rounds to 37.5
    assert p.target == 37.5
    sp = 38.5
    for _ in range(50):
        p.tick(sp)
        if p.queue:
            btn = _decoded_button(p.queue.popleft())
            assert btn == P.B.BTN_TEMP_DOWN
            sp -= 0.5  # the spa follows the press
        if p.target is None:
            break
    assert sp == 37.5 and p.command_result == "ok"


def test_setpoint_loop_gives_up_without_effect():
    p = FakeProto()
    P.PRESS_SETTLE_S = 0.0
    p.set_target(40)
    for _ in range(100):
        p.tick(38.0)
        p.queue.clear()  # presses are sent but the spa never changes
        if p.target is None:
            break
        time.sleep(0.001)
    assert p.command_result == "failed"


def test_first_press_may_only_wake_display():
    p = FakeProto()
    P.PRESS_SETTLE_S = 0.0
    p.set_target(39.0)
    sp, presses = 38.5, 0
    for _ in range(50):
        p.tick(sp)
        if p.queue:
            p.queue.popleft()
            presses += 1
            if presses >= 2:
                sp = 39.0  # first press ignored (display wake), second one changes the setpoint
        if p.target is None:
            break
        time.sleep(0.001)
    assert p.command_result == "ok" and presses == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
