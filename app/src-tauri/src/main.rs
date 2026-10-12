//! Vortex Nexus desktop shell.
//! Chat: Rust TCP client speaks the existing newline-JSON nexus protocol
//! (server `nexus.py` unchanged — its 15/15 E2E remains the authority).
//! A/V: LiveKit — backend mints join JWTs (HS256, key/secret from local config).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod chat;
mod tokens;

#[tauri::command]
fn chat_connect(
    app: tauri::AppHandle,
    host: String,
    port: u16,
    user: String,
    channel: String,
) -> Result<(), String> {
    chat::connect(app, &host, port, &user, &channel)
}

#[tauri::command]
fn chat_send(payload: serde_json::Value) -> Result<(), String> {
    chat::send(&payload)
}

/// Sign a LiveKit join token for `room` as `identity`.
/// Trust boundary: config (api secret) is read from disk only, never exposed
/// to the webview; the frontend receives the finished token alone.
#[tauri::command]
fn mint_token(room: String, identity: String) -> Result<serde_json::Value, String> {
    tokens::mint(&room, &identity)
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            chat_connect,
            chat_send,
            mint_token
        ])
        .run(tauri::generate_context!())
        .expect("error while running Vortex Nexus");
}
