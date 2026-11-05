import os
import json
import secrets
import datetime
import threading
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect, session, abort, flash,
                   send_from_directory, url_for, jsonify)
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ================= CORE =================
from core import jobs, editor, tiktok
from core.downloader import download_video
from core.creator import create_video_from_clips
from core.restore import restaurer_clips_json
from core.categories import load_weights, save_weights

# ================= SILENCE NOISY LIBS =================
import logging
import warnings
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("engineio").setLevel(logging.ERROR)
logging.getLogger("socketio").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# ================= PATH =================
BASE_DIR       = Path(__file__).parent.resolve()
os.chdir(BASE_DIR)    # core/ uses relative paths (clips/, data/, assets/)
load_dotenv(BASE_DIR / ".env")

DATA_DIR       = BASE_DIR / "data"
CLIPS_JSON     = DATA_DIR / "clips_info.json"
DEFAULT_OUTPUT = BASE_DIR / "output"
USERS_JSON     = DATA_DIR / "users.json"
LOGIN_LOG      = DATA_DIR / "login_log.json"
SETTINGS_JSON  = DATA_DIR / "settings.json"
SECRET_FILE    = DATA_DIR / "secret_key"
MUSIC_DIR      = BASE_DIR / "assets" / "music"

for d in (DATA_DIR, DEFAULT_OUTPUT, MUSIC_DIR):
    d.mkdir(parents=True, exist_ok=True)

def ensure_admin():
    """First launch only: random admin password shown once in the console (no more admin/admin)."""
    if not USERS_JSON.exists():
        first_pwd = secrets.token_urlsafe(9)
        with open(USERS_JSON, "w") as f:
            json.dump({"admin": generate_password_hash(first_pwd)}, f, indent=2)
        print(f"  [OK] Compte créé — utilisateur: admin / mot de passe: {first_pwd}  (affiché une seule fois)")

if not LOGIN_LOG.exists():
    with open(LOGIN_LOG, "w") as f:
        json.dump([], f)


def _secret_key():
    key = os.getenv("FLASK_SECRET_KEY")
    if key:
        return key
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
    return SECRET_FILE.read_text().strip()


# ================= SETTINGS =================
def load_settings():
    try:
        s = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    return {"output_folder": s.get("output_folder") or str(DEFAULT_OUTPUT),
            "input_folder": s.get("input_folder") or str(BASE_DIR / "clips")}


def save_settings(s):
    SETTINGS_JSON.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")


settings = load_settings()

# ================= APP =================
app = Flask(__name__)
app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  MAX_CONTENT_LENGTH=200 * 1024 * 1024)
LOCAL_ORIGINS = ["http://127.0.0.1:5000", "http://localhost:5000"]
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins=LOCAL_ORIGINS)

logs = []


def log(message, level="info"):
    ts        = datetime.datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}] {message}"
    entry     = {"text": formatted, "level": level}
    logs.append(entry)
    del logs[:-500]
    prefix = {"info": "", "success": "[OK] ", "warn": "[WARN] ", "error": "[ERROR] "}.get(level, "")
    print(f"{prefix}{formatted}")
    try:
        socketio.emit("new_log", entry, namespace="/")
    except Exception:
        pass


jobs.init(lambda ev, data: socketio.emit(ev, data, namespace="/"), log)


def is_logged():
    return "user" in session


def is_admin():
    return session.get("user") == "admin"


def load_users():
    with open(USERS_JSON) as f:
        return json.load(f)


def save_users(users):
    with open(USERS_JSON, "w") as f:
        json.dump(users, f, indent=2)


def record_login(username, success):
    try:
        with open(LOGIN_LOG) as f:
            data = json.load(f)
    except Exception:
        data = []
    data.append({
        "ts":       datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "username": username,
        "success":  success
    })
    data = data[-500:]
    with open(LOGIN_LOG, "w") as f:
        json.dump(data, f, indent=2)


