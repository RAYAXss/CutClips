# core/tiktok.py
"""
TikTok account link + Content Posting API.

App setup (developers.tiktok.com): platform "Desktop", products Login Kit +
Content Posting API, scopes user.info.basic, video.upload, video.publish,
redirect URI exactly REDIRECT_URI below. Desktop apps must use PKCE and TikTok
expects the code_challenge as the HEX sha256 of the verifier (not base64url).

Keys come from .env (TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET).
Tokens live in data/tiktok_token.json (git-ignored), never in .env.
"""

import os
import json
import time
import hashlib
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode

import requests

REDIRECT_URI = "http://127.0.0.1:5000/tiktok/callback/"
SCOPES = "user.info.basic,video.upload,video.publish"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
TOKEN_FILE = Path("data/tiktok_token.json")

MB = 1024 * 1024
CHUNK = 10 * MB          # TikTok: chunks 5-64 MB, last one may hold the remainder (<=128 MB)
_lock = threading.Lock()


class TikTokError(Exception):
    pass


def client_key():
    return os.getenv("TIKTOK_CLIENT_KEY", "").strip()


def client_secret():
    return os.getenv("TIKTOK_CLIENT_SECRET", "").strip()


def configured() -> bool:
    return bool(client_key() and client_secret())


# ── OAuth ─────────────────────────────────────────────

def pkce_pair():
    verifier = secrets.token_urlsafe(48)          # 64 chars of [A-Za-z0-9_-]
    return verifier, hashlib.sha256(verifier.encode()).hexdigest()


def authorize_url(state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_key": client_key(),
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def _token_request(data: dict) -> dict:
    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": client_key(), "client_secret": client_secret(), **data,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    body = r.json() if r.content else {}
    if r.status_code != 200 or "access_token" not in body:
        raise TikTokError(body.get("error_description") or body.get("error") or f"HTTP {r.status_code}")
    now = time.time()
    tok = {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "open_id": body.get("open_id", ""),
        "scope": body.get("scope", ""),
        "expires_at": now + int(body.get("expires_in", 0)),
        "refresh_expires_at": now + int(body.get("refresh_expires_in", 0)),
    }
    save_token(tok)
    return tok


def exchange_code(code: str, verifier: str) -> dict:
    return _token_request({"code": code, "grant_type": "authorization_code",
                           "redirect_uri": REDIRECT_URI, "code_verifier": verifier})


def load_token():
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_token(tok: dict):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok, indent=2), encoding="utf-8")


def needs_refresh(tok: dict, now: float = None, margin: float = 300) -> bool:
    return (now or time.time()) >= tok.get("expires_at", 0) - margin


def access_token() -> str:
    """Valid access token, refreshed when it expires in < 5 min. Refresh tokens rotate."""
    with _lock:
        tok = load_token()
        if not tok:
            raise TikTokError("Compte TikTok non connecté")
        if needs_refresh(tok):
            if time.time() >= tok.get("refresh_expires_at", 0):
                raise TikTokError("Session TikTok expirée : reconnecte le compte")
            tok = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        return tok["access_token"]


def disconnect():
    tok = load_token()
    if tok and configured():
        try:
            requests.post(f"{API}/oauth/revoke/", data={
                "client_key": client_key(), "client_secret": client_secret(), "token": tok["access_token"],
            }, timeout=10)
        except Exception:
            pass      # local logout must succeed even offline
    TOKEN_FILE.unlink(missing_ok=True)


# ── API helpers ───────────────────────────────────────

def _post(path: str, payload: dict = None) -> dict:
    r = requests.post(f"{API}{path}", json=payload or {}, timeout=20, headers={
        "Authorization": f"Bearer {access_token()}",
        "Content-Type": "application/json; charset=UTF-8",
    })
    body = r.json() if r.content else {}
    err = body.get("error", {})
    if r.status_code != 200 or err.get("code") not in (None, "ok"):
        raise TikTokError(f"{err.get('code', r.status_code)}: {err.get('message', '')}".strip())
    return body.get("data", {})


def user_info() -> dict:
    r = requests.get(f"{API}/user/info/", params={"fields": "open_id,display_name,avatar_url"},
                     headers={"Authorization": f"Bearer {access_token()}"}, timeout=15)
    return (r.json().get("data") or {}).get("user", {}) if r.ok else {}


def creator_info() -> dict:
    """Required by TikTok before every direct post: nickname, allowed privacy levels, limits."""
    return _post("/post/publish/creator_info/query/")


def chunk_plan(size: int, chunk: int = CHUNK):
    """(chunk_size, total_chunk_count) following the media transfer rules."""
    if size <= 0:
        raise TikTokError("Fichier vide")
    if size < 5 * MB or size <= chunk:
        return size, 1
    return chunk, size // chunk        # remainder goes into the last chunk


def _put_chunks(upload_url: str, path: str, size: int, chunk_size: int, count: int, progress=None):
    with open(path, "rb") as f:
        for i in range(count):
            start = i * chunk_size
            end = size - 1 if i == count - 1 else start + chunk_size - 1
            data = f.read(end - start + 1)
            r = requests.put(upload_url, data=data, timeout=300, headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{size}",
            })
            if r.status_code not in (200, 201, 206):
                raise TikTokError(f"Upload chunk {i + 1}/{count} refusé (HTTP {r.status_code})")
            if progress:
                progress((i + 1) / count * 80)


def upload(path: str, log, mode: str = "inbox", post_info: dict = None, progress=None):
    """
    mode "inbox": draft sent to the TikTok app, the user finishes the post there (scope video.upload).
    mode "direct": publish with post_info (privacy_level chosen by the user, scope video.publish).
    Returns the final status string, or None on failure.
    """
    size = os.path.getsize(path)
    chunk_size, count = chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": count}
    try:
        if mode == "direct":
            data = _post("/post/publish/video/init/", {"post_info": post_info, "source_info": source})
        else:
            data = _post("/post/publish/inbox/video/init/", {"source_info": source})
        log(f"TikTok : envoi de {Path(path).name} ({count} morceau(x))...", "info")
        _put_chunks(data["upload_url"], path, size, chunk_size, count, progress)

        publish_id = data["publish_id"]
        for _ in range(60):
            time.sleep(5)
            st = _post("/post/publish/status/fetch/", {"publish_id": publish_id})
            status = st.get("status", "")
            if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                msg = ("Brouillon envoyé : ouvre la notification dans l'app TikTok pour publier"
                       if status == "SEND_TO_USER_INBOX" else "Vidéo publiée sur TikTok")
                log(f"TikTok : {msg}", "success")
                return status
            if status == "FAILED":
                raise TikTokError(f"échec ({st.get('fail_reason', 'inconnu')})")
        log("TikTok : toujours en traitement, vérifie l'app TikTok", "warn")
        return "PROCESSING"
    except (TikTokError, requests.RequestException, KeyError) as e:
        log(f"TikTok : {e}", "error")
        return None
