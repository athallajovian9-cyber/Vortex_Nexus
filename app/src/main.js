// Vortex Nexus — Tauri frontend.
// Chat: Rust backend speaks the newline-JSON TCP protocol to nexus.py (reused,
// unchanged server).  A/V: LiveKit rooms via minted JWT (backend signs).
// Icons: lucide (no image assets).
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  Room, RoomEvent, Track, ConnectionState,
} from "livekit-client";
import {
  createIcons, Phone, PhoneOff, Mic, MicOff, Video, VideoOff, Send,
} from "lucide";

createIcons({ icons: { Phone, PhoneOff, Mic, MicOff, Video, VideoOff, Send } });

const LIVEKIT_URL = "ws://127.0.0.1:7880";
const $ = (id) => document.getElementById(id);
const DEFAULT_CHANNELS = ["general", "dev", "random"];

let channel = "general";
let room = null;
let micOn = true;
let camOn = false;

// ------------------------------------------------------------- chat ----
function setStatus(text, cls = "") {
  const el = $("status");
  el.textContent = text;
  el.className = cls;
}

function renderChannels() {
  $("channels").innerHTML = DEFAULT_CHANNELS.map(
    (c) => `<div class="ch${c === channel ? " active" : ""}" data-ch="${c}"># ${c}</div>`,
  ).join("");
  document.querySelectorAll(".ch").forEach((el) => {
    el.addEventListener("click", () => joinChannel(el.dataset.ch));
  });
}

function line(html, cls = "") {
  const d = document.createElement("div");
  d.className = "m " + cls;
  d.innerHTML = html;
  $("log").appendChild(d);
  $("log").scrollTop = $("log").scrollHeight;
}

function esc(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function joinChannel(c) {
  channel = c;
  $("chanTitle").textContent = `# ${c}`;
  $("msg").placeholder = `Message #${c} — Enter to send`;
  $("log").innerHTML = "";
  renderChannels();
  send({ type: "join", channel: c });
  if (room) leaveCall().then(joinCall); // follow chat into the new room
}

function send(obj) { return invoke("chat_send", { payload: obj }); }

async function connectChat() {
  try {
    await invoke("chat_connect", {
      host: $("host").value.trim(),
      port: parseInt($("port").value, 10) || 8765,
      user: $("nick").value.trim(),
      channel,
    });
    setStatus("connecting…");
  } catch (e) {
    setStatus(`connect failed: ${e}`, "err");
  }
}

listen("nexus", (ev) => {
  const m = ev.payload;
  if (m.type === "history") {
    $("log").innerHTML = "";
    for (const h of m.messages || []) {
      const ts = new Date((h.ts || 0) * 1000).toTimeString().slice(0, 5);
      line(`<span class="ts">[${ts}]</span><span class="who">${esc(h.user)}</span>: ${esc(h.text)}`);
    }
  } else if (m.type === "msg") {
    const ts = new Date((m.ts || 0) * 1000).toTimeString().slice(0, 5);
    line(`<span class="ts">[${ts}]</span><span class="who">${esc(m.user)}</span>: ${esc(m.text)}`);
  } else if (m.type === "presence") {
    $("online").innerHTML = (m.users || []).length
      ? m.users.map((u) => `<div class="u">· ${esc(u)}</div>`).join("")
      : '<span class="dim">—</span>';
  } else if (m.type === "ok") {
    setStatus(m.text, "ok");
  } else if (m.type === "error") {
    setStatus(m.text, "err");
  } else if (m.type === "_closed") {
    setStatus("disconnected", "err");
  }
});

$("connectBtn").addEventListener("click", connectChat);
$("sendBtn").addEventListener("click", () => {
  const t = $("msg").value.trim();
  if (t) { send({ type: "msg", channel, text: t }); $("msg").value = ""; }
});
$("msg").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("sendBtn").click();
});

// -------------------------------------------------------------- call ----
function tileFor(participant) {
  const id = participant.identity;
  let t = document.querySelector(`.tile[data-id="${id}"]`);
  if (!t) {
    t = document.createElement("div");
    t.className = "tile";
    t.dataset.id = id;
    t.innerHTML = `<div class="name">${esc(id)}</div>`;
    $("tiles").appendChild(t);
    $("tiles").hidden = false;
  }
  return t;
}

function attachTrack(participant, publication) {
  if (!publication.track) return;
  const t = tileFor(participant);
  if (publication.kind === Track.Kind.Video) {
    publication.track.attach();
    t.classList.remove("audio-only");
    t.prepend(publication.track.mediaStreamTrack
      ? Object.assign(document.createElement("video"), { srcObject: new MediaStream([publication.track.mediaStreamTrack]) })
      : publication.track.attachedElements[0]);
  } else {
    t.classList.add("audio-only");
    publication.track.attach(); // audio element, off-DOM ok
  }
}

function renderCallState() {
  $("callState").textContent = room
    ? `in call · ${room.name} · ${1 + room.remoteParticipants.size} in room`
    : "no call";
}

async function joinCall() {
  if (room) return;
  try {
    const { url, token } = await invoke("mint_token", {
      room: channel, identity: $("nick").value.trim() || "guest",
    });
    const r = new Room({ adaptiveStream: true });
    r.on(RoomEvent.ParticipantConnected, renderCallState)
      .on(RoomEvent.ParticipantDisconnected, renderCallState)
      .on(RoomEvent.TrackSubscribed, (p, pub) => attachTrack(p, pub))
      .on(RoomEvent.TrackUnsubscribed, (_p, pub) => pub.track?.detach());
    setStatus("joining call…");
    await r.connect(url || LIVEKIT_URL, token);
    room = r;
    micOn = true;
    try {
      await r.localParticipant.setMicrophoneEnabled(true);
    } catch { setStatus("no microphone found", "err"); }
    try {
      await r.localParticipant.setCameraEnabled(true);
      camOn = true;
    } catch { camOn = false; }
    tileFor(r.localParticipant);
    $("callControls").hidden = false;
    $("joinCall").hidden = true;
    syncCallBtns();
    renderCallState();
    setStatus("connected + in call", "ok");
  } catch (e) {
    setStatus(`call failed: ${e}`, "err");
  }
}

async function leaveCall() {
  if (!room) return;
  await room.disconnect();
  room = null;
  $("tiles").innerHTML = "";
  $("tiles").hidden = true;
  $("callControls").hidden = true;
  $("joinCall").hidden = false;
  renderCallState();
}

function syncCallBtns() {
  $("micBtn").classList.toggle("on", !micOn);
  $("micBtn").innerHTML = micOn ? '<i data-lucide="mic"></i>' : '<i data-lucide="mic-off"></i>';
  $("camBtn").classList.toggle("on", !camOn);
  $("camBtn").innerHTML = camOn ? '<i data-lucide="video"></i>' : '<i data-lucide="video-off"></i>';
  createIcons({ icons: { Mic, MicOff, Video, VideoOff } });
}

$("joinCall").addEventListener("click", joinCall);
$("leaveBtn").addEventListener("click", leaveCall);
$("micBtn").addEventListener("click", async () => {
  if (!room) return;
  micOn = !micOn;
  await room.localParticipant.setMicrophoneEnabled(micOn);
  syncCallBtns();
});
$("camBtn").addEventListener("click", async () => {
  if (!room) return;
  camOn = !camOn;
  try { await room.localParticipant.setCameraEnabled(camOn); } catch { camOn = false; }
  syncCallBtns();
});

// -------------------------------------------------------------- boot ----
renderChannels();
connectChat();
