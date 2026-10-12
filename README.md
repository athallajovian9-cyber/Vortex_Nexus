# Vortex Nexus

**A lightweight, high-performance, open-source alternative to Microsoft Teams.**

Vortex Nexus is not a Teams clone. It is the counter-strategy: extreme speed
and constraint instead of the "everything app". One Python process on each
side, text chat only, messages stored as flat files.

## Why Teams is being replaced → what Nexus does instead

| # | Teams architectural flaw | The Nexus invariant |
|---|--------------------------|---------------------|
| 1 | Multiple background processes idle away RAM even after updates | **One process per side**, stdlib only. No Electron, no background daemons — idle is just an open socket |
| 2 | The "everything app" (chat + video + calendars + apps + AI) makes context switching slow and cluttered | **Text chat only.** Channels + messages + presence. No video engine, no calendar, no app store, no AI copilot to bloat the interface |
| 3 | Every group chat force-spins up a SharePoint Document Library → data bloat, cloud cost | **No document library exists.** Messages are `.jsonl` files in `./data/channels/` — git-friendly, greppable, ~100 bytes at rest |

## Quickstart

```bat
pip install -r requirements.txt
START_SERVER.bat      # backend on 127.0.0.1:8765
START_NEXUS.bat       # GUI client — pick nick, Connect, chat
```

Two clients on one machine: just run `START_NEXUS.bat` twice. Other machines
on your LAN: run the server with `python nexus.py serve --host 0.0.0.0` and
point clients at the host IP. There is no cloud component — the server *is*
the whole backend, and it fits in one file.

## Architecture (one file, two modes)

```
nexus.py  ── serve ──▶ threaded TCP server (single writer)
                       newline-delimited JSON protocol
                       appends to data/channels/<name>.jsonl
                       broadcasts to channel members
          ── gui   ──▶ customtkinter client
                       reader thread → queue → Tk main loop (no unsafe UI calls)
```

Storage is append-only JSONL: `{ts, user, text}` per line. Zero databases, zero
document libraries. `cat data/channels/general.jsonl` *is* your export tool.

## Security & trust boundaries (kept, not simplified away)

- Channel names validated `^[a-z0-9_-]{1,32}$` on **both** sides (no path traversal)
- Nicks validated `^[\w .\-]{1,32}$` — printable, no control characters
- Protocol line cap 64 KB (abusive peer → connection dropped, server lives)
- Message cap 4,000 chars, enforced at server (authoritative) and client
- Server binds `127.0.0.1` by default — LAN exposure is an explicit opt-in
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

## License

MIT — free, forever. No accounts, no telemetry, no upsell.
