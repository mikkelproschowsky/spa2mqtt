"""MQTT bridge to Home Assistant: discovery, availability, state and commands.

- Two availability topics: the bridge itself (MQTT last will) and the spa link (data fresh). Entities use both, so
  Home Assistant shows "unavailable" instead of stale values when either is down.
- One JSON state topic; entities pick their value with a template.
- Commands arrive on paho's network thread and are handed to the asyncio loop with run_coroutine_threadsafe.
- Discovery is re-published when Home Assistant restarts (homeassistant/status = online).
- Legacy discovery configs from the old ha-mqtt-discoverable version (same device identifier) are removed once.
"""
import asyncio
import json
import logging
import threading
import time

import paho.mqtt.client as mqtt

DEVICE_SW = "spa2mqtt-addon"


class HaMqttBridge:
    def __init__(self, host, port, username, password, device_id: str, device_name: str, model: str, version: str,
                 loop: asyncio.AbstractEventLoop, on_command, discovery_prefix: str = "homeassistant",
                 legacy_device_ids=(), logger: logging.Logger = None):
        self.loop = loop
        self.on_command = on_command  # async (name, payload)
        self.prefix = discovery_prefix
        self.device_id = device_id
        self.base = f"spa2mqtt/{device_id}"
        self.t_bridge = f"{self.base}/bridge"
        self.t_link = f"{self.base}/link"
        self.t_state = f"{self.base}/state"
        self.t_diag = f"{self.base}/diagnostics"
        self.device = {"identifiers": [f"spa2mqtt_{device_id}"], "name": device_name, "manufacturer": "Jacuzzi",
                       "model": model, "sw_version": f"{DEVICE_SW} {version}"}
        self.legacy_device_ids = {str(x) for x in legacy_device_ids}
        self.logger = logger or logging.getLogger("spa2mqtt.mqtt")
        self.connected = threading.Event()
        self._legacy_done = False
        self._legacy_topics: list[str] = []
        self._last_state: dict = {}
        self._link_up = False

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"spa2mqtt-{device_id}")
        if username:
            self.client.username_pw_set(username, password or None)
        self.client.will_set(self.t_bridge, "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.host, self.port = host, int(port)

    # ---------- lifecycle ----------
    def start(self):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self):
        self.publish(self.t_bridge, "offline", retain=True)
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.logger.error("MQTT connect failed: %s", reason_code)
            return
        self.logger.info("Connected to MQTT %s:%s", self.host, self.port)
        client.subscribe(f"{self.base}/set/#")
        client.subscribe(f"{self.prefix}/status")
        if self.legacy_device_ids and not self._legacy_done:
            client.subscribe(f"{self.prefix}/+/+/config")
            client.subscribe(f"{self.prefix}/+/+/+/config")
            threading.Timer(5, self._finish_legacy_cleanup).start()
        self.publish_discovery()
        self.publish(self.t_bridge, "online", retain=True)
        self.set_link(self._link_up, force=True)
        if self._last_state:
            self.publish(self.t_state, json.dumps(self._last_state), retain=True)
        self.connected.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        self.logger.warning("MQTT disconnected (%s) – paho reconnects automatically", reason_code)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        topic, payload = msg.topic, msg.payload.decode(errors="replace")
        if topic == f"{self.prefix}/status":
            if payload == "online":
                self.logger.info("Home Assistant restarted – re-publishing discovery")
                self.publish_discovery()
                self.publish(self.t_bridge, "online", retain=True)
                self.set_link(self._link_up, force=True)
            return
        if topic.endswith("/config") and topic.startswith(self.prefix + "/"):
            self._check_legacy(topic, payload)
            return
        if topic.startswith(f"{self.base}/set/"):
            name = topic[len(f"{self.base}/set/"):]
            asyncio.run_coroutine_threadsafe(self.on_command(name, payload), self.loop)

    # ---------- legacy cleanup ----------
    def _check_legacy(self, topic, payload):
        if self._legacy_done or not payload:
            return
        try:
            cfg = json.loads(payload)
        except ValueError:
            return
        dev = cfg.get("device") or cfg.get("dev") or {}
        ids = dev.get("identifiers") or dev.get("ids") or []
        if isinstance(ids, str):
            ids = [ids]
        if self.legacy_device_ids & {str(i) for i in ids}:
            self._legacy_topics.append(topic)

    def _finish_legacy_cleanup(self):
        self._legacy_done = True
        self.client.unsubscribe(f"{self.prefix}/+/+/config")
        self.client.unsubscribe(f"{self.prefix}/+/+/+/config")
        for t in self._legacy_topics:
            self.logger.info("Removing legacy discovery config %s", t)
            self.publish(t, "", retain=True)

    # ---------- publishing ----------
    def publish(self, topic, payload, retain=False, qos=1):
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def set_link(self, up: bool, force: bool = False):
        if up != self._link_up or force:
            self._link_up = up
            self.publish(self.t_link, "online" if up else "offline", retain=True)

    def publish_state(self, state: dict):
        self._last_state = state
        self.publish(self.t_state, json.dumps(state), retain=True)

    def publish_diagnostics(self, diag: dict):
        self.publish(self.t_diag, json.dumps(diag), retain=False, qos=0)

    def publish_discovery(self):
        for component, object_id, cfg in self._entities():
            cfg = {**cfg, "unique_id": f"spa2mqtt_{self.device_id}_{object_id}", "device": self.device,
                   "default_entity_id": f"{component}.jacuzzi_{object_id}"}
            cfg.setdefault("availability", [{"topic": self.t_bridge}, {"topic": self.t_link}])
            cfg.setdefault("availability_mode", "all")
            self.publish(f"{self.prefix}/{component}/spa2mqtt_{self.device_id}/{object_id}/config", json.dumps(cfg),
                         retain=True)

    def _entities(self):
        st = self.t_state
        num = lambda key: "{{ value_json.%s if value_json.%s is defined else none }}" % (key, key)  # noqa: E731
        onoff = lambda key: "{{ 'ON' if value_json.%s else 'OFF' }}" % key  # noqa: E731
        temp = {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement",
                "suggested_display_precision": 1}
        return [
            ("climate", "spa", {
                "name": None, "icon": "mdi:hot-tub", "modes": ["heat"], "mode_state_topic": st,
                "mode_state_template": "heat",
                "current_temperature_topic": st, "current_temperature_template": num("water_temperature"),
                "temperature_state_topic": st, "temperature_state_template": num("setpoint_temperature"),
                "temperature_command_topic": f"{self.base}/set/target_temperature",
                "min_temp": 18.5, "max_temp": 40, "temp_step": 0.5, "precision": 0.5, "temperature_unit": "C"}),
            ("sensor", "water_temperature", {"name": "Vandtemperatur", "state_topic": st,
                                             "value_template": num("water_temperature"), **temp}),
            ("sensor", "target_temperature", {"name": "Måltemperatur", "state_topic": st,
                                              "value_template": num("setpoint_temperature"),
                                              "icon": "mdi:thermometer-check", **temp}),
            ("number", "target_temperature", {"name": "Sæt måltemperatur", "state_topic": st,
                                              "value_template": num("setpoint_temperature"),
                                              "command_topic": f"{self.base}/set/target_temperature",
                                              "min": 18.5, "max": 40, "step": 0.5, "mode": "box",
                                              "unit_of_measurement": "°C", "icon": "mdi:thermometer"}),
            ("binary_sensor", "circulation_pump", {"name": "Cirkulationspumpe", "state_topic": st,
                                                   "value_template": onoff("circulation_pump"),
                                                   "device_class": "running", "icon": "mdi:water-pump"}),
            ("binary_sensor", "pump_1", {"name": "Pumpe 1", "state_topic": st, "value_template": onoff("pump_1"),
                                         "device_class": "running", "icon": "mdi:pump"}),
            ("binary_sensor", "pump_2", {"name": "Pumpe 2", "state_topic": st, "value_template": onoff("pump_2"),
                                         "device_class": "running", "icon": "mdi:pump"}),
            ("binary_sensor", "connected", {"name": "Forbindelse", "state_topic": self.t_link,
                                            "payload_on": "online", "payload_off": "offline",
                                            "device_class": "connectivity", "entity_category": "diagnostic",
                                            "availability": [{"topic": self.t_bridge}]}),
            ("button", "pump_1", {"name": "Pumpe 1 (tryk)", "command_topic": f"{self.base}/set/press",
                                  "payload_press": "pump_1", "icon": "mdi:gesture-tap-button"}),
            ("button", "pump_2", {"name": "Pumpe 2 (tryk)", "command_topic": f"{self.base}/set/press",
                                  "payload_press": "pump_2", "icon": "mdi:gesture-tap-button"}),
            ("button", "temperature_up", {"name": "Temperatur op", "command_topic": f"{self.base}/set/press",
                                          "payload_press": "temp_up", "icon": "mdi:thermometer-plus"}),
            ("button", "temperature_down", {"name": "Temperatur ned", "command_topic": f"{self.base}/set/press",
                                            "payload_press": "temp_down", "icon": "mdi:thermometer-minus"}),
            ("sensor", "bridge_status", {"name": "Bro-status", "state_topic": self.t_diag,
                                         "value_template": "{{ value_json.command_result }}",
                                         "json_attributes_topic": self.t_diag, "entity_category": "diagnostic",
                                         "icon": "mdi:lan-connect", "availability": [{"topic": self.t_bridge}]}),
        ]


def wait_connected(bridge: HaMqttBridge, timeout: float = 30) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if bridge.connected.wait(1):
            return True
    return False
