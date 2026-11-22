# Switchyard

**自制设备的统一上位机 —— 一个 Home Assistant 加载项 + 一套 MQTT 约定。**

> switchyard：铁路编组站。列车的调车、编组、发车都在这里完成；
> 这个项目做的是同一件事——把家里各种设备统一登记、下发配置、更新固件。

## 文档导航

| 想做什么 | 看这里 |
|---|---|
| 安装 / 配置加载项 | [switchyard/README.md](switchyard/README.md) |
| **从零跑通一遍（固件配置 → 注册 → 下发布局 → OTA）** | [docs/QUICKSTART.md](docs/QUICKSTART.md) |
| 设备协议（主题、JSON、seq、caps、OTA 流程） | [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| 接入的设备项目（固件 / 代理） | 见下方 [生态](#生态已接入的项目) |

## 它解决什么问题

家里逐渐会有很多自制设备：圆屏控制面板、旋钮屏、水表、墨水屏、龙芯小主机……
每个都有自己的固件、自己的上报方式，改一个参数要重新烧录，发一次固件要挨个插 USB。

Switchyard 提供一层统一的"设备管理"：

| 能力 | 说明 |
|---|---|
| 设备台账 | 谁在线、固件版本、信号、电量、最后一次上报 |
| 远程配置 | 给屏幕下发布局/背景图/方向、给传感器下发采样周期，不用重烧 |
| 固件分发 | 上传一次固件，按设备推给它自己拉取并升级（URL 下发） |
| 远程诊断 | 错误摘要自动上报，日志按需回传，不用接线 |
| 统一协议 | 一套 MQTT 主题 + JSON 约定，新设备照抄就能接入（[docs/PROTOCOL.md](docs/PROTOCOL.md)） |
| 能力声明 | 设备用 `caps` 声明自己支持什么，插件只显示它真有的功能 |

**它不重复 Home Assistant 的活儿**：实体、自动化、仪表盘都还是 HA 的；
控制与状态走 HA 的 MQTT 集成，Switchyard 只做 HA 不适合做的几件事：设备台账、配置下发、固件分发、远程诊断。

## 生态：已接入的项目

四个不同硬件、不同架构的项目，共用同一套协议，都能在同一个面板里管理：

| 项目 | 硬件 | 角色 | 能力（caps）与特点 |
|---|---|---|---|
| [**orbis-w518**](https://github.com/xiao-si-qi/orbis-w518) | GUITION 太极小派 **JC3636W518**<br>ESP32-S3 · 1.85" 360×360 圆屏 | 触屏控制屏 | `layout` `background` `rotation` `voice` `brightness` `ota` `log`<br>JSON 布局、背景图轮播、四方向、小智语音页、滑动翻页 |
| [**orbis-f118**](https://github.com/xiao-si-qi/orbis-f118) | GUITION **JC3636F118C**<br>ESP32-S31 · 1.65" 圆屏 + **旋钮** | 旋钮控制屏 | 与 w518 同协议；弧形刻度盘调灯/空调/风扇；<br>额外支持 `switchyard/<id>/shot` 截图回传 |
| **ls2k0300**<sup>†</sup> | 龙芯 **2K0300**（LoongArch64，久久派） | 主机类设备 | Go 写的 `switchyard-agent`：主机指标（CPU/内存/磁盘）、USB 事件、<br>远程命令（`reboot`/`ota`/`cfg`/`log`/`set`）、日志回传 |
| [**aqueduct**](https://github.com/xiao-si-qi/aqueduct) | ESP32-C3 + RS485 超声水表 | 传感器 / 执行器 | `water` `valve` `battery` `climate` `leak` `comm_fault`<br>累计水量、开关阀门、水电量、漏水与通信故障告警 |

两块圆屏的**布局协议、MQTT 约定、HA 通道完全一致**，布局可以在同一个面板里互通编辑；
水表和主机类设备复用同一套 `state / meta / cmd / cfg / log` 语义。

> <sup>†</sup> `ls2k0300` 目前是私有仓库，仓库里同时记录了这块板从换内核到无头部署的完整踩坑笔记。
>
> 想接入新设备？照 [docs/PROTOCOL.md](docs/PROTOCOL.md) 实现 `state` + `meta` 就已经能被发现；
> 再实现 `cmd` / `cfg` / `log` 就能被远程控制和排障。

## 架构

```
                ┌──────────────── Home Assistant ────────────────┐
   Mosquitto    │  MQTT 集成（按 caps 自动发现实体）               │
   (broker)     │  Switchyard 加载项（设备台账 / 配置 / OTA / 日志）│
                └──────────┬────────────────────┬─────────────────┘
                           │                    │
     ┌─────────────────────┼────────────────────┼─────────────────────┐
  圆屏 w518             旋钮屏 f118            水表 (低功耗)        龙芯 2K0300 (主机)
  常在线                常在线                 周期上报             常在线
```

- 设备 → `switchyard/<id>/{state,availability,meta,event,diag,log}`
- Switchyard → `switchyard/<id>/{cmd,cfg}`（`cmd` 不 retain，`cfg` retained）
- 二进制（固件、背景图）不放 MQTT：设备拿 URL 自己去 HTTP 拉

完整主题表、JSON 字段、seq 去重、caps 语义见 **[docs/PROTOCOL.md](docs/PROTOCOL.md)**。

## 安装（Home Assistant 加载项）

1. HA → 设置 → 加载项商店 → ⋮ → 仓库，添加：
   `https://github.com/xiao-si-qi/switchyard`
2. 安装 **Switchyard**，启动，左侧栏出现面板；
3. 打开「配置」填 MQTT 账号（`mqtt_username` / `mqtt_password`），重启加载项。

## 现状

已实现并实机验证：

- [x] 加载项骨架（ingress 面板、零 pip 依赖的 Python 标准库实现）
- [x] 最小 MQTT 客户端（标准库实现；校验 SUBACK、应答 QoS1、PINGRESP 存活检测）
- [x] **设备台账**：按 retained 状态列出在线/固件/信号/电量，支持删除与重连补发
- [x] **能力声明**：只按设备 `caps` 生成实体，能力增减会自动清理孤儿实体
- [x] **布局编辑器**：多页 / 每页最多 12 卡片 / 卡片类型（开关·场景·传感器）/ 实体搜索
- [x] **背景图轮播**：浏览器端裁剪上传、间隔与暗化可调、按设备屏幕规格转换
- [x] **固件分发**：上传 `.bin`，推送 URL 让设备自己 OTA（双分区，可回滚）
- [x] **远程诊断**：错误摘要（`diag`）+ 按需日志回传（`log`）
- [x] **语音配置**：小智 AI 服务端地址下发 + 连通性探测（设备端语音页在 w518 上已握手）

后续可做：

- [ ] 批量固件分发（一次给同型号全部设备）
- [ ] OTA 进度回传（`event: ota_progress`）
- [ ] host 类设备的指标 caps（CPU / 内存 / 磁盘）

## 许可

MIT，见 [LICENSE](LICENSE)。
