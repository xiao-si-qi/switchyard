# Switchyard 设备协议

一套给"自制 ESP32 设备 + Home Assistant"用的最小 MQTT 约定。
目标：新设备照抄这几个主题和字段就能被 Switchyard 管理，不需要改上位机代码。

## 1. 命名

- `device_id`：小写、无空格，建议 `<房间>-<类型>`，例如 `hall-screen`、`kitchen-elink`、`inlet-water`；
- 所有主题以 `switchyard/` 为根（历史原因，保留；设备不分品牌都用这一个根）；
- 设备**必须**在连上 broker 时发布一次 `state`（retained）和 `availability=online`（retained），
  并用 LWT 把 `availability` 设成 `offline`。

## 2. 主题表

分两组：**设备自己维护的 HA 实体**走标准 MQTT Discovery，**插件独有能力**才走
`switchyard/` 下的命令主题。

### 2.1 数据与 HA 实体（设备负责）

| 主题 | 方向 | retain | 说明 |
|---|---|---|---|
| `homeassistant/<comp>/switchyard_<id>/<obj>/config` | 设备 → HA | ✅ | **HA 实体声明**，标准 MQTT Discovery，见 §8 |
| `switchyard/<id>/state` | 设备 → HA | ✅ | JSON 状态快照，见 §3；HA 实体直接读它 |
| `switchyard/<id>/availability` | 设备 → HA | ✅ | `online` / `offline`，用 LWT 维护 |
| `switchyard/<id>/event` | 设备 → | ❌ | JSON 事件，见 §4 |
| `switchyard/<id>/diag` | 设备 → | ✅ | 错误摘要（≤1KB，限流 1 条/分钟）|
| `switchyard/<id>/log` | 设备 → | ❌ | 按需回传最近 N 行日志 |
| `switchyard/<id>/meta` | 设备 → 插件 | ✅ | 设备声明（caps / metrics / ha_entities），见 §6 |

### 2.2 控制与配置下行

| 主题 | 方向 | retain | 说明 |
|---|---|---|---|
| `switchyard/<id>/ha/<obj>/set` | HA → 设备 | ❌ | HA 的控制（阀门、按钮、数字），纯值 payload，见 §8.3 |
| `switchyard/<id>/cmd` | 插件 → 设备 | ❌ | 插件独有的 JSON 命令（`get`/`set`/OTA 等），带 `seq` 去重，见 §5 |
| `switchyard/<id>/cfg` | 插件 → 设备 | ✅ | 配置下发（布局 JSON），见 §5.1 |
| `switchyard/<id>/ack` | 设备 → 插件 | ❌ | `{"seq":N,"ok":true,"err":"..."}` |

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

## 4.5 背景图轮播（可选字段）

布局 JSON 里可以带一个 `bg` 对象，设备端自己下载并解码：

```json
"bg": {
  "images": [
    "https://ha.xiaosiqi.cn/local/switchyard/orbis/bg/1.jpg",
    "https://ha.xiaosiqi.cn/local/switchyard/orbis/bg/2.jpg"
  ],
  "interval": 60,
  "dim": 35,
  "card": 60
}
```

- `images`：按顺序轮播，最多 8 张；
- `interval`：每张停留秒数（≥5，默认 60）；
- `dim`：图片上叠一层黑色的百分比（0-80，默认 35）；
- `card`：卡片自身的黑色蒙版百分比（0-95，默认 60）。卡片因此比背景更暗，
  形成"玻璃"效果；设 95 接近纯黑，设 0 则完全透明（只剩文字）。

设备侧行为：
1. 首次下载后把 JPEG 存进 flash 的 `assets` FAT 分区（`/assets/bg_<i>.jpg` + `bg_<i>.url`）；
2. 之后开机若 URL 未变，直接读缓存解码，不再走网络；
3. 解码成 RGB565 贴在最底层，切换时 500ms 淡入。

设备用 `meta.display` 声明屏幕规格（`w`/`h`/`formats`/`fit`），
switchyard 按此裁剪与转换；目前设备只声明 `jpeg`（`esp_jpeg` 解码）。

当前限制：TLS 记录必须用默认的 16 KB 输入缓冲（`CONFIG_MBEDTLS_SSL_IN_CONTENT_LEN=16384`），
调小会导致下载大文件时报 `MBEDTLS_ERR_SSL_BAD_INPUT_DATA`。

## 5. cmd（插件独有操作）

**只放 HA 做不到的动作**（固件分发、布局下发、取日志）。阀门、重启、亮度这类
HA 能直接表达的控制走 `switchyard/<id>/ha/<obj>/set`（见 §8.3），不占这里。

