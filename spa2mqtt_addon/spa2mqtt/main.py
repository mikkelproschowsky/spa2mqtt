"""spa2mqtt – Jacuzzi RS485 (via a TCP gateway) to Home Assistant over MQTT.

Configuration (environment variables; the Home Assistant add-on's run.sh sets them from the add-on options and the
Mosquitto service):
  SPA_IP, SPA_PORT, VARIANT_DEFINITION, MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
  DEVICE_ID (default 185569045), LOG_LEVEL (default info), DISCOVERY_PREFIX (default homeassistant)
"""
import asyncio
import logging
import os
import signal
import time

import yaml

from spa2mqtt.connection import SpaConnection
from spa2mqtt.ha_mqtt import HaMqttBridge
from spa2mqtt.spas.jacuzzi_encrypted.packet_types import JacuzziTopsideControllerButton as B
from spa2mqtt.spas.jacuzzi_encrypted.protocol import JacuzziProtocol

VERSION = os.environ.get("ADDON_VERSION", "dev")
STATUS_STALE_S = 30         # no status update for this long -> spa link "offline"
HEARTBEAT_S = 60            # re-publish state at least this often
DIAG_S = 30
PRESS_MAP = {"pump_1": B.BTN_P1, "pump_2": B.BTN_P2, "temp_up": B.BTN_TEMP_UP, "temp_down": B.BTN_TEMP_DOWN,
             "light": B.BTN_LIGHT_ON, "light_color": B.BTN_LIGHT_COLOR}

log = logging.getLogger("spa2mqtt")


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def load_variant(name: str) -> dict:
    path = os.path.join(os.path.dirname(__file__), "spa_definitions", f"{name}.yml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_state(parsed: dict) -> dict:
    pump_1 = "Høj" if parsed.get("pump_1_high") else ("Lav" if parsed.get("pump_1_low") else "Fra")
    return {
        "water_temperature": parsed.get("current_temperature"),
        "setpoint_temperature": parsed.get("setpoint_temperature"),
        "pump_1": pump_1,
        "pump_2": bool(parsed.get("pump_2")),
        "heating": bool(parsed.get("heater")),
    }


async def main():
    logging.basicConfig(level=env("LOG_LEVEL", "info").upper(),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    variant_name = env("VARIANT_DEFINITION", "jacuzzi_j235_encrypted")
    variant = load_variant(variant_name)
    if variant.get("family") != "jacuzzi_encrypted":
        raise SystemExit(f"Variant {variant_name}: only the jacuzzi_encrypted family is supported by this runtime")
    device_id = env("DEVICE_ID", "185569045")
    loop = asyncio.get_running_loop()
    log.info("spa2mqtt %s starting (variant %s, gateway %s:%s)", VERSION, variant_name, env("SPA_IP"), env("SPA_PORT"))

    last_published = {"state": None, "at": 0.0}
    lights: dict = {}
    last_parsed: dict = {}

    def on_status(parsed: dict):
        last_parsed.clear()
        last_parsed.update(parsed)
        new = {**build_state(parsed), **lights}
        now = time.monotonic()
        if new != last_published["state"] or now - last_published["at"] >= HEARTBEAT_S:
            bridge.publish_state(new)
            last_published.update(state=new, at=now)
        bridge.set_link(True)

    def on_lights(lt: dict):
        if lt != lights:
            lights.update(lt)
            if last_parsed:
                on_status(dict(last_parsed))

    def on_command_state(cs: dict):
        bridge.publish_diagnostics(diagnostics())

    def on_link(up: bool):
        if up:
            proto.reset_link()
        else:
            bridge.set_link(False)

    conn = SpaConnection(env("SPA_IP", "192.168.0.141"), env("SPA_PORT", 8899),
                         on_frame=lambda f: proto.handle_frame(f), on_link=on_link)
    proto = JacuzziProtocol(send=conn.send, message_configuration=variant.get("message_configuration", {}),
                            on_status=on_status, on_command_state=on_command_state, on_lights=on_lights)

    async def on_command(name: str, payload: str):
        try:
            if name == "target_temperature":
                proto.set_target(float(payload))
            elif name == "press" and payload in PRESS_MAP:
                if proto.channel is None:
                    log.warning("No bus channel yet – ignoring press %s", payload)
                else:
                    proto.press(PRESS_MAP[payload])
            else:
                log.warning("Unknown command %s=%s", name, payload)
        except ValueError:
            log.warning("Bad command payload %s=%r", name, payload)
        bridge.publish_diagnostics(diagnostics())

    bridge = HaMqttBridge(env("MQTT_HOST", "core-mosquitto"), env("MQTT_PORT", 1883), env("MQTT_USERNAME"),
                          env("MQTT_PASSWORD"), device_id=device_id, device_name="Jacuzzi",
                          model=variant.get("model_name", "Jacuzzi"), version=VERSION, loop=loop,
                          on_command=on_command, discovery_prefix=env("DISCOVERY_PREFIX", "homeassistant"),
                          legacy_device_ids=[device_id])

    def diagnostics() -> dict:
        age = None if proto.status_at is None else round(time.monotonic() - proto.status_at, 1)
        return {**proto.diagnostics(), "gateway_connected": conn.connected, "frames_received": conn.frames_received,
                "frames_sent": conn.frames_sent, "reconnects": conn.reconnects, "status_age_s": age,
                "version": VERSION}

    async def watchdog():
        while True:
            await asyncio.sleep(5)
            if proto.status_at is None or time.monotonic() - proto.status_at > STATUS_STALE_S:
                bridge.set_link(False)

    async def diag_loop():
        while True:
            await asyncio.sleep(DIAG_S)
            bridge.publish_diagnostics(diagnostics())

    bridge.start()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    tasks = [asyncio.create_task(conn.run_forever()), asyncio.create_task(watchdog()),
             asyncio.create_task(diag_loop())]
    await stop.wait()
    log.info("Stopping")
    for t in tasks:
        t.cancel()
    bridge.set_link(False)
    bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())
