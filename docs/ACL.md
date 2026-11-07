# 方案 B：每台设备一个 MQTT 账号（ACL 隔离）

目标：**账号 `orbis` 只能读写 `switchyard/orbis/#`**，冒充不了别的设备，也进不了
`homeassistant/#`（防止伪造 HA 实体）。

核心是这一条 ACL 通配规则：

```conf
pattern readwrite switchyard/%u/#
```

`%u` = 连接时用的用户名。所以用户名必须**等于**设备 ID。

---

## 0. 先认清现在的账号

| 用途 | 现在 | 目标账号 |
|---|---|---|
| HA 核心（MQTT 集成）| 某个 HA 用户（见 MQTT 集成配置页）| 保持不动，ACL 里给全量 |
| Switchyard 加载项 | `mqtt`（共用）| 新建 `switchyard` |
| 圆屏 orbis | `mqtt`（共用）| 新建 `orbis` |
| 墨水屏 / 水表 / 气表 等 | 可能都是 `mqtt` | 逐个迁移，**迁移完再收紧 `mqtt`** |

> 迁移期间可以让 `mqtt` 这个老账号保留原权限，避免其它设备掉线；
> 等全部迁完，再把它删掉或收紧。

---

## 1. 建账号（HA → 设置 → 人员 → 添加用户）

建三个用户（密码自己记好）：

| 用户名 | 用途 |
|---|---|
| `switchyard` | 加载项自己 |
| `orbis` | 圆屏 |
| （以后每台设备一个）| 设备 ID 同名 |

> 也可以在 Mosquitto 加载项的 `logins:` 里直接写 `- username: orbis` / `password: ...`，
> 效果一样，取决于你习惯哪种。

---

## 2. 写 `/share/mosquitto/acl.conf`

```conf
# ---------- HA 核心：需要读写一切（它要监听所有设备、发布指令）----------
user homeassistant          # ← 换成你 MQTT 集成里实际用的用户名
topic readwrite #

# ---------- Switchyard 加载项 ----------
user switchyard
topic readwrite switchyard/#
topic readwrite homeassistant/#

# ---------- 设备：只能碰自己的主题空间 ----------
pattern readwrite switchyard/%u/#

# ---------- 迁移期：其它还没改账号的设备 ----------
# 全部迁移完之后，把这一段删掉或注释掉
user mqtt
topic readwrite #

# ---------- 默认拒绝 ----------
# Mosquitto 对未列出的用户默认就是拒绝，不用额外写
```

要点：

- `user homeassistant` 必须是**你 MQTT 集成实际用的那个用户名**，否则 HA 会失联；
- `pattern` 规则对**所有**账号生效（包括 `switchyard`），所以插件账号也要显式给权限；
- `mqtt` 那段是过渡，迁完就删。

---

## 3. 启用 ACL

1. Mosquitto 加载项 → **配置** → 打开 `customize`
   （如果界面里没有 `customize`，就在加载项配置里加 `customize: {}` 或按版本对应的写法）；
2. 在 `/share/mosquitto/mosquitto.conf` 里加：

```conf
acl_file /share/mosquitto/acl.conf
```

3. **顺序很重要**：

```
① 建好所有账号
② 写好 acl.conf（先把老账号 mqtt 的全量权限留着）
③ 启用 customize + acl_file，重启 Mosquitto
④ 确认 HA 和所有设备还在线
⑤ 逐个把设备/插件切到新账号
⑥ 全部切完后，删掉 acl.conf 里 mqtt 那段，再重启一次
```

顺序反了会掉线（`CONNACK code 5` 未授权）。

---

## 4. 切换插件账号

Switchyard → **配置**：

```
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: switchyard      ← 改这里
mqtt_password: 你给 switchyard 设的密码
public_url: https://ha.xiaosiqi.cn
```

保存 → **重启加载项** → 日志里应该有：

```
mqtt: connecting to core-mosquitto:1883 as switchyard
```

如果显示 `as <anonymous>` 或 code 5，说明账号没建好或 ACL 没给权限。

---

## 5. 切换设备账号

圆屏：打开 `http://192.168.1.38/`（Basic Auth `admin/admin`）→

```
MQTT 用户名: orbis
MQTT 密码:  你给 orbis 设的密码
```

保存后设备重启。串口应该看到：

```
I (3xxx) mqtt: mqtt start: mqtt://...:1883 (ESP_OK), device id 'orbis'
I (3xxx) mqtt: connected to broker
```

如果看到：

```
W mqtt: MQTT user 'mqtt' != device id 'orbis' - per-device ACL will block this device
```

说明用户名还没改（这是固件新加的自检）。

---

## 6. 验证隔离

```bash
# 1) 圆屏账号发自己的主题 → 成功
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u orbis -P '***' \
  -t switchyard/orbis/state -m '{"fw":"test"}'

# 2) 圆屏账号冒充别的设备 → 被断开
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u orbis -P '***' \
  -t switchyard/hall-screen/state -m '{"fw":"test"}'

# 3) 圆屏账号伪造 HA 实体 → 被断开
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u orbis -P '***' \
  -t homeassistant/sensor/fake/config -m '{}'

# 4) 插件账号发 discovery → 成功
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u switchyard -P '***' \
  -t homeassistant/sensor/switchyard_test/fw/config -m '{"name":"t","unique_id":"t"}'
```

预期：1 和 4 成功，2 和 3 报 `Connection Refused: not authorised` 或被服务端断开。

---

## 7. 出问题怎么回滚

ACL 只在 Mosquitto 加载项里生效，回滚很快：

1. 把 `/share/mosquitto/mosquitto.conf` 里的 `acl_file ...` 那行注释掉；
2. 重启 Mosquitto 加载项 —— 立刻恢复成"任何账号可发任何主题"。

所以**先别删老账号**，留作后路。

---

## 为什么值得做

- broker 在公网（`mqtt.xiaosiqi.cn:1883`，明文）；
- 一旦某台设备被拆走/固件被换，它拿到的密码只够污染**它自己**的主题；
- 谁也伪造不了 `homeassistant/#`，也就不能凭空给 HA 加假设备或改别人的自动化。