```json
{"seq":12,"cmd":"ota","url":"https://ha.example.com/local/switchyard/fw/screen-0.2.0.bin","sha256":"..."}
{"seq":13,"cmd":"layout","url":"https://ha.example.com/local/switchyard/layout/hall.json"}
{"seq":14,"cmd":"state"}              // 立刻上报 state/meta
{"seq":15,"cmd":"get","key":"log"}    // 把最近 N 行日志发到 log 主题
{"seq":16,"cmd":"get","key":"cfg"}    // 把当前配置发到 cfg 主题
```

**seq 语义**

- 插件为每台设备维护单调递增的 `seq`（用当前时间戳，天然大于任何历史值）；
- 命令以 **retained** 方式发布，设备上线/唤醒后一订阅就能拿到最新一条；
- 设备本地保存"已执行的最后 seq"，**只执行 seq 更大的命令**，执行完发 `ack`；
- 如果收到的是 retained 命令，执行完再发一条空 retained 清掉它，避免重复执行。

## 5.1 cfg：配置直接下发（推荐）

布局这类"结构性配置"不走 cmd，而是单独一条 retained 主题：

```
switchyard/<id>/cfg      payload = 布局 JSON（和 /local/switchyard/layout.json 同一格式）
```

- Switchyard 保存布局时 **retained 发布**到该设备专属的 `cfg` 主题；
- 设备收到 → 解析 → 应用界面 → **存 NVS**（重启后先用 NVS 里的这份）；
- 设备回 `event: {"ev":"cfg_applied"}`；
- 新设备第一次上线会立刻收到 broker 上保留的最后一份，不需要等到下次下发；
- 好处：**按设备寻址**（同款屏可以有 N 个不同布局）、**主动推送**、**配置不出公网**
  （不再需要把 JSON 放到 `www` 静态目录）。

设备的优先级：`NVS 里的 cfg` → `HTTP /local/switchyard/layout.json` → 自动发现兜底。

## 6. meta（设备声明）

设备上线时发一次 retained 的 `switchyard/<id>/meta`。它描述"我是谁、我会什么"，
**不用于生成 HA 实体**（实体由设备自己发 discovery，见 §8）。

### 6.0 三个字段的用途

| 字段 | 用途 |
|---|---|
| `type` / `name` / `model` | 台账显示 |
| `caps` | **只有 Switchyard 才能做的操作**（OTA、布局下发……），决定插件界面显示哪些管理按钮 |
| `metrics` | 只读遥测的**清单**，用于插件界面展示；设备自己也按这份清单发 HA discovery |
| `ha_entities` | 本设备往 HA 发过哪些实体，供插件「删除设备」时清理 |

### 6.1 caps：Switchyard 独有能力

`caps` 的判定标准只有一条：**HA 自己做不到、必须由插件代劳的动作**。

| cap | 动作 |
|---|---|
| `ota` | 固件分发（插件负责文件托管、版本比较、批量下发） |
| `layout` | （不生成实体）设备页布局编辑器开关 |
| `background` `rotation` `voice` | （不生成实体）对应配置面板 |

**不再是 cap 的**：阀门、重启、亮度、固件版本、信号、电量、水量、温湿度、漏水……
这些 HA 都能用标准 MQTT 实体表达，归设备自己的 discovery。

### 6.2 metrics：只读遥测清单

设备声明自己有哪些值，插件**只用来展示**（不生成实体）。设备按同一份清单
自己发布 HA discovery，两边保持一致即可。

```json
{
  "type": "host",
  "caps": ["ota"],
  "name": "LS2K0300 板子",
  "model": "Loongson 2K0300",
  "metrics": [
    {"key": "ip_eth",   "name": "有线 IP",  "icon": "mdi:ethernet"},
    {"key": "cpu_load", "name": "CPU 占用", "unit": "%", "icon": "mdi:cpu-64-bit"}
  ],
  "ha_entities": [
    {"component": "sensor",        "object": "ip_eth"},
    {"component": "sensor",        "object": "cpu_load"},
    {"component": "binary_sensor", "object": "online"},
    {"component": "button",        "object": "reboot"}
  ]
}
```

metric 字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✅ | state JSON 里的字段名，也是 object_id 后缀。限 `[a-z0-9_]{1,40}` |
| `name` | | 显示名，缺省用 `key` |
| `kind` | | `sensor`（默认）或 `binary_sensor` |
| `unit` | | 单位 |
| `icon` | | mdi 图标 |
| `device_class` | | HA 的 device_class |
| `state_class` | | 用于 HA 发现（如 `total_increasing`） |
| `object` | | 覆盖 object_id 后缀 |

