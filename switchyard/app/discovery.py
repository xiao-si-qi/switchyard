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


# 已废弃的只读 cap：v0.2 起改由设备的 meta.metrics 声明。
# 升级时要把它们残留在 broker 上的实体配置清掉，否则 HA 里会留下永不更新的孤儿实体。
# 值是该 cap 原来生成的 HA 组件（climate 生成 temp，humidity 生成 hum）。
LEGACY_CAPS = {
    "fw": "sensor",
    "rssi": "sensor",
    "uptime": "sensor",
    "battery": "sensor",
    "battery_v": "sensor",
    "water": "sensor",
    "temp": "sensor",
    "hum": "sensor",
    "leak": "binary_sensor",
    "comm_fault": "binary_sensor",
    "log": "sensor",
}


def _cap_entity(cap):
    """cap -> (HA 组件, object_id 后缀)。同时覆盖现行 SPECS 和已废弃的 LEGACY_CAPS。"""
    spec = SPECS.get(cap)
    if spec is not None and spec.get("kind") not in (None, "none"):
        return spec["kind"], spec.get("obj", cap)
    legacy = LEGACY_CAPS.get(cap)
    if legacy:
        return legacy, cap
    return None


def _clearable_caps():
    """所有可能在 broker 上留下实体配置的 cap（现行 + 废弃）。"""
    return set(SPECS.keys()) | set(LEGACY_CAPS.keys())


def _entity_caps():
    """现行 SPECS 里会生成实体的 cap（排除纯 UI 能力）。"""
    return [cap for cap, spec in SPECS.items() if spec.get("kind") != "none"]

# 每类设备的默认能力（设备没上报 meta 时使用）。
# 注意：这里**只列可操作能力和纯 UI 能力**——只读遥测已经归 meta.metrics，
# 不会再从 caps 兜底，否则新规则形同虚设。
DEFAULT_CAPS = {
    "screen": ["layout", "background", "rotation", "voice", "ota", "reboot", "brightness"],
    "meter": ["valve", "ota", "reboot"],
    "elink": ["ota", "reboot"],
    "unknown": ["ota", "reboot"],
}

# 能力 -> 实体规格。**只放需要 Switchyard 主动操作的能力**，
# 以及只影响插件界面、不生成实体的纯 UI 能力。
# 只读遥测（固件/信号/运行时长/电量/水量/温湿度/漏水/通信故障）请走 meta.metrics。
#
#   kind: button / number / switch / none（none = 只影响插件界面）
SPECS = {
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
    # 纯 UI 能力：不生成实体，只决定设备页显示哪些配置面板
    "layout": {"kind": "none"},
    "background": {"kind": "none"},
    "rotation": {"kind": "none"},
    "voice": {"kind": "none"},
}

# 在线状态是设备的固有属性（由 availability 主题 + LWT 维护），
# 不需要设备声明，也不该挤在 caps 里。
ALWAYS_ENTITY = "online"


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

    # 在线状态：固有属性，不由 caps 声明，永远生成（走 availability + LWT）
    out.append(("binary_sensor", ALWAYS_ENTITY, {
        "name": "在线",
        "unique_id": "switchyard_%s_%s" % (device_id, ALWAYS_ENTITY),
        "object_id": "%s_%s" % (device_id, ALWAYS_ENTITY),
        "device": _device_block(device_id, state),
        "origin": {"name": MANUFACTURER},
        "state_topic": avail_topic,
        "payload_on": "online",
        "payload_off": "offline",
        "device_class": "connectivity",
    }))

    return out


