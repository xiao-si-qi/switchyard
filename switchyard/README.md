# Switchyard（Home Assistant 加载项）

ESP32 设备的统一上位机。当前版本（0.1.0）包含两块能力：

1. **屏幕布局下发**：编辑 JSON 布局（页面 / 卡片 / 实体），发布到
   `/config/www/orbis/layout.json`，屏幕重启后读取；
2. **固件 OTA 分发**：上传 `orbis.bin` + 版本号，写 `firmware.bin` / `firmware.json`，
   设备在 System 页点「检查更新」即可下载升级。

MQTT 设备台账（P1）正在开发，协议见 [../docs/PROTOCOL.md](../docs/PROTOCOL.md)。

## 安装

HA → 设置 → 加载项商店 → ⋮ → 仓库，添加：

```
https://github.com/xiao-si-qi/switchyard
```

然后安装 **Switchyard**、启动，左侧栏出现面板。

## 配置项

| 选项 | 默认 | 说明 |
|---|---|---|
| `www_dir` | `/config/www/orbis` | 发布目录；与 Orbis Panel 固件默认路径一致，方便两个加载项并存 |

## 为什么是零依赖

构建时只 `FROM python:3.12-alpine`，用 Python 标准库的 `http.server` + `urllib`，
不装 Flask/Requests，避免 HA 构建时 pip 拉包失败（国内网络常见）。

## 许可

MIT，见 [../LICENSE](../LICENSE)。
