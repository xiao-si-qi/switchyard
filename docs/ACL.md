# 限制每台设备能发什么（Mosquitto ACL）

默认情况下，**同一个 MQTT 账号能往任何主题发消息**。这意味着：

- 圆屏、墨水屏、水表如果共用一个账号，任何一台被刷了固件/被拆走，都能冒充其它设备；
- 更能往 `homeassistant/#` 发假设备、往 `ha/#` 干扰你现有的自动化；
- broker 又暴露在公网（`mqtt.xiaosiqi.cn:1883`），风险会放大。

ACL 就是给每个账号划一块主题空间。

## 主题约定（本项目）

| 角色 | 需要访问的主题 |
|---|---|
| Switchyard 插件 | `switchyard/#` 读写、`homeassistant/#` 读写（发 discovery）|
| 设备（圆屏/墨水屏/水表…）| 只读写 `switchyard/<自己的 id>/#` |

设备的设备 ID 与 MQTT 用户名**同名**最省事，这样 ACL 可以用 `%u` 通配符，一条规则覆盖所有设备。

## 方案 A：最小改动（共用一个账号）

如果暂时不想给每台设备建独立账号，至少把共用账号限制到本项目主题，别让它碰 `ha/`、`gas_meter/` 等：

```conf
# /share/mosquitto/acl.conf
user mqtt
topic readwrite switchyard/#
topic readwrite homeassistant/#

user switchyard
topic readwrite switchyard/#
topic readwrite homeassistant/#

# 其它账号（HA 自己、其它设备）按需自己加
```

缺点：仍然不能阻止"这台设备冒充那台设备"。

## 方案 B：推荐（每台设备独立账号）

1. 在 HA「设置 → 人员」里给每台设备建一个用户，用户名 = 设备 ID，例如 `orbis`、`bedroom-elink`；
2. 设备设置页（`http://<设备IP>/`）把 MQTT 用户名/密码改成它自己的；
3. ACL 用通配符：

```conf
# /share/mosquitto/acl.conf

# 插件：可以看所有设备 + 发 discovery
user switchyard
topic readwrite switchyard/#
topic readwrite homeassistant/#

# 每台设备：只能碰 switchyard/<自己的用户名>/#
pattern readwrite switchyard/%u/#

# 其它账号一律不给（或按需）
```

`pattern ... %u` 会把 `%u` 替换成连接时用的用户名，所以 `orbis` 这个账号只能发 `switchyard/orbis/...`，改不了别人的状态，也进不了 `homeassistant/#`。

## 在 HA 的 Mosquitto 加载项里启用

1. 打开 Mosquitto broker 加载项 → **配置** → 打开自定义配置（`customize`），把 `mosquitto.conf` 放到 `/share/mosquitto/`；
2. 在 `/share/mosquitto/mosquitto.conf` 里加一行（路径按你的加载项版本可能不同，见加载项文档）：

```conf
acl_file /share/mosquitto/acl.conf
```

3. 重启 Mosquitto 加载项；
4. 在 `/share/mosquitto/acl.conf` 里写上面的规则（改完再重启一次）。

> 注意：ACL 一旦生效，**没被授权的账号会立刻连不上**（`CONNACK code 5` 未授权）。
> 先把插件和所有设备的账号都写进 ACL，再重启，顺序反了会掉线。

## 验证

```bash
# 设备账号只能发自己的主题（应该成功）
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u orbis -P '***' \
  -t switchyard/orbis/state -m '{"fw":"test"}'

# 设备账号发别人的主题（应该被断开）
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u orbis -P '***' \
  -t switchyard/hall-screen/state -m '{"fw":"test"}'

# 设备账号发 discovery（应该被断开）
mosquitto_pub -h mqtt.xiaosiqi.cn -p 1883 -u orbis -P '***' \
  -t homeassistant/sensor/fake/config -m '{}'
```

## 相关

- 设备 ID 的字符集限制：`a-z 0-9 - _`（最长 31 字符），固件保存时会校验、插件收到非法 ID 会直接忽略；
- 设备 ID 改名流程见 [../switchyard/README.md](../switchyard/README.md)「改设备名」。
