"""Vortex Nexus — a lightweight, open-source alternative to Microsoft Teams.

Teams pain -> Nexus invariant (the value proposition is speed + constraint):
  1. Teams spins up multiple background processes that idle away RAM.
     -> Nexus is ONE Python process per side, stdlib only. No Electron,
        no background daemons; idle = one open socket.
  2. The "everything app" (chat + video + calendar + apps + AI) makes
     context switching slow and cluttered.
     -> TEXT CHAT ONLY. No video engine, no calendar, no app store, no AI
        copilot to bloat the interface. Channels + messages, nothing else.
  3. Every group chat force-spins up a SharePoint document library
     (data bloat, cloud cost).
     -> Messages are flat .jsonl files in ./data/channels/. Git-friendly,
        greppable, < 1 KB each at rest. No document library exists, ever.

Protocol: newline-delimited JSON over TCP (stdlib socket, thread per client).
Server is the single writer (persistence + broadcast); clients are readers.

Modes:
    python nexus.py serve  [--host 127.0.0.1] [--port 8765] [--data data]
    python nexus.py                                # GUI client
Launchers: START_SERVER.bat / START_NEXUS.bat
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import socket
import threading
import time
from pathlib import Path

VERSION = "0.1.0"
MAX_LINE = 64 * 1024          # protocol line cap (bytes) — trust boundary
TEXT_MAX = 4000               # message cap (chars) — enforced both sides
HISTORY_KEEP = 200            # messages sent on channel join
CHANNEL_RE = re.compile(r"^[a-z0-9_-]{1,32}$")   # -> safe file names
USER_RE = re.compile(r"^[\w .\-]{1,32}$")        # printable nick, no controls
DEFAULT_CHANNELS = ["general", "dev", "random"]


# ------------------------------------------------------- protocol io ----
def encode(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"


def send_json(sock: socket.socket, obj: dict) -> None:
    sock.sendall(encode(obj))


def make_socket(host: str, port: int) -> socket.socket:
    s = socket.create_connection((host, port), timeout=10)
    s.settimeout(None)
    return s


class LineReader:
    """Newline-delimited reader with a hard per-line size cap."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = b""

    def next(self) -> dict | None:
        """Next JSON line, or None on clean close. Raises on protocol abuse."""
        while b"\n" not in self.buf:
            if len(self.buf) > MAX_LINE:
                raise ValueError("line too long")
            chunk = self.sock.recv(4096)
            if not chunk:
                if self.buf:
                    raise ValueError("truncated line")
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        if len(line) > MAX_LINE:
            raise ValueError("line too long")
        obj = json.loads(line.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        return obj


# ------------------------------------------------------------ server ----
class NexusServer:
    """Single writer: appends messages to flat files, broadcasts to channel."""

    def __init__(self, host: str, port: int, data_dir: Path):
        self.host, self.port = host, port
        self.data_dir = Path(data_dir)
        self.channels_dir = self.data_dir / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.clients: dict[socket.socket, dict] = {}
        self.running = True

    # ---- storage (validation lives at the owner: the file-name seam)
    def channel_file(self, name: str) -> Path:
        if not CHANNEL_RE.match(name):
            raise ValueError(f"bad channel name: {name!r}")
        return self.channels_dir / f"{name}.jsonl"

    def append(self, channel: str, user: str, text: str) -> dict:
        rec = {"ts": int(time.time()), "user": user, "text": text}
        with open(self.channel_file(channel), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def history(self, channel: str) -> list[dict]:
        p = self.channel_file(channel)
        if not p.is_file():
            return []
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines()[-HISTORY_KEEP:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a torn line; never crash on bad data
        return rows

    # ---- membership
    def members(self, channel: str) -> list[str]:
        with self.lock:
            return sorted({m["user"] for m in self.clients.values()
                           if m.get("channel") == channel and m.get("user")})

    def broadcast(self, channel: str, obj: dict) -> None:
        with self.lock:
            targets = [s for s, m in self.clients.items()
                       if m.get("channel") == channel]
        for s in targets:
            try:
                send_json(s, obj)
            except OSError:
                pass  # dead peer: dropped on its next recv error

    # ---- connections
    def serve(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(64)
        print(f"Vortex Nexus {VERSION} server listening on {self.host}:{self.port} "
              f"(data: {self.data_dir})", flush=True)
        while self.running:
            try:
                conn, addr = srv.accept()
            except OSError:
                break
            self.clients[conn] = {"user": None, "channel": None, "addr": addr}
            threading.Thread(target=self._read_loop, args=(conn,),
                             daemon=True).start()
        srv.close()

    def _drop(self, conn: socket.socket) -> None:
        with self.lock:
            ch = self.clients.pop(conn, {}).get("channel")
        try:
            conn.close()
        except OSError:
            pass
        if ch:
            self.broadcast(ch, {"type": "presence", "channel": ch,
                                "users": self.members(ch)})

    def _read_loop(self, conn: socket.socket) -> None:
        reader = LineReader(conn)
        try:
            while True:
                obj = reader.next()
                if obj is None:
                    break
                self._handle(conn, obj)
        except (ValueError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
        finally:
            self._drop(conn)

    def _handle(self, conn: socket.socket, obj: dict) -> None:
        t = obj.get("type")
        if t == "hello":
            user = str(obj.get("user", "")).strip()
            if not USER_RE.match(user):
                send_json(conn, {"type": "error", "text": "invalid nick"})
                return
            with self.lock:
                self.clients[conn]["user"] = user
            send_json(conn, {"type": "ok", "text": f"hello {user}"})

        elif t == "join":
            ch = str(obj.get("channel", ""))
            user = (self.clients.get(conn) or {}).get("user")
            if not user:
                send_json(conn, {"type": "error", "text": "say hello first"})
                return
            try:
                hist = self.history(ch)
            except ValueError as e:
                send_json(conn, {"type": "error", "text": str(e)})
                return
            with self.lock:
                self.clients[conn]["channel"] = ch
            send_json(conn, {"type": "history", "channel": ch, "messages": hist})
            self.broadcast(ch, {"type": "presence", "channel": ch,
                                "users": self.members(ch)})

        elif t == "msg":
            ch = str(obj.get("channel", ""))
            user = (self.clients.get(conn) or {}).get("user")
            text = str(obj.get("text", ""))
            if not user:
                send_json(conn, {"type": "error", "text": "say hello first"})
                return
            if len(text.strip()) == 0 or len(text) > TEXT_MAX:
                send_json(conn, {"type": "error",
                                 "text": f"message must be 1-{TEXT_MAX} chars"})
                return
            try:
                rec = self.append(ch, user, text.strip())
            except ValueError as e:
                send_json(conn, {"type": "error", "text": str(e)})
                return
            except OSError as e:
                send_json(conn, {"type": "error", "text": f"storage: {e}"})
                return
            self.broadcast(ch, {"type": "msg", "channel": ch, **rec})

        elif t == "ping":
            send_json(conn, {"type": "pong"})
        else:
            send_json(conn, {"type": "error", "text": f"unknown type {t!r}"})


# ------------------------------------------------------------ client ----
def run_gui(server_host: str, server_port: int) -> None:
    """GUI lives here (lazy import: `serve` mode needs no tkinter/display)."""
    import customtkinter as ctk

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")

    BG, PANEL, GREEN, DIM, CORAL = "#0b1410", "#12211a", "#22c55e", "#7fa893", "#ff8b75"

    class Nexus(ctk.CTk):
        def __init__(self) -> None:
            super().__init__()
            self.title(f"Vortex Nexus {VERSION} — text chat, nothing else")
            self.geometry("1120x760")
            self.configure(fg_color=BG)
            self.sock: socket.socket | None = None
            self.rx: threading.Thread | None = None
            self.q: queue.Queue = queue.Queue()
            self.channel = "general"
            self.nick = "nobody"
            self._build()
            self.after(100, self._pump)
            # auto-connect like every chat client (button = reconnect)
            self.after(400, self.connect)

        # ------------------------------------------------ UI
        def _build(self) -> None:
            top = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=44)
            top.pack(fill="x")
            ctk.CTkLabel(top, text="◈ Vortex Nexus", font=("Consolas", 16, "bold"),
                         text_color=GREEN).pack(side="left", padx=12)
            ctk.CTkLabel(top, text="one process · text only · no SharePoint, ever",
                         text_color=DIM, font=("Consolas", 11)).pack(side="left")
            self.host_e = ctk.CTkEntry(top, width=130, placeholder_text="host")
            self.host_e.insert(0, server_host)
            self.port_e = ctk.CTkEntry(top, width=64, placeholder_text="port")
            self.port_e.insert(0, str(server_port))
            self.nick_e = ctk.CTkEntry(top, width=110, placeholder_text="nick")
            self.nick_e.insert(0, "athalla")
            for w in (self.host_e, self.port_e, self.nick_e):
                w.pack(side="right", padx=4, pady=6)
            self.conn_btn = ctk.CTkButton(top, text="Connect", width=90, fg_color=GREEN,
                                          text_color="#06120a", command=self.connect)
            self.conn_btn.pack(side="right", padx=6)

            side = ctk.CTkFrame(self, width=210, fg_color=PANEL, corner_radius=0)
            side.pack(side="left", fill="y")
            side.pack_propagate(False)
            ctk.CTkLabel(side, text="CHANNELS", font=("Consolas", 12, "bold"),
                         text_color=DIM).pack(padx=12, pady=(12, 4), anchor="w")
            self.ch_list = ctk.CTkTextbox(side, fg_color=BG, text_color=GREEN,
                                          font=("Consolas", 13), wrap="none")
            self.ch_list.pack(fill="both", expand=True, padx=10, pady=(0, 6))
            self.ch_list.bind("<Double-Button-1>", self._on_channel)
            ctk.CTkButton(side, text="＋ channel", fg_color="#253528",
                          command=self.add_channel).pack(fill="x", padx=10, pady=(0, 8))
            ctk.CTkLabel(side, text="ONLINE", font=("Consolas", 12, "bold"),
                         text_color=DIM).pack(padx=12, pady=(4, 2), anchor="w")
            self.presence_lbl = ctk.CTkLabel(side, text="—", text_color=DIM,
                                             font=("Consolas", 11), anchor="w",
                                             justify="left")
            self.presence_lbl.pack(fill="x", padx=12, pady=(0, 12))

            mid = ctk.CTkFrame(self, fg_color="transparent")
            mid.pack(side="left", fill="both", expand=True, padx=10, pady=10)
            self.head = ctk.CTkLabel(mid, text="# general", font=("Consolas", 15, "bold"),
                                     text_color=GREEN, anchor="w")
            self.head.pack(fill="x")
            self.log = ctk.CTkTextbox(mid, fg_color="#060d09", font=("Consolas", 13))
            self.log.pack(fill="both", expand=True, pady=(6, 6))

            row = ctk.CTkFrame(mid, fg_color=PANEL, corner_radius=12)
            row.pack(fill="x")
            self.entry = ctk.CTkEntry(row, placeholder_text="Message #general — Enter to send")
            self.entry.pack(side="left", fill="x", expand=True, padx=8, pady=8)
            self.entry.bind("<Return>", lambda _e: self.send_msg())
            ctk.CTkButton(row, text="Send", width=80, fg_color=GREEN,
                          text_color="#06120a", command=self.send_msg).pack(side="right",
                                                                            padx=8)
            self.status = ctk.CTkLabel(self, text="disconnected — start a server first "
                                                  "(START_SERVER.bat)",
                                       text_color=CORAL, font=("Consolas", 11))
            self.status.pack(side="bottom", pady=4)

            self._render_channels()

        def _render_channels(self) -> None:
            self.ch_list.delete("1.0", "end")
            self.ch_list.insert("end", "\n".join(f"# {c}" for c in self._channels()))
            self.ch_list.see("1.0")

        def _channels(self) -> list[str]:
            chans = list(DEFAULT_CHANNELS)
            for p in sorted(Path("data/channels").glob("*.jsonl")):
                if p.stem not in chans:
                    chans.append(p.stem)
            return chans

        # ------------------------------------------------ connection
        def connect(self) -> None:
            host = self.host_e.get().strip()
            try:
                port = int(self.port_e.get().strip())
            except ValueError:
                self._set_status("bad port", ok=False)
                return
            nick = self.nick_e.get().strip()
            if not USER_RE.match(nick):
                self._set_status("nick: 1-32 letters/digits/space . _ -", ok=False)
                return
            self._close()
            try:
                sock = make_socket(host, port)
            except OSError as e:
                self._set_status(f"connect failed: {e}", ok=False)
                return
            self.sock, self.nick = sock, nick
            send_json(sock, {"type": "hello", "user": nick})
            self.rx = threading.Thread(target=self._recv_loop, args=(sock,), daemon=True)
            self.rx.start()
            self.join(self.channel)

        def _recv_loop(self, sock: socket.socket) -> None:
            reader = LineReader(sock)
            try:
                while True:
                    obj = reader.next()
                    if obj is None:
                        break
                    self.q.put(obj)
            except Exception:  # noqa: BLE001 — any failure = lost link
                pass
            self.q.put({"type": "_closed"})

        def _pump(self) -> None:
            try:
                while True:
                    self._handle(self.q.get_nowait())
            except queue.Empty:
                pass
            self.after(100, self._pump)

        # ------------------------------------------------ messages
        def _handle(self, obj: dict) -> None:
            t = obj.get("type")
            if t == "history":
                if obj.get("channel") != self.channel:
                    return
                self.log.delete("1.0", "end")
                for m in obj.get("messages", []):
                    self._append_msg(m)
            elif t == "msg":
                if obj.get("channel") == self.channel:
                    self._append_msg(obj)
            elif t == "presence":
                if obj.get("channel") == self.channel:
                    users = obj.get("users", [])
                    self.presence_lbl.configure(
                        text=("\n".join(f"· {u}" for u in users) or "—"),
                        text_color=GREEN if users else DIM)
            elif t == "error":
                self._set_status(obj.get("text", "error"), ok=False)
            elif t == "ok":
                self._set_status(f"connected as {self.nick}", ok=True)
            elif t == "_closed":
                self.sock = None
                self._set_status("disconnected", ok=False)

        def _append_msg(self, m: dict) -> None:
            ts = time.strftime("%H:%M", time.localtime(m.get("ts", time.time())))
            self.log.insert("end", f"[{ts}] {m.get('user', '?')}: {m.get('text', '')}\n")
            self.log.see("end")

        def send_msg(self) -> None:
            text = self.entry.get().strip()
            if not text:
                return
            if not self.sock:
                self._set_status("not connected", ok=False)
                return
            if len(text) > TEXT_MAX:
                self._set_status(f"too long ({len(text)}/{TEXT_MAX})", ok=False)
                return
            try:
                send_json(self.sock, {"type": "msg", "channel": self.channel,
                                      "text": text})
                self.entry.delete(0, "end")
            except OSError as e:
                self._set_status(f"send failed: {e}", ok=False)

        def join(self, ch: str) -> None:
            if not CHANNEL_RE.match(ch):
                self._set_status("bad channel name", ok=False)
                return
            self.channel = ch
            self.head.configure(text=f"# {ch}")
            self.entry.configure(placeholder_text=f"Message #{ch} — Enter to send")
            self.log.delete("1.0", "end")
            if self.sock:
                try:
                    send_json(self.sock, {"type": "join", "channel": ch})
                except OSError:
                    self._set_status("join failed", ok=False)

        def _on_channel(self, _e: object) -> str:
            try:
                i = int(float(self.ch_list.index("insert").split(".")[0])) - 1
                self.join(self._channels()[i])
            except (ValueError, IndexError):
                pass
            return "break"

        def add_channel(self) -> None:
            name = ctk.CTkInputDialog(title="New channel",
                                      text="Channel name (a-z, 0-9, - , _):").get()
            if name:
                self.join(name.strip().lower())

        def _set_status(self, text: str, ok: bool) -> None:
            self.status.configure(text=text, text_color=GREEN if ok else CORAL)

        def _close(self) -> None:
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None

        def destroy(self) -> None:  # clean exit
            self._close()
            super().destroy()

    Nexus().mainloop()


# ------------------------------------------------------------- entry ----
def main() -> None:
    ap = argparse.ArgumentParser(description="Vortex Nexus")
    ap.add_argument("mode", nargs="?", choices=["serve", "gui"], default="gui")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default="data")
    args = ap.parse_args()
    if args.mode == "serve":
        NexusServer(args.host, args.port, Path(args.data)).serve()
    else:
        run_gui(args.host, args.port)


if __name__ == "__main__":
    main()
