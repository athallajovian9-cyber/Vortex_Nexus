"""Vortex Nexus engine E2E: persistence, broadcast, trust-boundary, restart."""
import json, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).parent
PORT = 8899
DATA = Path(tempfile.mkdtemp(prefix="nexus_test_"))

def sh(*args, **kw):
    return subprocess.Popen([sys.executable, str(HERE / "nexus.py"), *args], **kw)

def wait_port(port, tries=50):
    for _ in range(tries):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False

class Client:
    def __init__(self, nick, do_hello=True):
        self.sock = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        self.f = self.sock.makefile("rb")
        if do_hello:
            self.send(type="hello", user=nick)
            self.expect(type="ok")

    def send(self, **kw):
        self.sock.sendall(json.dumps(kw).encode() + b"\n")

    def recv(self, timeout=3):
        self.sock.settimeout(timeout)
        try:
            line = self.f.readline()
            return json.loads(line) if line else None
        except (socket.timeout, ValueError):
            return None

    def expect(self, timeout=3, **want):
        """Read until a message matching `want` arrives (skips interleaves)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = self.recv(timeout=max(0.1, deadline - time.time()))
            if m and all(m.get(k) == v for k, v in want.items()):
                return m
        return None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

ok = 0
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)
    ok += 1

srv = sh("serve", "--port", str(PORT), "--data", str(DATA),
         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    check("server starts", wait_port(PORT))

    # --- two users, bidirectional broadcast
    u1 = Client("u1")
    u1.send(type="join", channel="general")
    check("history reply", (u1.expect(type="history") or {}).get("channel") == "general")

    u2 = Client("u2")
    u2.send(type="join", channel="general")
    check("u2 history", (u2.expect(type="history") or {}).get("channel") == "general")

    u1.send(type="msg", channel="general", text="hello nexus")
    m = u1.expect(type="msg", text="hello nexus")
    check("u1 got own echo", m is not None and m.get("user") == "u1")
    check("u2 got broadcast", u2.expect(type="msg", text="hello nexus") is not None)

    u2.send(type="msg", channel="general", text="hi back")
    check("u1 got u2 msg", u1.expect(type="msg", text="hi back") is not None)

    # --- trust boundaries (server is the authority)
    u1.send(type="msg", channel="../evil", text="x")
    check("path traversal rejected", u1.expect(type="error") is not None)
    u1.send(type="msg", channel="general", text="")
    check("empty message rejected", u1.expect(type="error") is not None)
    u1.send(type="msg", channel="general", text="x" * 5000)
    check("oversize message rejected", u1.expect(type="error") is not None)

    bad = Client("x", do_hello=False)
    bad.send(type="hello", user="<script>alert(1)</script>")
    check("bad nick rejected", bad.expect(type="error") is not None)
    bad.close()

    # oversized protocol line -> that conn dropped, server keeps serving
    try:
        u1.sock.sendall(b"X" * (70 * 1024))
        time.sleep(0.4)
        socket.create_connection(("127.0.0.1", PORT), timeout=2).close()
        check("server survives 70KB line flood", True)
    except OSError:
        check("server survives 70KB line flood", False)

    u1.close(); u2.close()
finally:
    srv.terminate()
    out = srv.communicate(timeout=5)[0].decode(errors="replace")
    print("server log:", out.strip()[:200])

# --- persistence across restart
ch_file = DATA / "channels" / "general.jsonl"
check("channel file written", ch_file.is_file())
rows = [json.loads(l) for l in ch_file.read_text(encoding="utf-8").splitlines()]
check("2 messages persisted", len(rows) == 2 and rows[0]["text"] == "hello nexus")

srv2 = sh("serve", "--port", str(PORT), "--data", str(DATA),
          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    check("restart ok", wait_port(PORT))
    u4 = Client("u4")
    u4.send(type="join", channel="general")
    h = u4.expect(type="history")
    check("history survives restart",
          h is not None and len(h["messages"]) == 2
          and h["messages"][0]["text"] == "hello nexus")
    u4.close()
finally:
    srv2.terminate()
    srv2.communicate(timeout=5)

shutil.rmtree(DATA, ignore_errors=True)
print(f"\nNEXUS E2E: {ok}/{ok} PASS")
