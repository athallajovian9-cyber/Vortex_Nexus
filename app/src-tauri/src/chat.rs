//! Newline-JSON TCP client for the nexus server (single-connection, threaded).
//! Mirrors the Python client protocol: hello → join → msg; server replies
//! history/ok/presence/msg/error. Incoming lines are re-emitted to the UI.

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::Mutex;
use std::time::Duration;
use std::thread;

use tauri::Emitter;

struct Conn {
    out: TcpStream,
}

static CONN: Mutex<Option<Conn>> = Mutex::new(None);

fn encode(v: &serde_json::Value) -> Vec<u8> {
    let mut b = serde_json::to_vec(v).unwrap_or_default();
    b.push(b'\n');
    b
}

pub fn connect(app: tauri::AppHandle, host: &str, port: u16, user: &str, channel: &str) -> Result<(), String> {
    // trust boundary: validate before touching the wire (server re-checks)
    if user.trim().is_empty() || user.len() > 32 {
        return Err("nick must be 1-32 chars".into());
    }
    let addr = (host, port)
        .to_socket_addrs()
        .map_err(|e| e.to_string())?
        .next()
        .ok_or("bad host")?;
    let stream = TcpStream::connect_timeout(&addr, Duration::from_secs(10))
        .map_err(|e| e.to_string())?;
    stream.set_nodelay(true).ok();
    let reader = stream.try_clone().map_err(|e| e.to_string())?;

    let hello = serde_json::json!({"type": "hello", "user": user.trim()});
    let join = serde_json::json!({"type": "join", "channel": channel});
    {
        let mut w = &stream;
        w.write_all(&encode(&hello)).map_err(|e| e.to_string())?;
        w.write_all(&encode(&join)).map_err(|e| e.to_string())?;
        w.flush().ok();
    }

    let mut guard = CONN.lock().map_err(|_| "conn lock")?;
    *guard = Some(Conn { out: stream });

    // reader thread: lines → UI events
    let app2 = app.clone();
    thread::spawn(move || {
        let mut lines = BufReader::new(reader).lines();
        loop {
            match lines.next() {
                None => break,
                Some(Err(_)) => break,
                Some(Ok(line)) => {
                    if line.trim().is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<serde_json::Value>(&line) {
                        Ok(v) => {
                            let closed = v.get("type").and_then(|t| t.as_str()) == Some("_closed");
                            let _ = app2.emit("nexus", v);
                            if closed {
                                break;
                            }
                        }
                        Err(_) => break, // malformed frame from server = drop link
                    }
                }
            }
        }
        let _ = app2.emit(
            "nexus",
            serde_json::json!({"type": "_closed"}),
        );
        if let Ok(mut g) = CONN.lock() {
            *g = None;
        }
    });
    Ok(())
}

pub fn send(payload: &serde_json::Value) -> Result<(), String> {
    let guard = CONN.lock().map_err(|_| "conn lock")?;
    let conn = guard.as_ref().ok_or("not connected")?;
    let mut w = &conn.out;
    w.write_all(&encode(payload)).map_err(|e| e.to_string())?;
    w.flush().map_err(|e| e.to_string())
}
