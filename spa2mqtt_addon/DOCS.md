# Spa2MQTT (Jacuzzi)

Connects a Jacuzzi spa with the "encrypted" RS485 protocol (tested: J235, 2018) to Home Assistant through an RS485 → Wi‑Fi
gateway (EW11A, TCP server on port 8899). Runs entirely on Home Assistant: it uses the Mosquitto broker add-on
(credentials are picked up automatically) and needs no other service.

## Options

| Option | Default | Meaning |
|---|---|---|
| `spa_host` | `192.168.0.141` | IP of the RS485 gateway (give it a DHCP reservation) |
| `spa_port` | `8899` | TCP port of the gateway |
| `variant` | `jacuzzi_j235_encrypted` | Spa definition |
| `device_id` | `185569045` | Stable id for MQTT topics and entities |
| `log_level` | `info` | `debug` logs every command |

## Entities (device "Jacuzzi")

- `climate.jacuzzi_spa` – water temperature and setpoint (18.5–40 °C on the J235, 0.5 °C steps)
- `sensor.jacuzzi_water_temperature`, `sensor.jacuzzi_target_temperature`, `number.jacuzzi_target_temperature`
- `binary_sensor.jacuzzi_circulation_pump`, `_pump_1`, `_pump_2`, `binary_sensor.jacuzzi_connected`
- `button.jacuzzi_pump_1`, `_pump_2`, `_temperature_up`, `_temperature_down`
- `sensor.jacuzzi_bridge_status` (diagnostic: bus channel, frames, reconnects, last command result, raw status bytes)

Entities become **unavailable** when the add-on is stopped or no status has arrived from the spa for 30 s – they never
show stale values.

## How setting the temperature works

The spa has no "set temperature" message, so the add-on presses TEMP UP/DOWN on the bus in bursts (up to 10 presses of
0.5 °C, 0.35 s apart, never more than needed), waits until the setpoint is stable and tops up missed presses (the first
press may only wake the display). The J235 takes about one step per second. After 3 bursts without any effect it gives
up (`bridge_status` = `failed`). Publish anything to `spa2mqtt/<device_id>/set/cancel` to stop a change. Commands are only transmitted when the spa gives this client its
turn (clear-to-send), so they don't collide with the topside panel.

The add-on gets its own bus channel from the spa (new-client request), like an extra topside panel.

## Development

If the folder `/share/spa2mqtt_dev/spa2mqtt` exists, the add-on runs that code instead of the built-in copy (copy
files to the share and restart the add-on – no rebuild). Remove the folder to go back to the built-in code.
