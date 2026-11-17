"""Reads what the Schwung manager's remote UI websocket (/ws/remote-ui) sends a browser for one slot: the params of the module, filtered by key.

    python calibration/dev/ruiprobe.py <slot 0..3> [key-substring ...] [--secs 40] [--host move.local]

No packages: a minimal RFC 6455 client over a socket (text frames, masked, no extensions). Prints each param_update's matching keys with the value length and
the first characters, so "the page was never sent the sample shape" is told apart from "it was sent an empty one".
"""
import argparse, base64, json, os, socket, struct, time

ap = argparse.ArgumentParser()
ap.add_argument("slot", type=int)
ap.add_argument("keys", nargs="*")
ap.add_argument("--secs", type=float, default=40)
ap.add_argument("--host", default="move.local")
a = ap.parse_args()

s = socket.create_connection((a.host, 7700), timeout=10)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall(("GET /ws/remote-ui HTTP/1.1\r\nHost: %s:7700\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nOrigin: http://%s:7700\r\n\r\n" % (a.host, key, a.host)).encode())
buf = b""
while b"\r\n\r\n" not in buf:
    buf += s.recv(4096)
head, buf = buf.split(b"\r\n\r\n", 1)
assert b" 101 " in head.split(b"\r\n")[0], head[:200]


def send(obj):
    data = json.dumps(obj).encode()
    mask = os.urandom(4)
    n = len(data)
    hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)) + mask
    s.sendall(hdr + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def need(n):
    global buf
    while len(buf) < n:
        buf += s.recv(65536)


def frame():
    global buf
    need(2)
    op = buf[0] & 0x0F
    n = buf[1] & 0x7F
    off = 2
    if n == 126:
        need(4); n = struct.unpack(">H", buf[2:4])[0]; off = 4
    elif n == 127:
        need(10); n = struct.unpack(">Q", buf[2:10])[0]; off = 10
    need(off + n)
    data = buf[off:off + n]
    buf = buf[off + n:]
    return op, data


send({"type": "subscribe", "slot": a.slot})
s.settimeout(5)
t_end = time.time() + a.secs
t0 = time.time()
frag = b""
while time.time() < t_end:
    try:
        op, data = frame()
    except socket.timeout:
        continue
    if op == 8:
        break
    if op == 9:
        continue
    if op not in (0, 1, 2):
        continue
    frag += data
    try:
        msg = json.loads(frag.decode("utf-8"))
    except Exception:
        continue                                             # a fragmented message: more frames follow
    frag = b""
    if msg.get("type") != "param_update":
        print("%5.1fs %s (slot %s)" % (time.time() - t0, msg.get("type"), msg.get("slot")))
        continue
    for k, v in (msg.get("params") or {}).items():
        if not a.keys or any(x in k for x in a.keys):
            print("%5.1fs %-34s len %5d  %r" % (time.time() - t0, k, len(v), v[:48]))
