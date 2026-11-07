# Switchyard（Home Assistant 加载项）

ESP32 设备的统一上位机。当前版本（0.4.2）包含：

1. **设备台账**：设备往 `switchyard/<id>/state` 发一次就自动注册，显示在线/固件/信号/电量；
2. **按设备下发布局**：编辑页面与卡片，**retained 发布到 `switchyard/<id>/cfg`**，
   设备立即应用并存 NVS（不再写 `www` 静态文件）；
3. **按设备固件 OTA**：上传 `orbis.bin` 到 `www_dir/<device_id>/firmware.bin`，
   设备收到 `cmd:ota` 后自己下载、写非活动槽、重启。

**设备台账（P1）已可用**：订阅 `switchyard/+/state|availability|event`，显示在线状态、固件版本、信号/电量、最后上报时间（协议见 [../docs/PROTOCOL.md](../docs/PROTOCOL.md)）。

## 安装

HA → 设置 → 加载项商店 → ⋮ → 仓库，添加：

```
https://github.com/xiao-si-qi/switchyard
```

然后安装 **Switchyard**、启动，左侧栏出现面板。

## 配置项

| 选项 | 默认 | 说明 |
|---|---|---|
| `www_dir` | `/config/www/switchyard` | 发布根目录；每台设备一个子目录 `<www_dir>/<device_id>/firmware.bin` |
| `public_url` | 空 | 设备的下载前缀，例如 `https://ha.xiaosiqi.cn`；「下发 OTA」用它拼固件 URL |
| `device_stale_seconds` | `300` | 超过这么久没有新上报就显示离线（电池设备可以调大）|
| `mqtt_host` | `core-mosquitto` | HA 官方 Mosquitto 加载项的域名 |
| `mqtt_port` | `1883` | |
| `mqtt_username` | 空 | **Mosquitto 加载项默认要求认证**：在 HA「设置 → 人员」里建一个用户，填这里 |
| `mqtt_password` | 空 | 同上 |

### MQTT 认证怎么填

HA 的 Mosquitto 加载项用 Home Assistant 的账号做认证（不是 anonymous）：

1. HA → 设置 → 人员 → **添加人员**，建一个用户（例如 `switchyard`，密码自己定）；
2. 打开 Switchyard 加载项 → **配置** → 填入 `mqtt_username` / `mqtt_password` → 保存 → 重启加载项；
3. 面板顶部应显示 **「MQTT 已连接 · N 台设备」**。

### 按设备下发布局（MQTT）

布局编辑现在在**设备页里**（只有上报了 `layout` 能力的设备才显示），保存时：

- **retained 发布到 `switchyard/<id>/cfg`**，设备收到**立即应用**并存 NVS（重启后先用它）；
- 配置**不再写进 `www` 静态目录**，也就不出公网；
- 支持 N 块同款屏各用各的布局（设备 id 由固件自动生成 `orbis-xxxx`）；
- **布局以设备为准**：打开设备页会先显示缓存/本地副本，点「从设备读取」会发
  `{"cmd":"get","key":"layout"}`，设备把自己 NVS 里那份原样回发到 `switchyard/<id>/cfg`，
  编辑器据此填充 —— 换电脑、重装插件都不会丢布局。

### 检查插件自己的版本（0.7.2）

顶栏显示 `v0.7.2 · 已是最新`，点「检查更新」：

1. 先问 Supervisor（加载项商店）我们自己的版本，拿不到再退到 GitHub 上的
   `switchyard/config.yaml`；
2. 比对后显示 `已是最新` 或 `v0.7.2 → v0.8.0 可更新`；
3. **按住 Shift 点「检查更新」** = 调 Supervisor 的 `POST /store/reload`，
   强制刷新商店缓存——这就是 HA 商店"检查更新慢"的根因，刷完再去加载项里点更新就很快。

> 插件不能自己替换自己的容器，最后一步仍在 HA 里点"更新"。

### 检查更新（固件，插件内，0.7.1）

设备详情页的「本设备固件」面板直接比对版本，**不依赖 HA 的实体轮询**：

```
设备固件: 0.2.0    已上传: 0.2.1    可升级 → 0.2.1
```

点「检查更新」会：

1. 向设备发 `{"cmd":"state"}` —— 设备立刻上报 `state`/`meta`（不等 30s 周期）；
2. 1.2 秒后重新读取设备状态和已上传固件；
3. 显示 `已是最新` / `可升级 → vX` / `未上传固件`。

旁边就是「下发 OTA」，两步就能完成升级。

### 不同设备类型的实体（0.9.0）

除通用实体外，`state` 里出现对应字段就生成对应实体（所以同一套协议能覆盖圆屏、水表、墨水屏）：

