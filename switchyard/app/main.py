"""Switchyard — Home Assistant add-on (zero dependency).

Standard library only: no pip, no Flask, no requests. Device manager for ESP32 panels:
layout editor, firmware upload and writes it into Home Assistant's www folder, so
layouts are pushed over MQTT (`switchyard/<id>/cfg`); firmware lives under
`<www_dir>/<device_id>/firmware.bin`.

Environment:
    WWW_DIR           publish root (default /config/www/switchyard)
    SUPERVISOR_TOKEN  injected by the Supervisor, used to list entities
"""

import json
import os
import re
import hashlib
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from flask import Flask, jsonify, request, send_file

import discovery
import mqtt_bridge

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def load_options():
    try:
        with open("/data/options.json", "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


OPTS = load_options()
WWW_DIR = OPTS.get("www_dir") or os.environ.get("WWW_DIR") or "/config/www/switchyard"


def local_prefix():
    """把 /config/www/switchyard 映射成 /local/switchyard（HA 的 www 静态目录）。"""
    base = "/config/www"
    rel = os.path.relpath(WWW_DIR, base) if WWW_DIR.startswith(base) else os.path.basename(WWW_DIR)
    return "/local/" + rel.replace(os.sep, "/")
LAYOUT_PATH = os.environ.get("LAYOUT_PATH", os.path.join(WWW_DIR, "layout.json"))
CORE_API = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
PORT = int(os.environ.get("PORT", "8099"))

MAX_FW = 4 * 1024 * 1024  # app 分区 4MB


DEVICE_ID_RE = re.compile(r"^[a-z0-9_-]{1,31}$")


def valid_device_id(device_id):
    """device_id 会拼进 MQTT 主题和文件路径，必须限定字符集，防主题注入/路径穿越。"""
    return bool(DEVICE_ID_RE.match(device_id or ""))


def fw_paths(device_id):
    """每个设备一个子目录：/config/www/switchyard/<device_id>/{firmware.bin,firmware.json}"""
    folder = os.path.join(WWW_DIR, device_id)
    return folder, os.path.join(folder, "firmware.bin"), os.path.join(folder, "firmware.json")

SLUG = "switchyard"
# 与 config.yaml 的 version 保持一致（作为最后兜底，避免界面显示 v?）
FALLBACK_VERSION = "0.10.6"
REPO = "xiao-si-qi/switchyard"
# raw.githubusercontent.com 有 CDN 缓存（会返回旧版本号），改用 GitHub API
CONFIG_URL = "https://api.github.com/repos/%s/contents/switchyard/config.yaml?ref=main" % REPO
SELF_VERSION_CACHE = {"at": 0, "latest": "", "error": ""}


VERSION_SOURCE = {"current": "?", "source": "unknown", "error": ""}


def _version_from_yaml(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def addon_version():
    """当前安装的版本：Supervisor → 镜像内 config.yaml → 源码目录。"""
    if VERSION_SOURCE["source"] != "unknown":
        return VERSION_SOURCE["current"]

    errors = []
    try:
        info = supervisor_get("/addons/self/info")
        data = info.get("data") or info
        version = data.get("version")
        if version:
            VERSION_SOURCE.update({"current": version, "source": "supervisor", "error": ""})
            return version
        errors.append("supervisor: no version field")
    except Exception as exc:  # noqa: BLE001
        errors.append("supervisor: %s" % exc)

    for path in ("/app/addon_config.yaml", os.path.join(APP_DIR, "..", "config.yaml"),
                 os.path.join(APP_DIR, "addon_config.yaml")):
        version = _version_from_yaml(path)
        if version:
            VERSION_SOURCE.update({"current": version, "source": path, "error": " | ".join(errors)})
            return version
        errors.append("missing %s" % path)

    VERSION_SOURCE.update({"current": FALLBACK_VERSION, "source": "builtin", "error": " | ".join(errors)})
    print("version: using builtin %s (%s)" % (FALLBACK_VERSION, VERSION_SOURCE["error"]), flush=True)
    return FALLBACK_VERSION


def version_tuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


SUPERVISOR = "http://supervisor"


def supervisor_get(path, timeout=10):
    req = urllib.request.Request(SUPERVISOR + path, headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def supervisor_store_version():
    """从 Supervisor 的加载项商店读我们自己这个 add-on 的版本。"""
    errors = []
    for path in ("/store/addons/%s/info" % SLUG, "/addons/%s/info" % SLUG):
        try:
            data = supervisor_get(path)
            version = data.get("version") or (data.get("addon") or {}).get("version")
            if version:
                return version, ""
            errors.append("%s: no version field" % path)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (path, exc))
    return "", " | ".join(errors)


def reload_store():
    """让 Supervisor 重新拉取加载项仓库（HA 商店的"检查更新"其实就干这个）。"""
    try:
        req = urllib.request.Request(SUPERVISOR + "/store/reload", method="POST",
                                     headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, resp.status
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def github_latest_version():
    """直接问 GitHub 上的 config.yaml（API，不走 CDN 缓存）。"""
    req = urllib.request.Request(
        CONFIG_URL,
        headers={"User-Agent": "switchyard", "Accept": "application/vnd.github.raw"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        text = resp.read().decode("utf-8", "replace")
    for line in text.splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("version not found in remote config.yaml")


def latest_addon_version(max_age=60, force=False):
    """最新可用版本：Supervisor 商店 + GitHub 两路都查，取较大的那个。

    - force=True（用户点「检查更新」）忽略缓存，两路都重新请求；
    - 只取 Supervisor 会读到商店的旧缓存，所以必须和 GitHub 取最大值。
    """
    now = time.time()
    if not force and SELF_VERSION_CACHE["latest"] and now - SELF_VERSION_CACHE["at"] < max_age:
        return SELF_VERSION_CACHE["latest"], SELF_VERSION_CACHE["error"]

    candidates = []
    errors = []

    version, serr = supervisor_store_version()
    if version:
        candidates.append(("supervisor", version))
    else:
        errors.append("supervisor: %s" % serr)

    try:
        candidates.append(("github", github_latest_version()))
    except Exception as exc:  # noqa: BLE001
        errors.append("github: %s" % exc)

    if not candidates:
        msg = " | ".join(errors)
        SELF_VERSION_CACHE.update({"at": now, "latest": "", "error": msg})
        return "", msg

    source, latest = max(candidates, key=lambda item: version_tuple(item[1]))
    err = "" if len(candidates) == 2 else " | ".join(errors)
    SELF_VERSION_CACHE.update({"at": now, "latest": latest, "source": source, "error": err})
    return latest, err


DEFAULT_LAYOUT = {
    "version": 1,
    "title": "Home",
    "pages": [
        {
            "name": "Devices",
            "tiles": [
                {"entity": "light.example", "label": "示例灯", "type": "toggle"},
            ],
        }
    ],
}


def read_layout():
    if os.path.exists(LAYOUT_PATH):
        with open(LAYOUT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(json.dumps(DEFAULT_LAYOUT))


def write_layout(data):
    folder = os.path.dirname(LAYOUT_PATH) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, LAYOUT_PATH)


def read_fw_meta(device_id):
    _, _, meta_path = fw_paths(device_id)
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001
            pass
    return None


def write_firmware(device_id, data, version):
    """保存固件并生成 firmware.json（设备端按 version 判断是否需要升级）。"""
    folder, bin_path, meta_path = fw_paths(device_id)
    os.makedirs(folder, exist_ok=True)
    with open(bin_path, "wb") as fh:
        fh.write(data)
    meta = {
        "version": version,
        "file": "firmware.bin",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return meta


def ha_states():
    if not TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not set (run inside the Supervisor)")
    req = urllib.request.Request(CORE_API + "/states", headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def validate_layout(data):
    if not isinstance(data, dict):
        return "body must be a JSON object"
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        return "pages[] is required"
    for page in pages:
        if not isinstance(page, dict) or not page.get("name"):
            return "every page needs a name"
        tiles = page.get("tiles", [])
        if not isinstance(tiles, list):
            return "tiles must be an array"
        for tile in tiles:
            if not isinstance(tile, dict) or not tile.get("entity"):
                return "every tile needs an entity"
    return None



# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=None)

INGRESS_RE = re.compile(r"^(/api/hassio_ingress/[^/]+)(?=/|$)")


class _StripIngressPrefix:
    """HA ingress 会在路径前加 /api/hassio_ingress/<token>，路由前剥掉它。"""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO") or "/"
        match = INGRESS_RE.match(path)
        if match:
            environ["SCRIPT_NAME"] = (environ.get("SCRIPT_NAME") or "") + match.group(1)
            environ["PATH_INFO"] = path[len(match.group(1)):] or "/"
        return self.wsgi_app(environ, start_response)


app.wsgi_app = _StripIngressPrefix(app.wsgi_app)


def bad_id(device_id):
    if valid_device_id(device_id):
        return None
    return jsonify({"ok": False, "error": "invalid device id"}), 400


@app.get("/")
@app.get("/index.html")
def index():
    return send_file(os.path.join(APP_DIR, "index.html"), mimetype="text/html")


@app.get("/api/health")
def api_health():
    return jsonify(
        {
            "ok": True,
            "layout_path": LAYOUT_PATH,
            "layout_exists": os.path.exists(LAYOUT_PATH),
            "token": bool(TOKEN),
            "mqtt": mqtt_status(),
            "devices": STORE.device_count(),
            "version": addon_version(),
            "version_source": VERSION_SOURCE["source"],
            "version_error": VERSION_SOURCE["error"],
            "supervisor_token": bool(TOKEN),
        }
    )


@app.get("/api/layout")
def api_layout_get():
    try:
        return jsonify({"ok": True, "layout": read_layout(), "layout_path": LAYOUT_PATH})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/entities")
def api_entities():
    query = (request.args.get("q") or "").strip().lower()
    try:
        states = ha_states()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 502
    out = []
    for st in states:
        entity_id = st.get("entity_id", "")
        attrs = st.get("attributes") or {}
        name = attrs.get("friendly_name") or entity_id
        if query and query not in entity_id.lower() and query not in str(name).lower():
            continue
        out.append({"entity_id": entity_id, "name": name, "state": st.get("state")})
    out.sort(key=lambda item: item["entity_id"])
    return jsonify({"ok": True, "entities": out[:500], "total": len(out)})


@app.get("/api/devices")
def api_devices():
    return jsonify({"ok": True, "mqtt": mqtt_status(), "devices": STORE.snapshot()})


@app.get("/api/devices/<device_id>/layout")
def api_device_layout_get(device_id):
    err = bad_id(device_id)
    if err:
        return err
    lp = os.path.join(LAYOUTS_DIR, device_id + ".json")
    refresh = (request.args.get("refresh") or "0") not in ("0", "", "false")

    if refresh:
        # 让设备把 NVS 里的布局回发到 cfg 主题，等最多 2 秒
        send_device_cmd(device_id, {"cmd": "get", "key": "layout"})
        for _ in range(20):
            if STORE.device_cfg(device_id):
                break
            time.sleep(0.1)

    layout, source = None, "none"
    raw_cfg = STORE.device_cfg(device_id)
    if raw_cfg:
        try:
            layout, source = json.loads(raw_cfg), "device"
        except Exception:  # noqa: BLE001
            layout = None
    if layout is None and os.path.exists(lp):
        try:
            with open(lp, "r", encoding="utf-8") as fh:
                layout, source = json.load(fh), "file"
        except Exception:  # noqa: BLE001
            layout = None
    return jsonify({"ok": True, "device": device_id, "layout": layout, "source": source, "path": lp})


@app.post("/api/devices/<device_id>/layout")
def api_device_layout_post(device_id):
    err = bad_id(device_id)
    if err:
        return err
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"ok": False, "error": "invalid JSON body"}), 400
    problem = validate_layout(data)
    if problem:
        return jsonify({"ok": False, "error": problem}), 400

    os.makedirs(LAYOUTS_DIR, exist_ok=True)
    lp = os.path.join(LAYOUTS_DIR, device_id + ".json")
    try:
        with open(lp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500

    if MQTT is None or not MQTT.connected:
        return jsonify({"ok": False, "error": "MQTT not connected (layout saved locally)", "saved": True}), 503

    payload = json.dumps(data, ensure_ascii=False)
    topic = "switchyard/%s/cfg" % device_id
    ok = MQTT.publish(topic, payload, retain=True)
    return jsonify({"ok": ok, "topic": topic, "bytes": len(payload)}), (200 if ok else 500)


@app.get("/api/devices/<device_id>/firmware")
def api_device_firmware_get(device_id):
    err = bad_id(device_id)
    if err:
        return err
    _, bin_path, _ = fw_paths(device_id)
    public = (OPTS.get("public_url") or os.environ.get("PUBLIC_URL") or "").rstrip("/")
    return jsonify(
        {
            "ok": True,
            "device": device_id,
            "firmware": read_fw_meta(device_id),
            "present": os.path.exists(bin_path),
            "max_size": MAX_FW,
            "public_url": public,
            "firmware_url": (public + local_prefix() + "/" + device_id + "/firmware.bin") if public else "",
        }
    )


@app.post("/api/devices/<device_id>/firmware")
def api_device_firmware_post(device_id):
    err = bad_id(device_id)
    if err:
        return err
    version = (request.args.get("version") or "").strip()
    data = request.get_data(cache=False)
    if not data:
        return jsonify({"ok": False, "error": "empty body"}), 400
    if len(data) > MAX_FW:
        return jsonify({"ok": False, "error": "firmware larger than %d bytes" % MAX_FW}), 413
    # ESP32 app image starts with 0xE9
    if data[0] != 0xE9:
        return jsonify({"ok": False, "error": "not an ESP32 app image (first byte 0x%02x)" % data[0]}), 400
    if not version:
        return jsonify({"ok": False, "error": "version is required (e.g. 0.2.0)"}), 400
    try:
        meta = write_firmware(device_id, data, version)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500
    print("firmware %s/%s saved (%d bytes)" % (device_id, version, len(data)), flush=True)
    return jsonify({"ok": True, "firmware": meta})


@app.post("/api/devices/<device_id>/cmd")
def api_device_cmd(device_id):
    err = bad_id(device_id)
    if err:
        return err
    cmd = request.get_json(silent=True)
    if not isinstance(cmd, dict) or "cmd" not in cmd:
        return jsonify({"ok": False, "error": "cmd field is required"}), 400
    if MQTT is None or not MQTT.connected:
        return jsonify({"ok": False, "error": "MQTT not connected"}), 503
    cmd["seq"] = STORE.next_seq(device_id)
    topic = "switchyard/%s/cmd" % device_id
    ok = MQTT.publish(topic, json.dumps(cmd, ensure_ascii=False), retain=True)
    return jsonify({"ok": ok, "seq": cmd["seq"], "topic": topic}), (200 if ok else 500)


@app.post("/api/devices/<device_id>/forget")
def api_device_forget(device_id):
    err = bad_id(device_id)
    if err:
        return err
    dev = STORE.get_device(device_id)
    caps = dev.get("caps") if dev else None
    STORE.forget(device_id)
    discovery.remove_device(MQTT, device_id, caps)
    DISCOVERY_SEEN.pop(device_id, None)
    cleared = []
    if MQTT is not None and MQTT.connected:
        # 清掉 broker 上该设备的 retained 消息，否则重启后又会"复活"
        for suffix in ("state", "availability", "event", "cmd", "cfg"):
            if MQTT.publish("switchyard/%s/%s" % (device_id, suffix), "", retain=True):
                cleared.append(suffix)
    return jsonify({"ok": True, "forgot": device_id, "retained_cleared": cleared})


@app.post("/api/devices/<device_id>/log")
def api_device_log(device_id):
    """让设备回传最近 N 行日志（远程诊断）。"""
    err = bad_id(device_id)
    if err:
        return err
    if MQTT is None or not MQTT.connected:
        return jsonify({"ok": False, "error": "MQTT not connected"}), 503

    payload = request.get_json(silent=True) or {}
    lines = int(payload.get("lines") or 40)
    before = STORE.device_log(device_id)[1]
    if not send_device_cmd(device_id, {"cmd": "log", "lines": lines}):
        return jsonify({"ok": False, "error": "send failed"}), 500

    deadline = time.time() + 3.5
    while time.time() < deadline:
        text, at = STORE.device_log(device_id)
        if text and at > before:
            return jsonify({"ok": True, "device": device_id, "log": text})
        time.sleep(0.1)
    return jsonify({"ok": False, "error": "设备没有回传日志（可能不在线或不支持 log 能力）"}), 504


@app.get("/api/self_update")
def api_self_update():
    force = (request.args.get("force") or "0") not in ("0", "", "false")
    current = addon_version()
    latest, problem = latest_addon_version(force=force)
    return jsonify(
        {
            "ok": bool(latest),
            "current": current,
            "current_source": VERSION_SOURCE["source"],
            "latest": latest,
            "latest_source": SELF_VERSION_CACHE.get("source", ""),
            "update_available": bool(latest) and version_tuple(latest) > version_tuple(current),
            "error": problem,
            "repo": REPO,
            "store_hint": "HA → 设置 → 加载项商店 → ⋮ → 检查更新（或按住 Shift 点「检查更新」强制刷新商店缓存）",
        }
    )


@app.post("/api/store_reload")
def api_store_reload():
    ok, info = reload_store()
    return jsonify({"ok": ok, "info": str(info)}), (200 if ok else 502)


@app.errorhandler(404)
def not_found(_err):
    return jsonify({"ok": False, "error": "not found"}), 404


DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else os.path.join(APP_DIR, "..", "data"))
LAYOUTS_DIR = os.path.join(DATA_DIR, "layouts")
STORE = mqtt_bridge.DeviceStore(
    path=os.path.join(DATA_DIR, "devices.json"),
    stale_after=int(OPTS.get("device_stale_seconds") or os.environ.get("STALE_SECONDS") or 300),
)
MQTT = None


DISCOVERY_SEEN = {}


def on_mqtt_message(topic, payload):
    STORE.on_message(topic, payload)
    if topic.endswith("/state") or topic.endswith("/meta"):
        device_id = topic.split("/")[1]
        dev = STORE.get_device(device_id)
        if dev is None:
            return
        # 中文名也进 key：改名后要重新发布 discovery，否则 HA 里还是旧名字
        key = (dev.get("type"), dev.get("fw"), tuple(dev.get("caps") or []),
               (dev.get("meta") or {}).get("name"))
        if DISCOVERY_SEEN.get(device_id) != key:
            state = dict(dev["raw"])
            meta = dev.get("meta") or {}
            state["name"] = meta.get("name") or device_id
            state["model"] = meta.get("model")
            n = discovery.publish_device(MQTT, device_id, state, dev.get("caps"))
            if n:
                DISCOVERY_SEEN[device_id] = key
                print("discovery: %d entities for %s" % (n, device_id), flush=True)


def send_device_cmd(device_id, cmd):
    """给设备发一条 cmd（自动带 seq 去重），返回是否发出。"""
    if MQTT is None or not MQTT.connected:
        return False
    payload = dict(cmd)
    payload["seq"] = STORE.next_seq(device_id)
    topic = "switchyard/%s/cmd" % device_id
    return MQTT.publish(topic, json.dumps(payload, ensure_ascii=False), retain=True)


def discovery_loop():
    """每 5 分钟补发一次（broker 没开持久化时，重启后实体配置会丢）。"""
    while True:
        time.sleep(300)
        if MQTT is None or not MQTT.connected:
            continue
        for dev in STORE.snapshot():
            state = dict(dev["raw"])
            meta = dev.get("meta") or {}
            state["name"] = meta.get("name") or dev["id"]
            state["model"] = meta.get("model")
            discovery.publish_device(MQTT, dev["id"], state, dev.get("caps"))


def start_mqtt():
    """按加载项选项启动 MQTT 订阅；没配 host 就不启，界面显示未连接。"""
    host = OPTS.get("mqtt_host") or os.environ.get("MQTT_HOST") or "core-mosquitto"
    port = OPTS.get("mqtt_port") or int(os.environ.get("MQTT_PORT") or 1883)
    user = OPTS.get("mqtt_username") or os.environ.get("MQTT_USERNAME") or ""
    password = OPTS.get("mqtt_password") or os.environ.get("MQTT_PASSWORD") or ""
    global MQTT
    MQTT = mqtt_bridge.MqttClient(host, port, user, password, client_id="switchyard")
    MQTT.set_on_message(on_mqtt_message)
    MQTT.start(["switchyard/+/state", "switchyard/+/availability", "switchyard/+/event",
                "switchyard/+/cfg", "switchyard/+/diag", "switchyard/+/log"])
    threading.Thread(target=discovery_loop, daemon=True).start()
    print("mqtt: connecting to %s:%s as %s" % (host, port, user or "<anonymous>"), flush=True)


def mqtt_status():
    return MQTT.status() if MQTT else {"connected": False, "host": "", "last_error": "not started"}


def main():
    start_mqtt()
    threading.Thread(target=discovery_loop, daemon=True).start()
    print("switchyard %s listening on :%d (www_dir=%s)" % (addon_version(), PORT, WWW_DIR), flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False, debug=False)


if __name__ == "__main__":
    main()
