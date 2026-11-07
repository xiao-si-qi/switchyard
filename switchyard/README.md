# Switchyard（Home Assistant 加载项）

ESP32 设备的统一上位机。当前版本（0.1.0）包含两块能力：

1. **屏幕布局下发**：编辑 JSON 布局（页面 / 卡片 / 实体），发布到
   `/config/www/orbis/layout.json`，屏幕重启后读取；
2. **固件 OTA 分发**：上传 `orbis.bin` + 版本号，写 `firmware.bin` / `firmware.json`，
   设备在 System 页点「检查更新」即可下载升级。

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
| `www_dir` | `/config/www/orbis` | 发布目录；与 Orbis Panel 固件默认路径一致 |
| `mqtt_host` | `core-mosquitto` | HA 官方 Mosquitto 加载项的域名 |
| `mqtt_port` | `1883` | |
| `mqtt_username` | 空 | **Mosquitto 加载项默认要求认证**：在 HA「设置 → 人员」里建一个用户，填这里 |
| `mqtt_password` | 空 | 同上 |

### MQTT 认证怎么填

HA 的 Mosquitto 加载项用 Home Assistant 的账号做认证（不是 anonymous）：

1. HA → 设置 → 人员 → **添加人员**，建一个用户（例如 `switchyard`，密码自己定）；
2. 打开 Switchyard 加载项 → **配置** → 填入 `mqtt_username` / `mqtt_password` → 保存 → 重启加载项；
3. 面板顶部应显示 **「MQTT 已连接 · N 台设备」**。

设备一侧只要往 `orbis/<id>/state` 发 retained JSON，就会自动出现在设备列表里。

## 为什么是零依赖

构建时只 `FROM python:3.12-alpine`，用 Python 标准库的 `http.server` + `urllib`，
不装 Flask/Requests，避免 HA 构建时 pip 拉包失败（国内网络常见）。

## 许可

MIT，见 [../LICENSE](../LICENSE)。
