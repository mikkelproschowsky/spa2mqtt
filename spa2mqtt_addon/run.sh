#!/usr/bin/with-contenv bashio
# Maps the add-on options and the Mosquitto service to the environment variables spa2mqtt reads.

export SPA_IP="$(bashio::config 'spa_host')"
export SPA_PORT="$(bashio::config 'spa_port')"
export VARIANT_DEFINITION="$(bashio::config 'variant')"
export DEVICE_ID="$(bashio::config 'device_id')"
export LOG_LEVEL="$(bashio::config 'log_level')"
export ADDON_VERSION="$(bashio::addon.version)"

if ! bashio::services.available "mqtt"; then
    bashio::exit.nok "No MQTT broker found – install and start the Mosquitto broker add-on."
fi
export MQTT_HOST="$(bashio::services mqtt 'host')"
export MQTT_PORT="$(bashio::services mqtt 'port')"
export MQTT_USERNAME="$(bashio::services mqtt 'username')"
export MQTT_PASSWORD="$(bashio::services mqtt 'password')"

# Developer override: if /share/spa2mqtt_dev/spa2mqtt exists, run that code instead of the built-in copy
# (lets you test changes by copying files to the share and restarting the add-on – no rebuild).
export PYTHONPATH=/app
if [ -d /share/spa2mqtt_dev/spa2mqtt ]; then
    bashio::log.warning "Running DEVELOPMENT code from /share/spa2mqtt_dev"
    export PYTHONPATH=/share/spa2mqtt_dev
fi

bashio::log.info "Starting spa2mqtt ${ADDON_VERSION} – gateway ${SPA_IP}:${SPA_PORT}, MQTT ${MQTT_HOST}:${MQTT_PORT}"
exec python3 -m spa2mqtt.main
