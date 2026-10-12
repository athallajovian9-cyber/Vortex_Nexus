//! LiveKit join-token minting (HS256 JWT). The API secret lives only in
//! `livekit/livekit_config.json` (gitignored) on the host machine; the
//! webview only ever receives `{url, token}` — never the secret.

use std::time::{SystemTime, UNIX_EPOCH};
use std::{fs, path::Path};

use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct LkConfig {
    url: String,
    api_key: String,
    api_secret: String,
}

#[derive(Serialize)]
struct VideoGrant {
    #[serde(rename = "roomJoin")]
    room_join: bool,
    room: String,
    #[serde(rename = "canPublish", skip_serializing_if = "Option::is_none")]
    can_publish: Option<bool>,
    #[serde(rename = "canSubscribe", skip_serializing_if = "Option::is_none")]
    can_subscribe: Option<bool>,
}

#[derive(Serialize)]
struct Claims {
    iss: String,
    sub: String,
    iat: u64,
    nbf: u64,
    exp: u64,
    video: VideoGrant,
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn config_path() -> std::path::PathBuf {
    // dev cargo cwd is `app/src-tauri`; tauri dev runs the exe from there.
    // Shipped layout keeps `livekit/` beside `app/`. Env override wins.
    if let Ok(p) = std::env::var("NEXUS_LIVEKIT_CONFIG") {
        return std::path::PathBuf::from(p);
    }
    for cand in [
        "../../livekit/livekit_config.json",
        "../livekit/livekit_config.json",
        "livekit/livekit_config.json",
        "../../livekit_config.json",
        "../livekit_config.json",
    ] {
        let c = Path::new(cand);
        if c.is_file() {
            return c.to_path_buf();
        }
    }
    Path::new("../../livekit/livekit_config.json").to_path_buf()
}

pub fn mint(room: &str, identity: &str) -> Result<serde_json::Value, String> {
    // trust boundary: room/identity validated like the chat side
    let room_ok = !room.is_empty()
        && room.len() <= 32
        && room.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_');
    let id_ok = !identity.is_empty() && identity.len() <= 32 && !identity.chars().any(char::is_control);
    if !room_ok {
        return Err("bad room name".into());
    }
    if !id_ok {
        return Err("bad identity".into());
    }

    let path = config_path();
    let raw = fs::read_to_string(&path)
        .map_err(|_| format!("missing LiveKit config: {}", path.display()))?;
    let cfg: LkConfig = serde_json::from_str(&raw).map_err(|e| format!("config: {e}"))?;

    let iat = now();
    let claims = Claims {
        iss: cfg.api_key.clone(),
        sub: identity.to_string(),
        iat,
        nbf: iat.saturating_sub(10),
        exp: iat + 3600,
        video: VideoGrant {
            room_join: true,
            room: room.to_string(),
            can_publish: Some(true),
            can_subscribe: Some(true),
        },
    };
    let token = jsonwebtoken::encode(
        &jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256),
        &claims,
        &jsonwebtoken::EncodingKey::from_secret(cfg.api_secret.as_bytes()),
    )
    .map_err(|e| format!("sign: {e}"))?;

    Ok(serde_json::json!({ "url": cfg.url, "token": token }))
}
