"""Publish Home Assistant MQTT Discovery entries for every managed device.

**能力声明（caps）是唯一依据。** 设备在 retained 的 `switchyard/<id>/meta` 里
列出它会什么，Switchyard 就只生成对应的实体/按钮；不看 state 里碰巧有哪些字段
去猜（那样设备换个字段名、少报一次值，HA 里的实体就会飘）。

能力词表（详见 docs/PROTOCOL.md）：

  fw           sensor  固件版本
  rssi         sensor  信号强度
  uptime       sensor  运行时长
  battery      sensor  电量 %
  battery_v    sensor  电池电压 V
  water        sensor  累计水量 m³
  climate      sensor  温度 + 湿度
  leak         binary_sensor 漏水
  comm_fault   binary_sensor 通信故障
  online       binary_sensor 在线（connectivity，通常都加）
  valve        switch  阀门
  reboot       button  重启
  ota          button  检查更新
  layout       （不是实体，是插件里的「布局」面板开关）
  brightness   number  亮度

没上报 meta 的老设备，按 type 用下面的 DEFAULT_CAPS 兜底。
Discovery 配置是 retained，forget 时发空消息删除。
"""

import json

DISCOVERY_PREFIX = "homeassistant"
MANUFACTURER = "Switchyard"

# 每类设备的默认能力（设备没上报 meta 时使用）
DEFAULT_CAPS = {
    "screen": ["fw", "rssi", "uptime", "layout", "ota", "reboot", "brightness", "log", "online"],
    "meter": ["fw", "rssi", "uptime", "battery", "battery_v", "water", "climate",
              "leak", "comm_fault", "online", "valve", "ota", "reboot", "log"],
    "elink": ["fw", "rssi", "uptime", "battery", "battery_v", "climate",
              "leak", "online", "ota", "reboot"],
    "unknown": ["fw", "rssi", "uptime", "online", "ota", "reboot"],
}

# 能力 -> 实体规格。value 里的 state_topic/cmd_topic 在 _configs 里补齐。
#   kind: sensor / binary_sensor / button / number / switch
SPECS = {
    "fw": {"kind": "sensor", "name": "固件", "icon": "mdi:chip",
           "tpl": "{{ value_json.fw }}", "diagnostic": True},
    "rssi": {"kind": "sensor", "name": "信号", "unit": "dBm",
             "device_class": "signal_strength", "state_class": "measurement",
             "tpl": "{{ value_json.rssi }}", "diagnostic": True},
    "uptime": {"kind": "sensor", "name": "运行时长", "unit": "s",
               "device_class": "duration", "state_class": "total_increasing",
               "tpl": "{{ value_json.uptime }}", "diagnostic": True},
    "battery": {"kind": "sensor", "name": "电量", "unit": "%",
                "device_class": "battery", "state_class": "measurement",
                "tpl": "{{ value_json.battery }}", "diagnostic": True},
    "battery_v": {"kind": "sensor", "name": "电池电压", "unit": "V",
                  "device_class": "voltage", "state_class": "measurement",
                  "tpl": "{{ value_json.battery_v }}", "diagnostic": True,
                  "icon": "mdi:battery-outline"},
    "water": {"kind": "sensor", "name": "累计水量", "unit": "m³",
              "device_class": "water", "state_class": "total_increasing",
              "tpl": "{{ value_json.water }}", "icon": "mdi:water"},
    "climate": {"kind": "sensor", "obj": "temp", "name": "温度", "unit": "°C",
                "device_class": "temperature", "state_class": "measurement",
                "tpl": "{{ value_json.temp }}", "icon": "mdi:thermometer"},
    "humidity": {"kind": "sensor", "obj": "hum", "name": "湿度", "unit": "%",
                 "device_class": "humidity", "state_class": "measurement",
                 "tpl": "{{ value_json.hum }}", "icon": "mdi:water-percent"},
    "leak": {"kind": "binary_sensor", "name": "漏水", "device_class": "moisture",
             "bool_tpl": "leak", "icon": "mdi:water-alert"},
    "comm_fault": {"kind": "binary_sensor", "name": "通信故障", "device_class": "problem",
                   "bool_tpl": "comm_fault", "icon": "mdi:connection"},
    "valve": {"kind": "switch", "name": "阀门", "icon": "mdi:valve",
              "bool_tpl": "valve",
              "payload_on": {"cmd": "valve", "open": True},
              "payload_off": {"cmd": "valve", "open": False}},
    "reboot": {"kind": "button", "name": "重启", "icon": "mdi:restart",
               "payload": {"cmd": "reboot"}, "diagnostic": False},
    "ota": {"kind": "button", "name": "检查更新", "icon": "mdi:cloud-download",
            "payload": {"cmd": "ota"}, "diagnostic": False},
    "brightness": {"kind": "number", "name": "亮度", "unit": "%",
                   "icon": "mdi:brightness-6", "min": 10, "max": 100, "step": 5,
                   "command_template": '{"cmd":"set","key":"brightness","value":{{ value }}}'},
    "online": {"kind": "binary_sensor", "name": "在线", "device_class": "connectivity",
               "availability": True},
    "log": {"kind": "sensor", "name": "最近错误", "icon": "mdi:alert-circle-outline",
            "diag": True, "diagnostic": True},
}


