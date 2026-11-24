"""Home Assistant MQTT Discovery —— 只负责**清理**，不负责生成。

## 为什么插件不再生成实体

早期版本由插件读 `meta.caps` / `meta.metrics`，再替设备发布 discovery。问题是
**插件被放进了实体注册的数据路径**：

- 插件挂了的时候新加的设备，在 HA 里完全不出现；
- 插件升级带 bug，可能误清别人的实体；
- 想加一个新指标，得改插件 + 重新构建加载项。

v0.3 起改成**设备自己发布 discovery**（标准 MQTT Discovery），插件退出这条路径：

    设备 ──pub──▶ switchyard/<id>/state ──▶ HA（直接读，不经过插件）
                        └───────────────▶ 插件（只读，做台账）

插件挂了、升级坏了，HA 的实体和自动化都不受影响。

## 分工

| 能力 | 归属 | 协议 |
|---|---|---|
| 传感器 / 开关 / 按钮 / 数字（HA 能直接表达） | 设备 | HA MQTT Discovery |
| 台账、OTA 分发、布局下发、批量配置 | 插件 | switchyard/<id>/... |

## 插件还保留什么

只有一件管理动作：**删除设备时清掉它声明过的实体**。设备的实体分散在
`homeassistant/...` 下，插件不知道有哪些，所以设备要在 `meta.ha_entities` 里自报：

    "ha_entities": [
        {"component": "sensor",        "object": "water"},
        {"component": "binary_sensor", "object": "leak"},
        {"component": "button",        "object": "reboot"}
    ]

主题按 HA 的约定拼：`homeassistant/<component>/switchyard_<id>/<object>/config`。
"""

DISCOVERY_PREFIX = "homeassistant"


def entity_topic(component, device_id, obj):
    """该设备某个实体的 discovery 配置主题。"""
    return "%s/%s/switchyard_%s/%s/config" % (DISCOVERY_PREFIX, component, device_id, obj)


def cleanup_entities(mqtt, device_id, ha_entities):
    """按设备自报的 ha_entities 清空 retained 配置，返回清除条数。"""
    if mqtt is None or not mqtt.connected:
        return 0
    removed = 0
    for ent in ha_entities or []:
        if not isinstance(ent, dict):
            continue
        component = str(ent.get("component") or "").strip()
        obj = str(ent.get("object") or "").strip()
        if not component or not obj:
            continue
        if mqtt.publish(entity_topic(component, device_id, obj), "", retain=True):
            removed += 1
    return removed