### 6.3 ha_entities：供清理

设备把发过的实体列出来，插件「删除设备」时照着发空 retained：
`homeassistant/<component>/switchyard_<id>/<object>/config`。

完整的一份 meta 长这样：

```json
{
  "type": "screen",
  "name": "圆屏",
  "model": "JC3636W518",
  "caps": ["layout", "background", "voice", "rotation", "ota"],
  "display": {"w": 360, "h": 360},
  "metrics": [
    {"key": "rssi", "name": "信号", "unit": "dBm", "device_class": "signal_strength"},
    {"key": "battery", "name": "电量", "unit": "%", "device_class": "battery"}
  ],
  "ha_entities": [
    {"component": "sensor",        "object": "rssi"},
    {"component": "sensor",        "object": "battery"},
    {"component": "binary_sensor", "object": "online"},
    {"component": "button",        "object": "reboot"},
    {"component": "number",        "object": "brightness"}
  ]
}
```

插件只用它渲染台账和详情页，**不据此生成任何 HA 实体**。

## 7. OTA 流程

```
Switchyard                     设备
    │ 上传 firmware.bin+json      │
    │（/config/www/switchyard/<id>/firmware.bin）│
    │ ── cmd:ota(url,sha256) ───▶ │ 下载 → 校验 → 写非活动槽
    │ ◀── event: ota_progress ─── │ （可选，设备支持就发）
    │ ◀── event: ota_done ─────── │ 切槽重启
    │ ◀── state(fw=新版本) ────── │ 上线后确认
```

约束：

- 固件放 HTTP 静态目录，MQTT 只传 URL，避免 2.5 MB 走 broker；
- 设备写**非活动槽**，断电不影响当前固件；启用 bootloader rollback；
- `sha256` 可选，设备实现校验更佳。

## 8. Home Assistant 侧（设备自己注册，不经过插件）

### 8.1 为什么

早期版本由插件替设备生成 discovery。问题不是"会不会丢"——retained 配置在 broker 上
能活很久；真正的缺口是：

- 插件挂着的时候**新加的设备在 HA 里根本不出现**；
- 插件升级带 bug 时可能误改别人的实体；
- 加一个指标就得改插件 + 重新构建加载项。

所以 v0.3 起**设备自己发 discovery**，插件退出实体注册的数据路径：

```
设备 ──pub──▶ switchyard/<id>/state ──▶ HA（直接读，不经过插件）
                    └───────────────▶ 插件（只读，做台账）
```

插件挂了、升级坏了，HA 的实体和自动化都不受影响。

### 8.2 设备要发什么

对每个实体发一条 retained 配置：

```
homeassistant/<component>/switchyard_<id>/<object>/config
```

`<component>` 用 HA 的标准值：`sensor` / `binary_sensor` / `switch` / `button` / `number`。
`state_topic` 直接用 `switchyard/<id>/state`（数据源只有一个，两边都读它）。

在线状态例外：读 `switchyard/<id>/availability`，`payload_on=online`、`payload_off=offline`。

**连接建立时发一次即可**（retained）；建议顺带每 10 分钟补发一次，防止 broker 丢 retained
或别的组件误清。

### 8.3 控制回下行

HA 能表达的控制（开关、按钮、数字）**不占 `caps`**，由设备在自己的 discovery 里声明
`command_topic`：

```
switchyard/<id>/ha/<object>/set
```

payload 用 HA 的惯例（`ON`/`OFF`、数字、按钮的 `PRESS`），**不是** Switchyard 的 JSON 命令。

两条通道彻底分开：

| 通道 | 主题 | 格式 | 谁用 |
|---|---|---|---|
| HA 控制 | `switchyard/<id>/ha/<obj>/set` | 纯值（ON / 数字 / PRESS） | HA 的 switch/button/number |
| Switchyard 操作 | `switchyard/<id>/cmd` | JSON + `seq` 去重 | OTA、布局下发等插件独有能力 |

由于 `switchyard/+/state` 这类订阅只匹配三层，而 `ha/<obj>/set` 是五层，
两者在 MQTT 通配符层面天然隔离；设备台账的 `on_message` 也有 `len(parts)==3` 的防御。

### 8.4 删除设备时的清理

设备在 `meta.ha_entities` 里自报发过哪些实体，插件「删除设备」时照着发空 retained
（见 §6.3），同时清掉 `switchyard/<id>/` 下的 retained 状态。