def _num(name, default, lo, hi, cast=float):
    """Form number clamped to [lo, hi]; bad or empty input falls back to default."""
    try:
        return max(lo, min(hi, cast(request.form.get(name, default))))
    except (TypeError, ValueError):
        return default


def output_dir() -> Path:
    p = Path(settings["output_folder"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def video_path(name: str) -> Path:
    """Only bare .mp4 names that exist in the output folder; never a client-supplied path."""
    if name != Path(name).name or not name.lower().endswith(".mp4"):
        abort(404)
    p = output_dir() / name
    if not p.is_file():
        abort(404)
    return p


def music_files():
    return sorted(p.name for p in MUSIC_DIR.iterdir() if p.suffix.lower() in editor.MUSIC_EXTS)


# ================= AUTH GATE =================
@app.before_request
def require_login():
    if request.endpoint in ("login", "static") or is_logged():
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="auth"), 401
    return redirect(url_for("login"))


@socketio.on("connect")
def on_connect():
    return is_logged()


@app.context_processor
def common():
    return {"output_folder": settings["output_folder"], "input_folder": settings["input_folder"],
            "admin": is_admin(), "username": session.get("user", ""),
            "tiktok_linked": tiktok.load_token() is not None}


# ================= AUTO WEEKLY =================
def auto_launch():
    log("Auto Weekly : suppression des anciennes vidéos...", "warn")
    out = output_dir()
    mp4_files = list(out.glob("*.mp4"))
    if len(mp4_files) >= 6:
        for f in mp4_files:
            f.unlink(missing_ok=True)
        log("Anciennes vidéos supprimées", "warn")
    for i in range(6):
        submit_creation("funny", 70, 5, False, i + 1, 6)


def submit_creation(cat, cible, marge, catcore, i, n):
    out = str(output_dir())
    return jobs.submit("create", f"Création {cat} {i}/{n}", lambda progress: create_video_from_clips(
        log, "final", True, cat, cible, marge, catcore, out, progress))


# ================= ROUTES =================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        users = load_users()
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        h = users.get(u, "")
        if isinstance(h, dict):
            h = h.get("hash", "")
        if h and check_password_hash(h, p):
            session["user"] = u
            record_login(u, True)
            log(f"User '{u}' logged in", "success")
            return redirect("/section/creation")
        record_login(u, False)
        log(f"Failed login: '{u}'", "warn")
        return render_template("login.html", error="Identifiant ou mot de passe invalide")
    return render_template("login.html", error=None)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def index():
    return redirect("/section/creation")


SECTIONS = {"creation", "download", "library", "accounts", "settings", "stats", "admin"}


@app.route("/section/<name>")
def section(name):
    if name not in SECTIONS or (name == "admin" and not is_admin()):
        abort(404)
    ctx = {"active": name, "logs": logs, "jobs_list": jobs.list_jobs()}
    if name == "stats":
        ctx["weights"] = load_weights()
    elif name == "library":
        vids = sorted(output_dir().glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        ctx["videos"] = [{"name": p.name, "size": f"{p.stat().st_size / 1048576:.1f} Mo",
                          "date": datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m %H:%M")}
                         for p in vids]
    elif name == "accounts":
        ctx["tiktok_configured"] = tiktok.configured()
        ctx["redirect_uri"] = tiktok.REDIRECT_URI
        ctx["tiktok_user"] = {}
        if tiktok.load_token():
            try:
                ctx["tiktok_user"] = tiktok.user_info()
            except Exception as e:      # network down / non-JSON reply must not break the page
                flash(f"TikTok : {e}", "error")
    elif name == "admin":
        ctx["users_list"] = list(load_users().keys())
        try:
            with open(LOGIN_LOG) as f:
                ctx["login_history"] = list(reversed(json.load(f)))[:50]
        except Exception:
            ctx["login_history"] = []
    return render_template(f"{name}.html", **ctx)


@app.route("/api/jobs")
def api_jobs():
    return jsonify(jobs.list_jobs())


@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        flash("URL invalide", "error")
        return redirect("/section/download")
    cat = secure_filename(request.form.get("category") or "funny") or "funny"
    jobs.submit("download", f"Téléchargement {cat}", lambda progress: download_video(url, log, cat) or True)
    flash("Téléchargement ajouté à la file", "success")
    return redirect("/section/download")


@app.route("/create", methods=["POST"])
def create():
    nb      = _num("number", 6, 1, 20, int)
    cat     = secure_filename(request.form.get("category", "").strip())
    cible   = _num("cible", 70, 1, 600)
    marge   = _num("marge", 5, 0, 120)
    catcore = request.form.get("catcore") == "on"
    for i in range(nb):
        submit_creation(cat, cible, marge, catcore, i + 1, nb)
    flash(f"{nb} vidéo(s) ajoutée(s) à la file", "success")
    return redirect("/section/creation")


@app.route("/auto_confirm", methods=["POST"])
def auto_confirm():
    threading.Thread(target=auto_launch, daemon=True).start()
    return redirect("/section/creation")


@app.route("/restore", methods=["POST"])
def restore():
    folder = settings["input_folder"]
    jobs.submit("restore", "Restauration JSON", lambda progress: restaurer_clips_json(
        log, str(CLIPS_JSON), folder) is not None)
    return redirect("/section/creation")


@app.route("/update_weight", methods=["POST"])
def update_weight():
    cat     = request.form.get("category", "")
    val     = _num("value", None, 0, 1000)
    weights = load_weights()
    if cat in weights and val is not None:
        weights[cat] = val
        save_weights(weights)
        log(f"Weight updated: {cat} = {val}", "success")
    return redirect("/section/stats")


@app.route("/clear_logs", methods=["POST"])
def clear_logs():
    logs.clear()
    socketio.emit("clear_logs", {})
    return redirect(request.referrer or "/section/creation")


# ── SETTINGS (folders) ──

@app.route("/settings/folders", methods=["POST"])
def settings_folders():
    for key in ("output_folder", "input_folder"):
        val = request.form.get(key, "").strip().strip('"')
        if not val:
            continue
        p = Path(val).expanduser()
        if not p.is_absolute():
            flash(f"Chemin absolu requis : {val}", "error")
            continue
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            flash(f"Dossier invalide : {e}", "error")
            continue
        settings[key] = str(p)
    save_settings(settings)
    flash("Dossiers enregistrés", "success")
    return redirect("/section/settings")


@app.route("/settings/browse/<key>", methods=["POST"])
def settings_browse(key):
    """Native folder picker. Tk runs on the local machine, so it only works when the browser is local too."""
    if key not in ("output_folder", "input_folder"):
        abort(404)
    import tkinter as tk
    from tkinter.filedialog import askdirectory
    root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
    folder = askdirectory(initialdir=settings[key]); root.destroy()
    if folder:
        settings[key] = str(Path(folder))
        save_settings(settings)
        flash(f"Dossier : {settings[key]}", "success")
    return redirect("/section/settings")


# ── LIBRARY / MEDIA ──

@app.route("/media/<name>")
def media(name):
    return send_from_directory(output_dir(), video_path(name).name)


@app.route("/music/<name>")
def music(name):
    if name not in music_files():
        abort(404)
    return send_from_directory(MUSIC_DIR, name)


@app.route("/library/delete", methods=["POST"])
def library_delete():
    p = video_path(request.form.get("name", ""))
    try:
        p.unlink()
    except OSError as e:          # Windows: file still open by an export or the player
        flash(f"Impossible de supprimer {p.name} : {e.strerror}", "error")
        return redirect("/section/library")
    log(f"Supprimé : {p.name}", "warn")
    flash(f"{p.name} supprimée", "success")
    return redirect("/section/library")


# ── EDITOR ──

@app.route("/editor/<name>")
def editor_page(name):
    video_path(name)
    return render_template("editor.html", active="library", name=name, musics=music_files(),
                           selected_music=request.args.get("music", ""), logs=logs, jobs_list=jobs.list_jobs(),
                           positions=list(editor.POSITIONS), colors=list(editor.COLORS))


@app.route("/music/upload", methods=["POST"])
def music_upload():
    back = request.form.get("back", "")
    f = request.files.get("file")
    name = secure_filename(f.filename) if f else ""
    if not name or Path(name).suffix.lower() not in editor.MUSIC_EXTS:
        flash("Fichier audio refusé (mp3, m4a, wav, aac, ogg)", "error")
    else:
        f.save(MUSIC_DIR / name)
        flash(f"Musique ajoutée : {name}", "success")
    if back and back == Path(back).name:
        return redirect(url_for("editor_page", name=back, music=name))
    return redirect("/section/library")


@app.route("/editor/<name>/export", methods=["POST"])
def editor_export(name):
    src = str(video_path(name))
    music_name = request.form.get("music", "")
    music_path = str(MUSIC_DIR / music_name) if music_name in music_files() else None
    try:
        raw = json.loads(request.form.get("texts", "[]"))[:10]
    except ValueError:
        raw = []
    texts = []
    for t in raw if isinstance(raw, list) else []:
        try:
            texts.append({
                "text": str(t.get("text", ""))[:300],
                "start": max(0.0, float(t.get("start", 0))),
                "end": max(0.0, float(t.get("end", 0))),
                "position": t.get("position") if t.get("position") in editor.POSITIONS else "bottom",
                "color": t.get("color") if t.get("color") in editor.COLORS else "white",
                "size": max(24, min(160, int(t.get("size", 72)))),
            })
        except (TypeError, ValueError, AttributeError):
            continue
    opts = dict(music=music_path,
                video_vol=_num("video_vol", 100, 0, 150) / 100,
                music_vol=_num("music_vol", 100, 0, 150) / 100,
                music_offset=_num("music_offset", 0, 0, 3600),
                fade=_num("fade", 0, 0, 10),
                texts=texts)
    out = str(output_dir())
    jobs.submit("edit", f"Montage {name}", lambda progress: editor.export(src, out, log, progress, **opts))
    flash("Export ajouté à la file — le fichier original n'est pas modifié", "success")
    return redirect("/section/library")


# ── TIKTOK ──

@app.route("/tiktok/connect", methods=["POST"])
def tiktok_connect():
    if not tiktok.configured():
        flash("Ajoute TIKTOK_CLIENT_KEY et TIKTOK_CLIENT_SECRET dans .env puis relance l'app", "error")
        return redirect("/section/accounts")
    verifier, challenge = tiktok.pkce_pair()
    state = secrets.token_urlsafe(24)
    session["tt_state"], session["tt_verifier"] = state, verifier
    return redirect(tiktok.authorize_url(state, challenge))


@app.route("/tiktok/callback/")
def tiktok_callback():
    state, verifier = session.pop("tt_state", None), session.pop("tt_verifier", None)
    if request.args.get("error"):
        flash(f"TikTok : {request.args.get('error_description') or request.args['error']}", "error")
    elif not state or not secrets.compare_digest(state, request.args.get("state", "")):
        flash("TikTok : état OAuth invalide, recommence la connexion", "error")
    else:
        try:
            tiktok.exchange_code(request.args.get("code", ""), verifier)
            log("Compte TikTok connecté", "success")
            flash("Compte TikTok connecté", "success")
        except (tiktok.TikTokError, Exception) as e:
            flash(f"TikTok : {e}", "error")
    return redirect("/section/accounts")


@app.route("/tiktok/disconnect", methods=["POST"])
def tiktok_disconnect():
    tiktok.disconnect()
    flash("Compte TikTok déconnecté", "success")
    return redirect("/section/accounts")


@app.route("/publish/<name>", methods=["GET", "POST"])
def publish(name):
    p = video_path(name)
    if not tiktok.load_token():
        flash("Connecte d'abord ton compte TikTok", "error")
        return redirect("/section/accounts")
    creator, creator_error = {}, None
    try:
        creator = tiktok.creator_info()
    except Exception as e:
        creator_error = str(e)

    if request.method == "POST":
        mode = "direct" if request.form.get("mode") == "direct" else "inbox"
        post_info = None
        if mode == "direct":
            privacy = request.form.get("privacy", "")
            own, branded = request.form.get("brand_organic") == "on", request.form.get("brand_content") == "on"
            disclose = request.form.get("disclose") == "on"
            errors = []
            if creator_error:
                errors.append(f"TikTok refuse la publication pour l'instant : {creator_error}")
            if privacy not in creator.get("privacy_level_options", []):
                errors.append("Choisis la confidentialité")
            if disclose and not (own or branded):
                errors.append("Indique s'il s'agit de ta marque ou d'un contenu sponsorisé")
            if branded and privacy == "SELF_ONLY":
                errors.append("Un contenu sponsorisé ne peut pas être privé")
            if request.form.get("consent") != "on":
                errors.append("Accepte la confirmation d'utilisation de la musique")
            if errors:
                for e in errors:
                    flash(e, "error")
                return redirect(url_for("publish", name=name))
            post_info = {
                "title": request.form.get("title", "")[:2200],
                "privacy_level": privacy,
                "disable_comment": request.form.get("allow_comment") != "on",
                "disable_duet": request.form.get("allow_duet") != "on",
                "disable_stitch": request.form.get("allow_stitch") != "on",
                "brand_content_toggle": disclose and branded,
                "brand_organic_toggle": disclose and own,
            }
        jobs.submit("upload", f"TikTok {name}", lambda progress: tiktok.upload(
            str(p), log, mode=mode, post_info=post_info, progress=progress))
        flash("Envoi TikTok ajouté à la file (le traitement peut prendre quelques minutes)", "success")
        return redirect("/section/library")

    duration = editor.probe(str(p))["duration"]
    max_dur = creator.get("max_video_post_duration_sec")
    return render_template("publish.html", active="library", name=name, creator=creator,
                           creator_error=creator_error, duration=duration,
                           too_long=bool(max_dur and duration > max_dur), logs=logs, jobs_list=jobs.list_jobs())


# ── ADMIN ROUTES ──

@app.route("/admin/create_user", methods=["POST"])
def admin_create_user():
    if not is_admin():
        return redirect("/login")
    u = request.form.get("username", "").strip()
    p = request.form.get("password", "")
    if not u or not p:
        return redirect("/section/admin")
    users = load_users()
    if u in users:
        log(f"Admin: user '{u}' already exists", "warn")
        return redirect("/section/admin")
    users[u] = generate_password_hash(p)
    save_users(users)
    log(f"Admin: created user '{u}'", "success")
    return redirect("/section/admin")


@app.route("/admin/change_password", methods=["POST"])
def admin_change_password():
    if not is_admin():
        return redirect("/login")
    u = request.form.get("username", "")
    p = request.form.get("new_password", "")
    if not u or not p:
        return redirect("/section/admin")
    users = load_users()
    if u not in users:
        return redirect("/section/admin")
    users[u] = generate_password_hash(p)
    save_users(users)
    log(f"Admin: password changed for '{u}'", "success")
    return redirect("/section/admin")


@app.route("/admin/delete_user", methods=["POST"])
def admin_delete_user():
    if not is_admin():
        return redirect("/login")
    u = request.form.get("username", "")
    if u == "admin":
        log("Admin: cannot delete admin account", "warn")
        return redirect("/section/admin")
    users = load_users()
    if u in users:
        del users[u]
        save_users(users)
        log(f"Admin: deleted user '{u}'", "warn")
    return redirect("/section/admin")


def open_browser():
    webbrowser.open("http://127.0.0.1:5000/login")


if __name__ == "__main__":
    ensure_admin()
    threading.Timer(1, open_browser).start()
    socketio.run(app, host="127.0.0.1", port=5000, debug=False, allow_unsafe_werkzeug=True)