| state 字段 | 实体 |
|---|---|
| `water` / `temp` / `hum` / `battery_v` | `sensor`（m³ / °C / % / V）|
| `leak` / `comm_fault` | `binary_sensor`（moisture / problem）|
| `valve` + caps 含 `valve` | `switch.阀门`（payload `{"cmd":"valve","open":true}`）|

### 设备能力声明（caps，0.7.0）

设备上线时发一次 retained 的 `switchyard/<id>/meta`，告诉插件它会什么：

```json
{"type": "screen", "caps": ["layout", "ota", "reboot", "brightness"],
 "display": {"w": 360, "h": 360}, "fw": "0.2.0"}
```

插件按 `caps` 决定发哪些实体/显示哪些按钮：

| caps | 效果 |
|---|---|
| `reboot` | HA `button.重启` + 详情页「重启」 |
| `ota` | HA `button.检查更新` + 详情页「下发 OTA」 |
| `brightness` | HA `number.亮度` + 详情页亮度输入 |
| `layout` | 详情页「编辑本设备布局」 |

没上报 `meta` 的设备按 `type` 用默认能力（`screen` 全功能，其它 `ota`+`reboot`），
所以老设备不受影响；详情页会显示一行「能力: …」。

### 自动变成 HA 实体（MQTT Discovery）

每台注册的设备，Switchyard 会替它发布一套 HA MQTT Discovery（retained），
所以在 HA 里直接就是一个设备 + 一组实体，**设备固件不用改**：

| 实体 | 类型 | 来源 |
|---|---|---|
| 固件 / 信号 / 运行时长 / 电量 | sensor | `switchyard/<id>/state` 的 JSON 字段（`value_template`）|
| 在线 | binary_sensor（connectivity）| `switchyard/<id>/availability` |
| 重启 / 检查更新 | button | 发布 `{"cmd":"reboot"}` / `{"cmd":"ota"}` 到 `switchyard/<id>/cmd` |

- discovery 主题：`homeassistant/<component>/switchyard_<id>/<object>/config`；
- 设备首次上报时发布一次，固件版本/类型变化时重发，另外每 5 分钟补发一次
  （broker 没开持久化时，重启后配置不会丢）；
- 「删除设备」会把这些 discovery 配置一起清掉（空 retained）。

### 改设备名（device_id）

device_id 是 MQTT 主题里那一段（`switchyard/<device_id>/...`），改它等于换身份：

1. 打开 `http://<设备IP>/` → 改「设备 ID」（小写、用 `-`，别用 `/`、空格、`+`、`#`）→ 保存，设备会重启；
2. 新 id 会自动注册到列表；旧条目点 **删除设备**（会同时清掉 broker 上它的 retained 状态，
   否则重启后又会"复活"）；
3. 布局是按设备存的（`/data/layouts/<id>.json`），换 id 后要重新下发一次。

### 设备自动注册（不用手工添加）

- 设备第一次往 `switchyard/<id>/state`、`switchyard/<id>/availability` 或 `switchyard/<id>/event` 发消息，
  就自动出现在设备列表里，**不需要在插件里登记**；
- 注册表落在 `/data/devices.json`，加载项重启后仍保留（不会因为插件重启而"忘记"设备）；
- `availability` 是 retained 的，但设备关机后那条 `online` 不会自己消失，所以"在线"还要求
  **最近 `device_stale_seconds` 内有上报**，超时就显示离线（电池设备本来就长时间不报，按需调大）；
- 设备页可以「删除设备」（`POST /api/devices/<id>/forget`），把它从注册表里去掉。

## 为什么是零依赖

构建时只 `FROM python:3.12-alpine`，用 Python 标准库的 `http.server` + `urllib`，
不装 Flask/Requests，避免 HA 构建时 pip 拉包失败（国内网络常见）。

## 许可

MIT，见 [../LICENSE](../LICENSE)。

## 主题空间

- 本插件只用 `switchyard/#` 和 `homeassistant/#`（后者发 discovery），
  不订阅 `#`、不碰 `ha/`、`gas_meter/`、`water_meter/` 等其它命名空间；
- 设备 ID 限定 `a-z 0-9 - _`（≤31 字符）：固件保存时校验，插件收到非法 ID 直接忽略，
  防止主题注入和 `../` 路径穿越；
- broker 目前是自家设备共用账号，**没有加 ACL**（设备都在自己手里，先图省事）。
  如果以后要防"设备互相冒充"，再按"每台设备一个账号 + `pattern readwrite switchyard/%u/#`"收紧。

## 开发提示

- 改版本号时，`switchyard/config.yaml` 的 `version:` 和 `app/main.py` 里的
  `FALLBACK_VERSION` 要一起改（后者是 Supervisor 和文件都读不到时的兜底，
  避免顶栏显示 `v?`）。
- 空格键/Shift 之类的小技巧：顶栏「检查更新」普通点击 = 查最新版本；
  **按住 Shift 点击** = 让 Supervisor 重新拉取加载项仓库（刷新商店缓存）。
