# Changelog

## 1.0.0
- Home Assistant add-on packaging (Mosquitto credentials via the Supervisor; no external services).
- New runtime: reconnects to the gateway forever (back-off, 30 s silence watchdog); MQTT availability (bridge + spa
  link) so entities go unavailable instead of showing stale values; re-publishes discovery when HA restarts.
- Commands are sent only on this client's clear-to-send and are handed thread-safely from MQTT to the asyncio loop.
- Correct Balboa new-client channel request (FE BF 01 02 F1 73) instead of reusing a "dormant" channel.
- Setpoint closed loop: one press at a time, verified against the status, tolerant of the display-wake press.
- `climate` entity, Danish entity names, stable entity ids, diagnostics sensor; legacy discovery configs removed.
- Replaced ha-mqtt-discoverable with plain paho-mqtt 2.x.
