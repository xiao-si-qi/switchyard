"""Minimal MQTT 3.1.1 client + device store (standard library only).

Only the subset Switchyard needs: CONNECT, SUBSCRIBE (QoS 0), PUBLISH (QoS 0),
PINGREQ. No external dependencies, so the add-on image keeps building without pip.
"""

import json
import os
import socket
import struct
import threading
import time

CONNECT = 1
CONNACK = 2
PUBLISH = 3
PUBACK = 4
SUBSCRIBE = 8
SUBACK = 9
PINGREQ = 12
PINGRESP = 13
DISCONNECT = 14


def _encode_len(n):
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n > 0:
            byte |= 0x80
        out.append(byte)
        if n == 0:
            return bytes(out)


def _encode_str(s):
    raw = s.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


class MqttClient:
    def __init__(self, host, port, username="", password="", client_id="switchyard", keepalive=30):
        self.host = host
        self.port = int(port)
        self.username = username or ""
        self.password = password or ""
        self.client_id = client_id
        self.keepalive = keepalive

        self.connected = False
        self.last_error = ""
        self.last_message_at = 0.0
        self.messages = 0

        self._sock = None
        self._send_lock = threading.Lock()
        self._on_message = None
        self._topics = []
        self._stop = threading.Event()
        self._packet_id = 1

    # ---------------------------------------------------------------- public
    def set_on_message(self, cb):
        self._on_message = cb

    def start(self, topics):
        self._topics = list(topics)
        t = threading.Thread(target=self._run, name="mqtt", daemon=True)
        t.start()
        return self

    def publish(self, topic, payload, retain=False):
        if not self.connected or self._sock is None:
            return False
        body = _encode_str(topic) + payload.encode("utf-8")
        header = bytes([(PUBLISH << 4) | (0x01 if retain else 0x00)])
        pkt = header + _encode_len(len(body)) + body
        with self._send_lock:
            try:
                self._sock.sendall(pkt)
                return True
            except OSError as exc:
                self.last_error = str(exc)
                self.connected = False
                return False

    def status(self):
        return {
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "username": self.username or "<anonymous>",
            "messages": self.messages,
            "last_error": self.last_error,
            "last_message_at": self.last_message_at,
        }

    # --------------------------------------------------------------- internals
    def _run(self):
        backoff = 1
        while not self._stop.is_set():
            try:
                self._connect()
                backoff = 1
                self._loop()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self.connected = False
            finally:
                try:
                    if self._sock:
                        self._sock.close()
                except OSError:
                    pass
                self._sock = None
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 30)

    def _connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=10)
        sock.settimeout(5)

        flags = 0x02  # clean session
        payload = _encode_str(self.client_id)
        if self.username:
            flags |= 0x80
            payload += _encode_str(self.username)
            if self.password:
                flags |= 0x40
                payload += _encode_str(self.password)

        var = _encode_str("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive)
        body = var + payload
        pkt = bytes([CONNECT << 4]) + _encode_len(len(body)) + body
        sock.sendall(pkt)

        first, body = self._read_packet(sock)
        if first >> 4 != CONNACK:
            raise RuntimeError("unexpected packet %d" % (first >> 4))
        if len(body) >= 2 and body[1] != 0:
            reasons = {
                1: "协议版本不支持",
                2: "client id 被拒绝",
                3: "broker 不可用",
                4: "用户名或密码错误",
                5: "未授权：用户名/密码不对，或 broker 禁止匿名（Mosquitto 默认如此）",
            }
            code = body[1]
            raise RuntimeError(
                "CONNACK refused code %d（%s）" % (code, reasons.get(code, "未知原因"))
            )

        self._sock = sock
        self.connected = True
        self.last_error = ""

        # subscribe (QoS 0)
        sub_body = struct.pack("!H", self._packet_id)
        for t in self._topics:
            sub_body += _encode_str(t) + b"\x00"
        sub = bytes([SUBSCRIBE << 4 | 0x02]) + _encode_len(len(sub_body)) + sub_body
        sock.sendall(sub)

    def _loop(self):
        sock = self._sock
        sock.settimeout(1.0)
        last_ping = time.time()
        while not self._stop.is_set():
            try:
                first, body = self._read_packet(sock)
            except socket.timeout:
                first, body = None, None
            except OSError as exc:
                raise RuntimeError("read failed: %s" % exc)

            if first is not None:
                ptype = first >> 4
                if ptype == PUBLISH:
                    self._handle_publish(first, body)
            if time.time() - last_ping > self.keepalive * 0.7:
                with self._send_lock:
                    sock.sendall(bytes([PINGREQ << 4, 0]))
                last_ping = time.time()

    def _handle_publish(self, hdr_byte, body):
        qos = (hdr_byte >> 1) & 0x03
        if len(body) < 2:
            return
        tlen = struct.unpack("!H", body[:2])[0]
        topic = body[2:2 + tlen].decode("utf-8", "replace")
        rest = body[2 + tlen:]
        if qos > 0:
            rest = rest[2:]
        payload = rest.decode("utf-8", "replace")
        self.messages += 1
        self.last_message_at = time.time()
        if self._on_message:
            try:
                self._on_message(topic, payload)
            except Exception as exc:  # noqa: BLE001
                self.last_error = "handler: %s" % exc

    @staticmethod
    def _read_exact(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise OSError("connection closed")
            buf += chunk
        return buf

    def _read_packet(self, sock):
        first = self._read_exact(sock, 1)[0]
        length = 0
        mult = 1
        while True:
            b = self._read_exact(sock, 1)[0]
            length += (b & 0x7F) * mult
            if not (b & 0x80):
                break
            mult *= 128
            if mult > 128 ** 3:
                raise RuntimeError("malformed remaining length")
        body = self._read_exact(sock, length) if length else b""
        return first, body


class DeviceStore:
    """Keeps the last known state of every device, fed by retained MQTT topics.

    Devices register themselves the first time they publish anything on
    switchyard/<id>/...; the registry is persisted so the list survives add-on
    restarts, and "online" also requires a recent message (a retained "online"
    from a device that is switched off must not look alive forever).
    """

    def __init__(self, path=None, stale_after=300):
        self._lock = threading.Lock()
        self._devices = {}
        self._seq = {}
        self._path = path
        self._stale_after = int(stale_after)
        self._last_save = 0.0
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self):
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._devices = data.get("devices", {})
            self._seq = {k: int(v) for k, v in data.get("seq", {}).items()}
            print("devices: restored %d from %s" % (len(self._devices), self._path), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("devices: restore failed: %s" % exc, flush=True)

    def _save(self, force=False):
        if not self._path:
            return
        now = time.time()
        if not force and now - self._last_save < 5:
            return
        self._last_save = now
        try:
            folder = os.path.dirname(self._path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"devices": self._devices, "seq": self._seq}, fh, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as exc:  # noqa: BLE001
            print("devices: save failed: %s" % exc, flush=True)

    def on_message(self, topic, payload):
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "switchyard":
            return
        device_id, kind = parts[1], parts[2]
        with self._lock:
            dev = self._devices.setdefault(
                device_id, {"id": device_id, "state": {}, "availability": "unknown", "event": None, "last_seen": 0}
            )
            if kind == "availability":
                dev["availability"] = payload.strip()
            elif kind == "state":
                try:
                    dev["state"] = json.loads(payload)
                except json.JSONDecodeError:
                    dev["state"] = {"raw": payload}
                dev["availability"] = "online"
            elif kind == "event":
                try:
                    dev["event"] = json.loads(payload)
                except json.JSONDecodeError:
                    dev["event"] = {"raw": payload}
            dev["last_seen"] = int(time.time())
            self._save()

    def snapshot(self):
        now = int(time.time())
        with self._lock:
            out = []
            for dev in self._devices.values():
                state = dict(dev["state"])
                fresh = dev["last_seen"] and (now - dev["last_seen"]) <= self._stale_after
                online = dev["availability"] == "online" and fresh
                out.append(
                    {
                        "id": dev["id"],
                        "online": online,
                        "availability": dev["availability"],
                        "type": state.get("type", "unknown"),
                        "fw": state.get("fw"),
                        "rssi": state.get("rssi"),
                        "uptime": state.get("uptime"),
                        "battery": state.get("battery"),
                        "ip": state.get("ip"),
                        "last_seen": dev["last_seen"],
                        "stale_seconds": self._stale_after,
                        "event": dev["event"],
                        "raw": state,
                    }
                )
            out.sort(key=lambda d: d["id"])
            return out

    def next_seq(self, device_id):
        with self._lock:
            self._seq[device_id] = self._seq.get(device_id, 0) + 1
            return self._seq[device_id]

    def device_count(self):
        with self._lock:
            return len(self._devices)

    def forget(self, device_id):
        with self._lock:
            self._devices.pop(device_id, None)
            self._seq.pop(device_id, None)
            self._save(force=True)
