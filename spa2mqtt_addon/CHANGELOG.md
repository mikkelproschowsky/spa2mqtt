# Changelog

## 1.0.6
- Setpoint changes in bursts (up to 10 presses, 0.35 s apart), then verified and topped up – never more presses than
  needed, so no overshoot. The J235 itself takes ~1 step (0.5 °C) per second: 38.5 → 18.5 °C now ~40 s (was > 70 s).
- New command `spa2mqtt/<id>/set/cancel` stops an ongoing setpoint change (Home Assistant sends it on "manual").

## 1.0.5
- Lights: `binary_sensor.jacuzzi_light` "Lys" from the lights packet (0xCA, data[4] bit 7). Two light buttons (codes 241
  and 242) with neutral names until their effect on the J235 panel is confirmed. Lights packet in diagnostics.

## 1.0.4
- Pumps and heater decoded from measurements on a J235: `sensor.jacuzzi_pump_1` (Fra/Lav/Høj – byte 2 bits 3/4),
  `binary_sensor.jacuzzi_pump_2` (byte 1 bit 2), `binary_sensor.jacuzzi_heater` "Varmelegeme" (byte 10 bit 6).
  The old "circulation pump" was byte 1 bit 6, which just blinks (panel LED) – removed with the old pump_1 binary.
- Climate: HVAC modes auto/heat/off, presets Eco/Klar/Vinter (state and commands on spa2mqtt/<id>/ha/..., owned by
  Home Assistant automations), and hvac_action heating/idle from the heater bit.

## 1.0.3
- The development override (/share/spa2mqtt_dev) now really takes effect (run from the code directory; `python -m` put /app first).

## 1.0.2
- Setpoint range 18.5–40 °C (the J235 does not go lower; measured).

## 1.0.1
- run.sh uses `with-contenv` (the base image runs s6; without it the script had no Supervisor token and could not read options or the MQTT service).

## 1.0.0
- Home Assistant add-on packaging (Mosquitto credentials via the Supervisor; no external services).
- New runtime: reconnects to the gateway forever (back-off, 30 s silence watchdog); MQTT availability (bridge + spa
  link) so entities go unavailable instead of showing stale values; re-publishes discovery when HA restarts.
- Commands are sent only on this client's clear-to-send and are handed thread-safely from MQTT to the asyncio loop.
- Correct Balboa new-client channel request (FE BF 01 02 F1 73) instead of reusing a "dormant" channel.
- Setpoint closed loop: one press at a time, verified against the status, tolerant of the display-wake press.
- `climate` entity, Danish entity names, stable entity ids, diagnostics sensor; legacy discovery configs removed.
- Replaced ha-mqtt-discoverable with plain paho-mqtt 2.x.
