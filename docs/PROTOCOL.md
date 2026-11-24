# Switchyard 设备协议（v0.1 草案）

一套给"自制 ESP32 设备 + Home Assistant"用的最小 MQTT 约定。
目标：新设备照抄这几个主题和字段就能被 Switchyard 管理，不需要改上位机代码。

## 1. 命名

- `device_id`：小写、无空格，建议 `<房间>-<类型>`，例如 `hall-screen`、`kitchen-elink`、`inlet-water`；
- 所有主题以 `switchyard/` 为根（历史原因，保留；设备不分品牌都用这一个根）；
- 设备**必须**在连上 broker 时发布一次 `state`（retained）和 `availability=online`（retained），
  并用 LWT 把 `availability` 设成 `offline`。

## 2. 主题表

| 主题 | 方向 | retain | 说明 |
|---|---|---|---|
| `switchyard/<id>/state` | 设备 → HA | ✅ | JSON 状态快照，见 §3 |
| `switchyard/<id>/availability` | 设备 → HA | ✅ | `online` / `offline`，用 LWT 维护 |
| `switchyard/<id>/event` | 设备 → HA | ❌ | JSON 事件，见 §4 |
| `switchyard/<id>/cmd` | Switchyard → 设备 | ❌ | JSON 命令，见 §5（**不 retain**：一次性动作，设备需在线）|
| `switchyard/<id>/cfg` | Switchyard → 设备 | ✅ | **配置下发（布局 JSON）**，见 §5.1 |
| `switchyard/<id>/diag` | 设备 → 上位机 | ✅ | 错误摘要（≤1KB，限流 1 条/分钟）|
| `switchyard/<id>/log` | 设备 → 上位机 | ❌ | **按需**回传最近 N 行日志，非 retained |
| `switchyard/<id>/ack` | 设备 → HA | ❌ | `{"seq":N,"ok":true,"err":"..."}` |
| `switchyard/<id>/meta` | 设备 → HA | ✅ | **能力声明**，见 §6（推荐；缺省按 type 兜底） |

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

## 5. cmd

```json
{"seq":12,"cmd":"ota","url":"https://ha.example.com/local/switchyard/fw/screen-0.2.0.bin","sha256":"..."}
{"seq":13,"cmd":"layout","url":"https://ha.example.com/local/switchyard/layout/hall.json"}
{"seq":14,"cmd":"reboot"}
{"seq":16,"cmd":"state"}   // 立刻上报 state/meta
{"seq":17,"cmd":"get","key":"layout"}   // 把 NVS 里的布局回发到 cfg
{"seq":15,"cmd":"set","key":"brightness","value":60}
```

**seq 语义（关键）**

- Switchyard 维护每个设备的 `seq` 单调递增；
- 命令以 **retained** 方式发布，设备上线/唤醒后订阅到最新一条；
- 设备本地保存"已执行的最后 seq"，**只执行 seq 更大的命令**，执行完发 `ack`，再清掉 retained（`retain=true, payload=""`）；
- 这样"发命令时设备在睡觉"也不会丢：醒来第一件事就是拉到它。

### 4.5 背景图轮播（可选字段）

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

## 6. meta（能力声明）

设备上线时发一次 retained 的 `switchyard/<id>/meta`。插件只按这里的声明生成实体，
不猜 state 字段。

