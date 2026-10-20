"""Listen to the spa through the gateway with the add-on's runtime classes, WITHOUT sending anything.

Usage: PYTHONPATH=spa2mqtt_addon python dev/live_listen.py <gateway-ip> [seconds]
Prints status changes, the channel the client would pick, and the raw decoded status bytes (to find unknown fields).
"""
import asyncio
import os
import sys
import time

import yaml

from spa2mqtt.connection import SpaConnection
from spa2mqtt.spas.jacuzzi_encrypted.protocol import JacuzziProtocol

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.141"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 30
VARIANT = os.path.join(os.path.dirname(__file__), "..", "spa2mqtt_addon", "spa2mqtt", "spa_definitions",
                       "jacuzzi_j235_encrypted.yml")


async def main():
    cfg = yaml.safe_load(open(VARIANT, encoding="utf-8"))["message_configuration"]
    sent = []
    last = {}
    raws = {}

    async def dry_send(raw: bytes):
        sent.append(raw.hex())
        return True

    def on_status(parsed):
        if parsed != last.get("p"):
            print(time.strftime("%H:%M:%S"), parsed, "| raw", " ".join(f"{b:02x}" for b in proto.raw_status))
            last["p"] = parsed
        for i, b in enumerate(proto.raw_status):
            raws.setdefault(i, set()).add(b)

    proto = JacuzziProtocol(send=dry_send, message_configuration=cfg, on_status=on_status)
    orig = proto.handle_frame

    async def handle(frame):  # also print changes of the lights packet (0xCA)
        before = list(proto.raw_lights)
        await orig(frame)
        if proto.raw_lights != before:
            print(time.strftime("%H:%M:%S"), "LIGHTS", " ".join(f"{b:02x}" for b in proto.raw_lights))
    conn = SpaConnection(HOST, 8899, on_frame=handle)
    task = asyncio.create_task(conn.run_forever())
    await asyncio.sleep(SECONDS)
    task.cancel()
    print("frames", conn.frames_received, "| diagnostics", proto.diagnostics())
    print("would have sent:", sent[:5])
    print("varying raw status bytes:", {i: sorted(f"{x:02x}" for x in v) for i, v in raws.items() if len(v) > 1})


asyncio.run(main())
