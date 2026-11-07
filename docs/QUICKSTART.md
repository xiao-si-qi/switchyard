# 现在的启动流程（MQTT-only 版）

## 1. 固件启动

```
上电 → 连 Wi-Fi
     → 起配置网页 http://<设备IP>/
     → 起 MQTT（没配 host 时跳过）
     → 读 NVS 里的布局（上次 switchyard 下发的）
        有 → 直接建界面
        没有 → 只显示 System 页，等 orbis/<id>/cfg
```

## 2. 首次配置（一次）

浏览器打开 `http://192.168.1.38/`（设备当前 IP，System 页也能看到）：

| 字段 | 填什么 |
|---|---|
| Wi-Fi 名称/密码 | 已填好，不用改 |
| HA 地址 / 令牌 | 已填好，不用改 |
| **MQTT 服务器** | HA 的局域网 IP，例如 `192.168.1.10`（不是 `core-mosquitto`，那是 Docker 内部名字） |
| **MQTT 端口** | `1883` |
| **MQTT 用户名/密码** | 在 HA「设置 → 人员」里建的用户，Mosquitto 用它认证 |
| **设备 ID** | 留空 = 自动 `orbis-xxxx`；当前这块已经是 `hall-screen`，想统一命名可以改成 `orbis-xxxx` |

保存后设备自动重启。

## 3. 自动注册

设备连上 MQTT 后：

```
orbis/<id>/availability  online (retained)   →   switchyard 设备列表出现这台设备
orbis/<id>/state         每 30s (retained)    →  在线状态/固件/IP/信号/运行时长
```

**不需要在插件里登记**，注册表还会存到 `/data/devices.json`，插件重启不丢。

## 4. 下发布局

switchyard → 点设备卡片 → 「编辑本设备布局」→ 加卡片 → 「保存并发布」：

```
POST /api/devices/<id>/layout
   → retained 发布 orbis/<id>/cfg
   → 设备收到：应用界面 + 存 NVS + 回 event:cfg_applied
```

屏幕**立即变**，不需要重启；断网重启后先用 NVS 里那份，再等 broker 上的 retained cfg。

## 5. 远程 OTA

switchyard「布局 / 固件」页上传 `orbis.bin`（版本号要等于固件里的 `PROJECT_VER`）→
设备页「下发 OTA」→ 设备从 `public_url` 拼出的地址下载 → 写非活动槽 → 重启。

> `public_url` 要在加载项配置里填，例如 `https://ha.xiaosiqi.cn`；
> 填错设备会下载失败，日志里能看到 `ota begin failed`。

## 已删除的东西

- ❌ HTTP `GET /local/orbis/layout.json`（布局不再走静态文件）
- ❌ 自动挑选实体（灯/场景/传感器的兜底）
- ❌ `APP_ENTITY_*` / `APP_SENSOR_*` 的 menuconfig 槽位

固件里的布局来源只剩：**NVS（上次下发）→ 等 MQTT cfg**。
