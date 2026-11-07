# 限制 MQTT 账号能发什么（方案 A：最小改动）

默认情况下，**同一个 MQTT 账号能往任何主题发消息**。你的 broker 在公网
（`mqtt.xiaosiqi.cn:1883`，明文），所以一个密码泄漏就可能：

- 往 `homeassistant/#` 发假设备，凭空给 HA 加实体；
- 往 `ha/`、`gas_meter/`、`water_meter/` 干扰你现有的自动化和设备。

方案 A 不改设备、不建新账号，只做一件事：**把共用账号的写入范围限制在
`switchyard/#` 和 `homeassistant/#`**，别的一律不许。

---

## 1. 先确认两个账号

| 用途 | 账号 | ACL 里给什么 |
|---|---|---|
| HA 核心（MQTT 集成）| 见 **设置 → 设备与服务 → MQTT → 配置** 里的用户名 | `topic readwrite #`（它要监听所有设备）|
| 插件 + 所有设备 | `mqtt`（你现在共用的这个）| 只给 `switchyard/#` + `homeassistant/#` |

> 如果插件和设备的账号不是同一个，把下面 `user mqtt` 那段复制成两份，
> 用户名各自替换即可。

---

## 2. 写 `/share/mosquitto/acl.conf`

```conf
# ---------- HA 核心：全量 ----------
user homeassistant          # ← 换成 MQTT 集成里实际用的用户名
topic readwrite #

# ---------- 插件 + 设备：只用项目自己的主题 ----------
user mqtt
topic readwrite switchyard/#
topic readwrite homeassistant/#

# 其它没列出的账号：Mosquitto 默认拒绝
```

这样即使密码泄漏，攻击者也只能：

- 伪造/篡改**我们的**设备状态（影响范围限于本项目）；
- 发 `homeassistant/#` 的 discovery —— 这一点方案 A 挡不住（因为插件自己就需要这个权限），
  要彻底隔绝就得走"每台设备独立账号 + `pattern readwrite switchyard/%u/#`"（见文末）。

---

## 3. 启用 ACL

1. Mosquitto 加载项 → **配置** → 打开 `customize`；
2. 在 `/share/mosquitto/mosquitto.conf` 里加一行：

```conf
acl_file /share/mosquitto/acl.conf
```

3. 重启 Mosquitto 加载项；
4. 看 HA 日志 / 设备是否还在线（HA 失联多半是 `user homeassistant` 那行用户名写错了）。

---

## 4. 验证

```bash
# 1) 共用账号发自己的主题 → 成功
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u mqtt -P '***' \
  -t switchyard/orbis/state -m '{"fw":"test"}'

# 2) 共用账号发别人的命名空间 → 被断开
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u mqtt -P '***' \
  -t ha/test -m 'hello'

# 3) 共用账号发 gas_meter → 被断开
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u mqtt -P '***' \
  -t gas_meter/test -m 'hello'
```

预期：1 成功；2、3 报 `not authorised` 或被服务端断开。

---

## 5. 回滚

1. 注释掉 `/share/mosquitto/mosquitto.conf` 里的 `acl_file ...`；
2. 重启 Mosquitto 加载项。

立刻恢复成"任何账号可发任何主题"，所以先别急着删老账号。

---

## 6. 以后想更严（方案 B 摘要）

如果哪天想防"设备冒充设备"，再升级：

1. HA 里给每台设备建用户，**用户名 = 设备 ID**；
2. 设备设置页把 MQTT 用户名改成它自己的；
3. ACL 里加一条通配规则，替代 `user mqtt` 那段：

```conf
pattern readwrite switchyard/%u/#
```

`%u` 是连接时的用户名，所以账号 `orbis` 只能发 `switchyard/orbis/...`。
插件账号仍需单独给 `switchyard/#` + `homeassistant/#`。

---

## 相关

- 设备 ID 字符集：`a-z 0-9 - _`（≤31 字符），固件保存时校验，插件收到非法 ID 直接忽略；
- 主题空间划分：本插件只用 `switchyard/#` + `homeassistant/#`，不订阅 `#`，不碰其它命名空间。
