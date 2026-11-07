# Switchyard

**ESP32 设备的统一上位机 —— 一个 Home Assistant 加载项 + 一套 MQTT 约定。**

> switchyard：铁路编组站。列车的调车、编组、发车都在这里完成；
> 这个项目做的是同一件事——把家里各种 ESP32 设备统一登记、下发任务、更新固件。

## 文档导航

| 想做什么 | 看这里 |
|---|---|
| 安装 / 配置加载项 | [switchyard/README.md](switchyard/README.md) |
| 设备协议（主题、JSON、seq、OTA 流程） | [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| 被管理的设备固件（圆屏） | [orbis-panel](https://github.com/xiao-si-qi/orbis-panel) |
| 从旧插件迁移 | [从 Orbis Panel Editor 迁移](#从-orbis-panel-editor-迁移) |

## 它解决什么问题

家里逐渐会有很多自制的 ESP32 设备：圆屏控制面板、墨水屏、水表、温湿度计……
每个都有自己的固件、自己的上报方式，改一个参数要重新烧录，发一次固件要挨个插 USB。

Switchyard 提供一层统一的"设备管理"：

| 能力 | 说明 |
|---|---|
| 设备清单 | 谁在线、固件版本、信号、电量、最后一次上报 |
| 远程配置 | 给屏幕下发布局、给传感器下发采样周期，不用重烧 |
| 固件分发 | 上传一次固件，按设备类型推给所有同类设备（URL 下发，设备自己拉） |
| 统一协议 | 一套 MQTT 主题 + JSON 约定，新设备照抄就能接入（[docs/PROTOCOL.md](docs/PROTOCOL.md)） |

**它不重复 Home Assistant 的活儿**：实体、自动化、语音、仪表盘都还是 HA 的；
控制与状态走 HA 的 MQTT 集成，Switchyard 只做 HA 不适合做的三件事：设备台账、固件分发、批量配置。

## 架构

```
                ┌──────────────── Home Assistant ────────────────┐
   Mosquitto    │  MQTT 集成（自动发现实体）                       │
   (broker)     │  Switchyard 加载项（设备台账 / OTA / 配置下发）   │
                └──────────┬────────────────────┬─────────────────┘
                           │                    │
        ┌──────────────────┼────────────────────┼──────────────────┐
     圆屏 (常在线)        墨水屏 (电池唤醒)       水表 (低功耗)      更多设备…
```

- 设备 → `orbis/<id>/state`（retained）+ `orbis/<id>/availability`（LWT）
- Switchyard → `orbis/<id>/cmd`（retained + seq，设备醒来后执行）
- 二进制（固件、布局）不放 MQTT：设备拿 URL 自己去 HTTP 拉

完整主题表、JSON 字段、seq 去重语义见 **[docs/PROTOCOL.md](docs/PROTOCOL.md)**。

## 安装（Home Assistant 加载项）

1. HA → 设置 → 加载项商店 → ⋮ → 仓库，添加：
   `https://github.com/xiao-si-qi/switchyard`
2. 安装 **Switchyard**，启动，左侧栏出现面板；
3. 打开「配置」填 MQTT 账号（`mqtt_username` / `mqtt_password`），重启加载项。

## 现状与路线

- [x] 加载项骨架（ingress 面板、`homeassistant_api` 权限、零依赖 Python 标准库）
- [x] 屏幕布局编辑器（页面 / 卡片 / 实体搜索 / 发布到 `www`）
- [x] 固件上传（写 `firmware.bin` + `firmware.json`，设备按版本号自更新）
- [x] 最小 MQTT 客户端（标准库实现，无 paho 依赖）+ 设备状态缓存
- [x] **P1** 设备台账：订阅 MQTT retained 状态，列出在线/固件/电量
- [ ] **P2** 批量固件分发：按设备类型上传、按类型下发 `cmd:ota`
- [ ] **P3** 配置下发：屏幕布局 / 采样周期 / 亮度，统一 push
- [ ] 设备注册表持久化、OTA 进度回传（`event: ota_progress`）

## 从 Orbis Panel Editor 迁移

本加载项原名 **Orbis Panel Editor**，最初放在 [orbis-panel](https://github.com/xiao-si-qi/orbis-panel) 仓库里。
现在那个仓库只维护圆屏固件，插件全部搬到这里：

1. HA 加载项商店 → 卸载 **Orbis Panel Editor**；
2. ⋮ → 仓库 → 移除 `https://github.com/xiao-si-qi/orbis-panel`；
3. 添加 `https://github.com/xiao-si-qi/switchyard`，安装 **Switchyard**；
4. 发布目录默认仍是 `/config/www/orbis`，已有的 `layout.json` / `firmware.json` **不需要迁移**。

## 兼容性


现有的 Orbis Panel 固件（[orbis-panel](https://github.com/xiao-si-qi/orbis-panel)）默认从
`<ha>/local/orbis/layout.json` 和 `<ha>/local/orbis/firmware.json` 取文件，
Switchyard 的默认发布目录就是这个，所以两个加载项可以并存；
以后统一迁到 `switchyard/` 路径时，会保留旧路径兼容。

## 许可

MIT，见 [LICENSE](LICENSE)。