def _device_block(device_id, state):
    return {
        "identifiers": ["switchyard_%s" % device_id],
        "name": state.get("name") or device_id,
        "manufacturer": MANUFACTURER,
        "model": state.get("model") or state.get("type") or "esp32",
        "sw_version": state.get("fw") or "",
    }


def _base(device_id, state, cap, spec):
    return {
        "name": spec["name"],
        "unique_id": "switchyard_%s_%s" % (device_id, cap),
        # 固定 entity_id = <device_id>_<obj>：跟着设备 id 走，不随中文设备名变拼音
        "object_id": "%s_%s" % (device_id, spec.get("obj", cap)),
        "device": _device_block(device_id, state),
        "origin": {"name": MANUFACTURER},
    }


def _configs(device_id, state, caps):
    """按能力清单生成实体，返回 [(component, object_id, payload), ...]。"""
    caps = caps or []
    state_topic = "switchyard/%s/state" % device_id
    avail_topic = "switchyard/%s/availability" % device_id
    cmd_topic = "switchyard/%s/cmd" % device_id
    out = []

    # climate 一个能力覆盖"温度 + 湿度"两个实体
    if "climate" in caps and "humidity" not in caps:
        caps = list(caps) + ["humidity"]

    for cap in caps:
        spec = SPECS.get(cap)
        if spec is None:
            continue          # 未知能力：忽略（设备可以比插件新）
        kind = spec["kind"]
        cfg = _base(device_id, state, cap, spec)
        cfg["icon"] = spec.get("icon", cfg.get("icon", ""))
        if spec.get("diagnostic"):
            cfg["entity_category"] = "diagnostic"

        if spec.get("diag"):
            cfg.update({
                "state_topic": "switchyard/%s/diag" % device_id,
                "value_template": "{{ value_json.line }}",
            })
        elif kind in ("sensor", "binary_sensor") and spec.get("availability"):
            cfg.update({
                "state_topic": avail_topic,
                "payload_on": "online",
                "payload_off": "offline",
                "device_class": spec["device_class"],
            })
        elif kind == "sensor":
            cfg.update({"state_topic": state_topic, "value_template": spec["tpl"]})
            for key in ("unit", "device_class", "state_class"):
                if spec.get(key):
                    cfg["unit_of_measurement" if key == "unit" else key] = spec[key]
        elif kind == "binary_sensor":
            cfg.update({
                "state_topic": state_topic,
                "value_template": "{{ 'ON' if value_json.%s else 'OFF' }}" % spec["bool_tpl"],
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": spec["device_class"],
            })
        elif kind == "switch":
            cfg.update({
                "state_topic": state_topic,
                "value_template": "{{ 'ON' if value_json.%s else 'OFF' }}" % spec["bool_tpl"],
                "command_topic": cmd_topic,
                "payload_on": json.dumps(spec["payload_on"]),
                "payload_off": json.dumps(spec["payload_off"]),
                "state_on": "ON",
                "state_off": "OFF",
            })
        elif kind == "button":
            cfg.update({
                "command_topic": cmd_topic,
                "payload_press": json.dumps(spec["payload"]),
            })
        elif kind == "number":
            cfg.update({
                "command_topic": cmd_topic,
                "command_template": spec["command_template"],
                "min": spec["min"],
                "max": spec["max"],
                "step": spec["step"],
                "unit_of_measurement": spec["unit"],
            })

        if not cfg.get("icon"):
            cfg.pop("icon", None)
        out.append((kind, spec.get("obj", cap), cfg))

    return out


def publish_device(mqtt, device_id, state, caps=None):
    """发布（retained）某台设备的全部实体配置，返回条数。

    设备改了 caps（比如以前声明过 battery，现在不声明）时，把不再声明的
    实体发布成空 retained，否则 HA 里会留下永远 unknown 的孤儿实体。
    """
    if mqtt is None or not mqtt.connected:
        return 0

    declared = set(caps or [])
    for cap, spec in SPECS.items():
        if cap in declared:
            continue
        obj = spec.get("obj", cap)
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, spec["kind"], device_id, obj)
        mqtt.publish(topic, "", retain=True)

    sent = 0
    for component, obj, cfg in _configs(device_id, state, caps):
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, component, device_id, obj)
        if mqtt.publish(topic, json.dumps(cfg, ensure_ascii=False), retain=True):
            sent += 1
    return sent


def remove_device(mqtt, device_id, caps=None):
    """删除某台设备的全部实体配置（空 retained）。"""
    if mqtt is None or not mqtt.connected:
        return 0
    removed = 0
    # 用全量能力清，避免设备改过 caps 后留下孤儿实体
    for component, obj, _cfg in _configs(device_id, {}, list(SPECS.keys())):
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, component, device_id, obj)
        if mqtt.publish(topic, "", retain=True):
            removed += 1
    return removed