def _metric_configs(device_id, state, metrics):
    """把设备声明的 meta.metrics 转成 HA 实体配置，返回 [(component, object, payload), ...]。

    metric 字段（都来自设备，插件不猜）：

        key          必填，JSON 里的字段名（也做 object_id 后缀）
        name         显示名，缺省用 key
        kind         sensor（默认）或 binary_sensor
        unit         单位，有单位就带 state_class=measurement
        icon         mdi 图标
        device_class HA 的 device_class（可省，binary_sensor 建议给）
        state_class  覆盖默认的 measurement
        object       覆盖 object_id 后缀（迁移时用来保持 entity_id 不变）
        category     diagnostic / config，缺省 diagnostic
        source       state（默认）或 diag —— 值从 state 还是 diag 主题取
    """
    out = []
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
        kind = str(m.get("kind") or "sensor").lower()
        if kind not in ("sensor", "binary_sensor"):
            kind = "sensor"
        source = str(m.get("source") or "state").lower()
        if source == "diag":
            state_topic = "switchyard/%s/diag" % device_id
        else:
            source = "state"
            state_topic = "switchyard/%s/state" % device_id
        cfg = {
            "name": m.get("name") or key,
            # unique_id 和 caps 路径用同一套命名，保证迁移时实体不变
            "unique_id": "switchyard_%s_%s" % (device_id, obj),
            "object_id": "%s_%s" % (device_id, obj),
            "state_topic": state_topic,
            "device": _device_block(device_id, state),
            "origin": {"name": MANUFACTURER},
        }
        if kind == "binary_sensor":
            # 布尔值：设备给 true/false/0/1 都行，统一映射成 ON/OFF
            cfg["value_template"] = "{{ 'ON' if value_json.%s else 'OFF' }}" % key
            cfg["payload_on"] = "ON"
            cfg["payload_off"] = "OFF"
        else:
            cfg["value_template"] = "{{ value_json.%s }}" % key
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
        out.append((kind, obj, cfg))
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
    # 第一次见到这台设备时不知道 broker 上残留了什么（包括旧版本发布、
    # 现在已废弃的只读 cap），全量清一遍；之后只清「上次声明过、这次没声明」的。
    previous = _CAP_SEEN.get(device_id)
    stale = (_clearable_caps() - declared) if previous is None else (previous - declared)
    for cap in stale:
        target = _cap_entity(cap)
        if target is None:
            continue
        component, obj = target
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, component, device_id, obj)
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
    for component, obj, cfg in metric_cfgs:
        current.add((component, obj))
        topic = "%s/%s/switchyard_%s/%s/config" % (
            DISCOVERY_PREFIX, component, device_id, obj)
        if mqtt.publish(topic, json.dumps(cfg, ensure_ascii=False), retain=True):
            sent += 1

    # 清掉这台设备上次发布过、这次不再声明的 metric 实体
    previous = _METRIC_SEEN.get(device_id, set())
    for component, obj in previous - current:
        topic = "%s/%s/switchyard_%s/%s/config" % (
            DISCOVERY_PREFIX, component, device_id, obj)
        mqtt.publish(topic, "", retain=True)
    _METRIC_SEEN[device_id] = current

    return sent


def remove_device(mqtt, device_id, caps=None):
    """删除某台设备的全部实体配置（空 retained）。"""
    if mqtt is None or not mqtt.connected:
        return 0
    removed = 0
    # 清掉这台设备曾经声明过的 cap 实体（含已废弃的只读 cap）
    past = _CAP_SEEN.pop(device_id, None)
    for cap in (past if past is not None else _clearable_caps()):
        target = _cap_entity(cap)
        if target is None:
            continue
        component, obj = target
        topic = "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, component, device_id, obj)
        if mqtt.publish(topic, "", retain=True):
            removed += 1
    # 这台设备声明过的 metric 实体也要清（_METRIC_SEEN 存的是 (component, obj)）
    for component, obj in _METRIC_SEEN.pop(device_id, set()):
        topic = "%s/%s/switchyard_%s/%s/config" % (
            DISCOVERY_PREFIX, component, device_id, obj)
        if mqtt.publish(topic, "", retain=True):
            removed += 1
    # 固有属性（在线状态）也要清
    topic = "%s/binary_sensor/switchyard_%s/%s/config" % (
        DISCOVERY_PREFIX, device_id, ALWAYS_ENTITY)
    if mqtt.publish(topic, "", retain=True):
        removed += 1
    return removed
