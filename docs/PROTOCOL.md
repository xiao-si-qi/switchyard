# Switchyard 设备协议（v0.1 草案）

一套给"自制 ESP32 设备 + Home Assistant"用的最小 MQTT 约定。
目标：新设备照抄这几个主题和字段就能被 Switchyard 管理，不需要改上位机代码。

## 1. 命名

- `device_id`：小写、无空格，建议 `<房间>-<类型>`，例如 `hall-screen`、`kitchen-elink`、`inlet-water`；
- 所有主题以 `orbis/` 为根（历史原因，保留；设备不分品牌都用这一个根）；
- 设备**必须**在连上 broker 时发布一次 `state`（retained）和 `availability=online`（retained），
  并用 LWT 把 `availability` 设成 `offline`。

## 2. 主题表

| 主题 | 方向 | retain | 说明 |
|---|---|---|---|
| `orbis/<id>/state` | 设备 → HA | ✅ | JSON 状态快照，见 §3 |
| `orbis/<id>/availability` | 设备 → HA | ✅ | `online` / `offline`，用 LWT 维护 |
| `orbis/<id>/event` | 设备 → HA | ❌ | JSON 事件，见 §4 |
| `orbis/<id>/cmd` | Switchyard → 设备 | ✅ | JSON 命令，见 §5 |
| `orbis/<id>/cfg` | Switchyard → 设备 | ✅ | **配置下发（布局 JSON）**，见 §5.1 |
| `orbis/<id>/ack` | 设备 → HA | ❌ | `{"seq":N,"ok":true,"err":"..."}` |
| `orbis/<id>/meta` | 设备 → HA | ✅ | 静态能力声明，见 §6（可选） |

## 3. state 快照

```json
{
  "id": "hall-screen",
  "type": "screen",
  "fw": "0.1.1",
  "ip": "192.168.1.38",
  "rssi": -72,
  "uptime": 8123,
  "heap": 128000,
  "battery": null,
  "ts": 1730990000
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 与主题里的 `<id>` 一致 |
| `type` | ✅ | `screen` / `elink` / `sensor` / `meter` …（决定 Switchyard 展示哪套卡片） |
| `fw` | ✅ | 固件版本，与 OTA 比较用 |
| `rssi` `ip` `uptime` `heap` | 可选 | 在线设备建议都发 |
| `battery` | 可选 | 电池设备发百分比，插电设备留 `null` |
| `ts` | ✅ | Unix 秒，用来判断"最后上报时间" |

上报频率：常在线设备 30~60 s；电池设备每次唤醒发一次。

## 4. event

```json
{"ev":"ota_progress","seq":12,"pct":42}
{"ev":"ota_done","seq":12}
{"ev":"ota_failed","seq":12,"err":"http 404"}
{"ev":"button","key":"boot","press":"long"}
```

## 5. cmd

```json
{"seq":12,"cmd":"ota","url":"https://ha.example.com/local/orbis/fw/screen-0.2.0.bin","sha256":"..."}
{"seq":13,"cmd":"layout","url":"https://ha.example.com/local/orbis/layout/hall.json"}
{"seq":14,"cmd":"reboot"}
{"seq":15,"cmd":"set","key":"brightness","value":60}
```

**seq 语义（关键）**

- Switchyard 维护每个设备的 `seq` 单调递增；
- 命令以 **retained** 方式发布，设备上线/唤醒后订阅到最新一条；
- 设备本地保存"已执行的最后 seq"，**只执行 seq 更大的命令**，执行完发 `ack`，再清掉 retained（`retain=true, payload=""`）；
- 这样"发命令时设备在睡觉"也不会丢：醒来第一件事就是拉到它。

### 5.1 cfg：配置直接下发（推荐）

布局这类"结构性配置"不走 cmd，而是单独一条 retained 主题：

```
orbis/<id>/cfg      payload = 布局 JSON（和 /local/orbis/layout.json 同一格式）
```

- Switchyard 保存布局时 **retained 发布**到该设备专属的 `cfg` 主题；
- 设备收到 → 解析 → 应用界面 → **存 NVS**（重启后先用 NVS 里的这份）；
- 设备回 `event: {"ev":"cfg_applied"}`；
- 新设备第一次上线会立刻收到 broker 上保留的最后一份，不需要等到下次下发；
- 好处：**按设备寻址**（同款屏可以有 N 个不同布局）、**主动推送**、**配置不出公网**
  （不再需要把 JSON 放到 `www` 静态目录）。

设备的优先级：`NVS 里的 cfg` → `HTTP /local/orbis/layout.json` → 自动发现兜底。

## 6. meta（能力声明，可选）

```json
{
  "type": "screen",
  "caps": ["layout", "ota", "brightness"],
  "display": {"w": 360, "h": 360},
  "partition": {"ota": true}
}
```

Switchyard 用它决定显示哪些按钮（有没有 OTA、能否下发布局）。

## 7. OTA 流程

```
Switchyard                     设备
    │ 上传 firmware.bin+json      │
    │（/config/www/orbis/fw/…）   │
    │ ── cmd:ota(url,sha256) ───▶ │ 下载 → 校验 → 写非活动槽
    │ ◀── event: ota_progress ─── │ （可选，设备支持就发）
    │ ◀── event: ota_done ─────── │ 切槽重启
    │ ◀── state(fw=新版本) ────── │ 上线后确认
```

约束：

- 固件放 HTTP 静态目录，MQTT 只传 URL，避免 2.5 MB 走 broker；
- 设备写**非活动槽**，断电不影响当前固件；启用 bootloader rollback；
- `sha256` 可选，设备实现校验更佳。

## 8. Home Assistant 侧

- 设备实体仍用标准 MQTT Discovery（`homeassistant/<component>/<node>/<object>/config`），
  这样 HA 的仪表盘、自动化、语音都能直接用；
- Switchyard 只订阅 `orbis/+/state|availability|event`，**不代替** discovery；
- 没有 MQTT 的设备（比如现在走 HTTP 的圆屏）可以先保留 HTTP，Switchyard 按设备类型兼容。

## 9. 版本演进

- v0.1（本文件）：state / availability / cmd / ack / event；
- 后续新增字段必须可选，设备可忽略未知字段；
- 破坏性变更走 `orbis/v2/...` 新根，不原地改语义。
