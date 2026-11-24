"""Publish Home Assistant MQTT Discovery entries for every managed device.

**两套来源，职责分开：**

1. `caps`（能力）——**只放需要 Switchyard 主动操作的**能力：
   阀门、重启、OTA、亮度、布局…… 这些要生成 switch / button / number 实体，
   或者只决定插件界面显示哪些面板。

2. `meta.metrics`（指标）——**只读遥测**：IP、CPU、内存、磁盘、电量、水量……
   设备只声明"我有哪些值、叫什么、单位是什么"，由这里统一生成 sensor。
   这样设备不需要懂 HA 的 discovery 格式，`SPECS` 词表也不用为每台新设备扩张。

兼容：没有 `metrics` 的老设备继续走 caps → SPECS 的老路径，行为不变。

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
import re

DISCOVERY_PREFIX = "homeassistant"
MANUFACTURER = "Switchyard"

# metrics 的 key 会进 MQTT 主题和 entity_id，必须限定字符集（同 DEVICE_ID_RE 的理由）
METRIC_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")

# 每台设备上「上一次发布过哪些 metric 实体」，用来清理设备不再声明的孤儿实体
_METRIC_SEEN = {}

# 每台设备上「上一次发布过哪些 cap」，用来精确清理，避免每次全量清 13 条
_CAP_SEEN = {}


def _entity_caps():
    """所有会生成实体的 cap（排除 background/rotation/voice 这类纯 UI 能力）。"""
    return [cap for cap, spec in SPECS.items() if spec.get("kind") != "none"]

# 每类设备的默认能力（设备没上报 meta 时使用）
DEFAULT_CAPS = {
    "screen": ["fw", "rssi", "uptime", "layout", "background", "rotation", "voice", "ota", "reboot",
               "brightness", "log", "online"],
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
    "brightness": {"kind": "number", "name": "亮度", "unit": "%", "tpl_field": "brightness",
                   "icon": "mdi:brightness-6", "min": 10, "max": 100, "step": 5,
                   "command_template": '{"cmd":"set","key":"brightness","value":{{ value }}}'},
    "online": {"kind": "binary_sensor", "name": "在线", "device_class": "connectivity",
               "availability": True},
    # 这些能力不生成实体，只决定设备页显示哪些配置面板
    "background": {"kind": "none"},
    "rotation": {"kind": "none"},
    "voice": {"kind": "none"},

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
        if kind == "none":
            continue          # 纯 UI 能力（例如背景图配置）
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
                "state_topic": state_topic,
                "value_template": "{{ value_json.%s }}" % spec.get("tpl_field", "value"),
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


def _metric_configs(device_id, state, metrics):
    """把设备声明的 meta.metrics 转成 HA sensor 配置，返回 [(object, payload), ...]。

    metric 字段（都来自设备，插件不猜）：

        key          必填，state JSON 里的字段名（也做 object_id 后缀）
        name         显示名，缺省用 key
        unit         单位，有单位就带 state_class=measurement
        icon         mdi 图标
        device_class HA 的 device_class（可省）
        object       覆盖 object_id 后缀（迁移时用来保持 entity_id 不变）
        category     diagnostic / config，缺省 diagnostic
    """
    out = []
    state_topic = "switchyard/%s/state" % device_id
    for m in metrics or []:
        if not isinstance(m, dict):
            continue
        key = str(m.get("key") or "").strip()
        if not METRIC_KEY_RE.match(key):
            # 非法 key 直接跳过：可能是设备写错了，也可能是主题注入尝试
            continue
        obj = str(m.get("object") or key).strip()
        if not METRIC_KEY_RE.match(obj):
            obj = key
        cfg = {
            "name": m.get("name") or key,
            # unique_id 和 caps 路径用同一套命名，保证迁移时实体不变
            "unique_id": "switchyard_%s_%s" % (device_id, obj),
            "object_id": "%s_%s" % (device_id, obj),
            "state_topic": state_topic,
            "value_template": "{{ value_json.%s }}" % key,
            "device": _device_block(device_id, state),
            "origin": {"name": MANUFACTURER},
        }
        if m.get("unit"):
            cfg["unit_of_measurement"] = m["unit"]
            cfg["state_class"] = m.get("state_class") or "measurement"
        if m.get("icon"):
            cfg["icon"] = m["icon"]
        if m.get("device_class"):
            cfg["device_class"] = m["device_class"]
        category = m.get("category", "diagnostic")
        if category:
            cfg["entity_category"] = category
        out.append((obj, cfg))
    return out


def publish_device(mqtt, device_id, state, caps=None, metrics=None):
    """发布（retained）某台设备的全部实体配置，返回条数。

    caps 走 SPECS 词表（可操作能力 + 老固件的只读能力），
    metrics 走设备自声明（只读遥测）。两条路径互不干扰。

    设备不再声明的 cap 或 metric，会发空 retained 删掉对应实体，
    否则 HA 里会留下永远 unknown 的孤儿实体。
    """
    if mqtt is None or not mqtt.connected:
        return 0

    declared = set(caps or [])

    # 清理不再声明的 cap 实体。
    # 第一次见到这台设备时不知道 broker 上残留了什么，全量清一遍；
    # 之后只清「上次声明过、这次没声明」的，避免每轮发布都发十几条空消息。
    previous = _CAP_SEEN.get(device_id)
    stale = (set(_entity_caps()) - declared) if previous is None else (previous - declared)
    for cap in stale:
        spec = SPECS.get(cap)
        if spec is None or spec.get("kind") == "none":
            continue
        obj = spec.get("obj", cap)
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, spec["kind"], device_id, obj)
        mqtt.publish(topic, "", retain=True)
    _CAP_SEEN[device_id] = declared

    sent = 0
    for component, obj, cfg in _configs(device_id, state, caps):
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, component, device_id, obj)
        if mqtt.publish(topic, json.dumps(cfg, ensure_ascii=False), retain=True):
            sent += 1

    # ---- metrics（只读遥测）----
    metric_cfgs = _metric_configs(device_id, state, metrics)
    current = set()
    for obj, cfg in metric_cfgs:
        current.add(obj)
        topic = "%s/sensor/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, device_id, obj)
        if mqtt.publish(topic, json.dumps(cfg, ensure_ascii=False), retain=True):
            sent += 1

    # 清掉这台设备上次发布过、这次不再声明的 metric 实体
    previous = _METRIC_SEEN.get(device_id, set())
    for obj in previous - current:
        topic = "%s/sensor/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, device_id, obj)
        mqtt.publish(topic, "", retain=True)
    _METRIC_SEEN[device_id] = current

    return sent


def remove_device(mqtt, device_id, caps=None):
    """删除某台设备的全部实体配置（空 retained）。"""
    if mqtt is None or not mqtt.connected:
        return 0
    removed = 0
    # 清掉这台设备曾经声明过的 cap 实体
    past = _CAP_SEEN.pop(device_id, set(_entity_caps()))
    for cap in past:
        spec = SPECS.get(cap)
        if spec is None or spec.get("kind") == "none":
            continue
        obj = spec.get("obj", cap)
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, spec["kind"], device_id, obj)
        if mqtt.publish(topic, "", retain=True):
            removed += 1
    # 这台设备声明过的 metric 实体也要清
    for obj in _METRIC_SEEN.pop(device_id, set()):
        topic = "%s/sensor/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, device_id, obj)
        if mqtt.publish(topic, "", retain=True):
            removed += 1
    return removed
