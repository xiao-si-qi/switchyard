# Switchyard（Home Assistant 加载项）

ESP32 设备的统一上位机。当前版本（0.4.2）包含：

1. **设备台账**：设备往 `orbis/<id>/state` 发一次就自动注册，显示在线/固件/信号/电量；
2. **按设备下发布局**：编辑页面与卡片，**retained 发布到 `orbis/<id>/cfg`**，
   设备立即应用并存 NVS（不再写 `www` 静态文件）；
3. **按设备固件 OTA**：上传 `orbis.bin` 到 `www_dir/<device_id>/firmware.bin`，
   设备收到 `cmd:ota` 后自己下载、写非活动槽、重启。

**设备台账（P1）已可用**：订阅 `orbis/+/state|availability|event`，显示在线状态、固件版本、信号/电量、最后上报时间（协议见 [../docs/PROTOCOL.md](../docs/PROTOCOL.md)）。

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

### 按设备下发布局（MQTT，0.4.0）

「布局 / 固件」页顶部先选 **布局目标（设备）**，保存时：

- **retained 发布到 `orbis/<id>/cfg`**，设备收到**立即应用**并存 NVS（重启后先用它）；
- 配置**不再写进 `www` 静态目录**，也就不出公网；
- 支持 N 块同款屏各用各的布局（设备 id 由固件自动生成 `orbis-xxxx`）；
- 设备详情页的「编辑本设备布局」会自动把目标切到那台设备。

### 改设备名（device_id）

device_id 是 MQTT 主题里那一段（`orbis/<device_id>/...`），改它等于换身份：

1. 打开 `http://<设备IP>/` → 改「设备 ID」（小写、用 `-`，别用 `/`、空格、`+`、`#`）→ 保存，设备会重启；
2. 新 id 会自动注册到列表；旧条目点 **删除设备**（会同时清掉 broker 上它的 retained 状态，
   否则重启后又会"复活"）；
3. 布局是按设备存的（`/data/layouts/<id>.json`），换 id 后要重新下发一次。

### 设备自动注册（不用手工添加）

- 设备第一次往 `orbis/<id>/state`、`orbis/<id>/availability` 或 `orbis/<id>/event` 发消息，
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
