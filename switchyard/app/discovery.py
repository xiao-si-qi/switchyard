"""Publish Home Assistant MQTT Discovery entries for every managed device.

The device only publishes `switchyard/<id>/state`; Switchyard translates that
into a set of HA entities (one HA device per managed device):

  sensor        firmware / rssi / uptime / battery
  binary_sensor online
  button        reboot / update (check for firmware)

Discovery configs are retained, so they survive HA restarts. They are removed
(published empty) when the device is forgotten.
"""

import json

DISCOVERY_PREFIX = "homeassistant"
MANUFACTURER = "Switchyard"


def _device_block(device_id, state):
    return {
        "identifiers": ["switchyard_%s" % device_id],
        "name": device_id,
        "manufacturer": MANUFACTURER,
        "model": state.get("type") or "esp32",
        "sw_version": state.get("fw") or "",
    }


def _common(device_id, state, name, uid_suffix, component):
    return {
        "name": name,
        "unique_id": "switchyard_%s_%s" % (device_id, uid_suffix),
        "device": _device_block(device_id, state),
        "origin": {"name": MANUFACTURER},
    }


def _configs(device_id, state):
    """Return [(component, object_id, payload_dict), ...] for one device."""
    state_topic = "switchyard/%s/state" % device_id
    avail_topic = "switchyard/%s/availability" % device_id
    cmd_topic = "switchyard/%s/cmd" % device_id
    out = []

    def add(component, obj, cfg):
        out.append((component, obj, cfg))

    cfg = _common(device_id, state, "固件", "fw", "sensor")
    cfg.update({"state_topic": state_topic, "value_template": "{{ value_json.fw }}", "icon": "mdi:chip"})
    add("sensor", "fw", cfg)

    cfg = _common(device_id, state, "信号", "rssi", "sensor")
    cfg.update({
        "state_topic": state_topic,
        "value_template": "{{ value_json.rssi }}",
        "unit_of_measurement": "dBm",
        "device_class": "signal_strength",
        "state_class": "measurement",
        "entity_category": "diagnostic",
    })
    add("sensor", "rssi", cfg)

    cfg = _common(device_id, state, "运行时长", "uptime", "sensor")
    cfg.update({
        "state_topic": state_topic,
        "value_template": "{{ value_json.uptime }}",
        "unit_of_measurement": "s",
        "device_class": "duration",
        "state_class": "total_increasing",
        "entity_category": "diagnostic",
    })
    add("sensor", "uptime", cfg)

    cfg = _common(device_id, state, "电量", "battery", "sensor")
    cfg.update({
        "state_topic": state_topic,
        "value_template": "{{ value_json.battery }}",
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "entity_category": "diagnostic",
    })
    add("sensor", "battery", cfg)

    cfg = _common(device_id, state, "在线", "online", "binary_sensor")
    cfg.update({
        "state_topic": avail_topic,
        "payload_on": "online",
        "payload_off": "offline",
        "device_class": "connectivity",
    })
    add("binary_sensor", "online", cfg)

    cfg = _common(device_id, state, "重启", "reboot", "button")
    cfg.update({
        "command_topic": cmd_topic,
        "payload_press": json.dumps({"cmd": "reboot"}),
        "icon": "mdi:restart",
        "entity_category": "config",
    })
    add("button", "reboot", cfg)

    cfg = _common(device_id, state, "检查更新", "update", "button")
    cfg.update({
        "command_topic": cmd_topic,
        "payload_press": json.dumps({"cmd": "ota"}),
        "icon": "mdi:cloud-download",
        "entity_category": "config",
    })
    add("button", "update", cfg)

    return out


def publish_device(mqtt, device_id, state):
    """Publish (retained) all discovery configs for one device. Returns count."""
    if mqtt is None or not mqtt.connected:
        return 0
    sent = 0
    for component, obj, cfg in _configs(device_id, state):
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, component, device_id, obj)
        if mqtt.publish(topic, json.dumps(cfg, ensure_ascii=False), retain=True):
            sent += 1
    return sent


def remove_device(mqtt, device_id):
    """Drop every discovery entry of a device (empty retained payload)."""
    if mqtt is None or not mqtt.connected:
        return 0
    removed = 0
    for component, obj, _cfg in _configs(device_id, {}):
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, component, device_id, obj)
        if mqtt.publish(topic, "", retain=True):
            removed += 1
    return removed
