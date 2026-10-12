# Vortex Nexus

**A lightweight, high-performance, open-source alternative to Microsoft Teams —
assembled from proven open-source foundations, not written from scratch.**

Vortex Nexus is not a Teams clone. It is the counter-strategy: extreme speed
and constraint instead of the "everything app".

## The three foundational blocks

| Block | Role | Teams flaw it kills |
|-------|------|---------------------|
| [Tauri](https://tauri.app/) | Desktop window on the OS web engine (WebView2) | No 200 MB Electron bundle — the shell is ~10 MB, so Teams' idle RAM burn is gone at the root |
| [LiveKit](https://livekit.io/) | Ultra-low-latency WebRTC audio/video | Optimized streaming blocks out of the box — no homegrown media stack, no enterprise media bloat |
| [Lucide](https://lucide.dev/) | Vector UI icons (mic, camera, hang-up…) | Modern UI without shipping image atlases |

Chat stays a **flat-file nexus server** — every Teams flaw still maps to an
invariant:

| # | Teams architectural flaw | The Nexus invariant |
|---|--------------------------|---------------------|
| 1 | Multiple background processes idle away RAM even after updates | One Tauri process (OS webview) + one stdlib Python server. Idle = open sockets |
| 2 | The "everything app" (chat + video + calendars + apps + AI) makes context switching slow and cluttered | Chat + calls only. Channels, messages, presence, WebRTC. No calendar, no app store, no AI copilot |
| 3 | Every group chat force-spins up a SharePoint Document Library → data bloat, cloud cost | **No document library exists.** Messages are `.jsonl` files in `./data/channels/` — git-friendly, greppable |

## Layout

```
nexus.py            chat server (stdlib TCP, newline-JSON) — unchanged authority
nexus_test.py       engine E2E: 15/15 PASS
app/                Tauri 2 desktop client (vanilla JS + Vite, no framework)
  src/main.js       chat UI via invoke/emit + LiveKit room join
  src-tauri/src/
    chat.rs         Rust TCP client speaking the nexus protocol → UI events
    tokens.rs       mints LiveKit join JWTs (HS256; secret never reaches UI)
livekit/            local LiveKit server (binary fetched by setup_livekit.ps1)
```

Storage invariant: `cat data/channels/general.jsonl` *is* your export tool.

## Quickstart

```bat
:: 1. chat server (no pip deps — stdlib)
START_SERVER.bat

:: 2. A/V server (first run downloads the binary, configs are generated)
livekit\START_LIVEKIT.bat

:: 3. desktop app
cd app
npm install
npx tauri dev
```

LAN use: run `python nexus.py serve --host 0.0.0.0` and point clients at the
host IP. There is no cloud component and no account anywhere.

## Security & trust boundaries

- Chat: channel `^[a-z0-9_-]{1,32}$` and nick checks on **both** Rust client
  and Python server (no path traversal); 64 KB protocol line cap;
  4,000-char messages; server binds `127.0.0.1` by default
- A/V: API secret lives only in `livekit/livekit_config.json` (gitignored);
  the webview receives `{url, token}` only — tokens are HS256, expire in 1 h,
  scoped to a single room
- History loads last 200 messages; malformed lines are skipped, never crash

## Proof

`python nexus_test.py` — engine E2E, **15/15 PASS**:

```
PASS broadcast to both users (u1↔u2)     PASS path traversal rejected (../evil)
PASS history on join                      PASS empty message rejected
PASS own echo + peer delivery             PASS 4001-char message rejected
PASS bad nick rejected (<script>)         PASS server survives 70KB line flood
PASS channel file written                 PASS 2 messages persisted
PASS history survives server restart
```

Desktop app: Tauri window connects to chat (history + presence render),
LiveKit token mint → room join → mic/cam/hang-up controls with Lucide icons.

## License

MIT — free, forever. No accounts, no telemetry, no upsell.