**从 v0.2 起 `caps` 只放"需要 Switchyard 主动操作"的能力**（会下发命令的那些），
以及只影响插件界面的纯 UI 能力。只读遥测走 [`meta.metrics`](#61-metrics只读遥测声明)。

| cap | 实体 / 动作 |
|---|---|
| `valve` | 阀门 switch，命令 `{"cmd":"valve","open":true}` |
| `reboot` | 重启按钮，命令 `{"cmd":"reboot"}` |
| `ota` | 检查更新按钮 |
| `brightness` | 亮度 number，命令 `{"cmd":"set","key":"brightness","value":N}` |
| `layout` | （不生成实体）设备页布局编辑器开关 |
| `background` `rotation` `voice` | （不生成实体）对应配置面板 |

**在线状态不需要声明**：`switchyard/<id>/availability` 是每个设备都有的固有主题，
插件永远生成 `binary_sensor.<id>_online`（connectivity，由 LWT 维护）。

未声明的 cap 不生成实体。

> **迁移说明（v0.2）**：`fw` / `rssi` / `uptime` / `battery` / `battery_v` / `water` /
> `climate` / `humidity` / `leak` / `comm_fault` / `log` / `online` 这些**只读** cap
> 已从词表移除，改由 `metrics` 声明。老固件继续声明它们也不会报错，只是不再生成实体；
> 插件升级时会把 broker 上残留的旧实体配置清掉（见 `LEGACY_CAPS`）。
> 迁移时 metric 的 `key` 沿用原 cap 名（如 `water`），`object_id` 不变，HA 里的实体能平滑过渡。

```json
{
  "type": "screen",
  "caps": ["layout", "ota", "brightness"],
  "display": {"w": 360, "h": 360},
  "partition": {"ota": true}
}
```

Switchyard 用它决定显示哪些按钮（有没有 OTA、能否下发布局）。

### 6.1 metrics（只读遥测声明）

`caps` 和 `metrics` 的分工，是 v0.2 起明确的一条边界：

| | 用途 | 生成什么 |
|---|---|---|
| **caps** | **需要 Switchyard 主动操作**的能力 | switch / button / number（阀门、重启、OTA、亮度），或只决定插件界面 |
| **metrics** | **只读遥测** | sensor |

**为什么这么分**：如果每台新设备、每个新指标都往 `caps` 词表里加一项，
`SPECS` 会变成什么都管的上帝对象，加一个"CPU 占用"就得改插件 + 重新构建。
遥测数据由设备自己声明"我有哪些值"，插件按声明生成实体，**加指标不用动插件**。

设备在 `meta` 里加一个 `metrics` 数组：

```json
{
  "type": "host",
  "caps": ["online", "reboot"],
  "name": "LS2K0300 板子",
  "model": "Loongson 2K0300",
  "metrics": [
    {"key": "ip_eth",       "name": "有线 IP",  "icon": "mdi:ethernet"},
    {"key": "ip_wlan",      "name": "无线 IP",  "icon": "mdi:wifi"},
    {"key": "cpu_load",     "name": "CPU 占用", "unit": "%", "icon": "mdi:cpu-64-bit"},
    {"key": "mem_used_pct", "name": "内存占用", "unit": "%"},
    {"key": "disk_used_pct","name": "存储占用", "unit": "%"},
    {"key": "uptime",       "name": "运行时长", "unit": "s",
     "device_class": "duration", "state_class": "total_increasing"}
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✅ | state JSON 里的字段名，也是 `object_id` 后缀。限 `[a-z0-9_]{1,40}` |
| `name` | | 显示名，缺省用 `key` |
| `unit` | | 单位。有单位就自动带 `state_class=measurement` |
| `icon` | | mdi 图标 |
| `device_class` | | HA 的 device_class（如 `temperature`、`signal_strength`、`duration`） |
| `state_class` | | 覆盖默认的 `measurement`（如 `total_increasing`） |
| `object` | | 覆盖 object_id 后缀——**迁移时用来保持 entity_id 不变** |
| `category` | | `diagnostic` / `config`，缺省 `diagnostic` |

生成的实体：`homeassistant/sensor/switchyard_<id>/<object>/config`，
`state_topic` 指向 `switchyard/<id>/state`，`value_template` 为 `{{ value_json.<key> }}`。

**兼容性**：没有 `metrics` 的老设备继续走 `caps → SPECS` 老路径，行为不变。

**迁移提示**：把某项从 `caps` 迁到 `metrics` 时，`key` 沿用原来的 cap 名
（例如 `water`），`object_id` 就不会变，HA 里的实体无感迁移，不会产生重复实体。

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

## 8. Home Assistant 侧

- 设备实体仍用标准 MQTT Discovery（`homeassistant/<component>/<node>/<object>/config`），
  这样 HA 的仪表盘、自动化、语音都能直接用；
- Switchyard 只订阅 `switchyard/+/state|availability|event`，**不代替** discovery；
- 没有 MQTT 的设备（比如现在走 HTTP 的圆屏）可以先保留 HTTP，Switchyard 按设备类型兼容。

## 9. 版本演进

- v0.1（本文件）：state / availability / cmd / ack / event；
- 后续新增字段必须可选，设备可忽略未知字段；
- 破坏性变更走 `switchyard/v2/...` 新根，不原地改语义。
