import os
import json
import threading
import datetime
import webbrowser
from pathlib import Path

from flask import Flask, render_template_string, request, redirect, session
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash
import tkinter as tk
from tkinter.filedialog import askdirectory

# ================= CORE =================
from core.downloader import download_video
from core.creator import create_video_batch
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
DATA_DIR       = BASE_DIR / "data"
CLIPS_JSON     = DATA_DIR / "clips_info.json"
DEFAULT_OUTPUT = BASE_DIR / "output"
USERS_JSON     = DATA_DIR / "users.json"
LOGIN_LOG      = DATA_DIR / "login_log.json"

for d in (DATA_DIR, DEFAULT_OUTPUT):
    d.mkdir(parents=True, exist_ok=True)

# users.json format: {"username": "pbkdf2_hash_string"}
if not USERS_JSON.exists():
    with open(USERS_JSON, "w") as f:
        json.dump({"admin": generate_password_hash("admin")}, f, indent=2)
    print("  [OK] Default credentials — username: admin / password: admin")

if not LOGIN_LOG.exists():
    with open(LOGIN_LOG, "w") as f:
        json.dump([], f)

# ================= APP =================
app = Flask(__name__)
app.secret_key = "cliptwist_secret"
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

logs          = []
output_folder = str(DEFAULT_OUTPUT)
input_folder  = str(BASE_DIR / "clips")


def log(message, level="info"):
    ts        = datetime.datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}] {message}"
    entry     = {"text": formatted, "level": level}
    logs.append(entry)
    prefix = {"info": "", "success": "[OK] ", "warn": "[WARN] ", "error": "[ERROR] "}.get(level, "")
    print(f"{prefix}{formatted}")
    try:
        socketio.emit("new_log", entry, namespace="/")
    except Exception:
        pass


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


# ================= AUTO WEEKLY =================
def auto_launch():
    log("Auto Weekly started...", "warn")
    mp4_files = [f for f in os.listdir(output_folder) if f.lower().endswith(".mp4")]
    if len(mp4_files) >= 6:
        for f in mp4_files:
            os.remove(os.path.join(output_folder, f))
        log("Old videos deleted", "warn")
    create_video_batch(6, "final", True, "funny", log, 70, 5, False, output_folder)
    log("6 videos created", "success")


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
        return render_template_string(LOGIN_HTML, error="Invalid username or password")
    return render_template_string(LOGIN_HTML, error=None)


@app.route("/choose_output")
def choose_output():
    global output_folder
    if not is_logged():
        return redirect("/login")
    root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
    folder = askdirectory(initialdir=output_folder); root.destroy()
    if folder:
        output_folder = folder
        log(f"Output folder: {folder}", "info")
    return redirect("/section/creation")


@app.route("/choose_input")
def choose_input():
    global input_folder
    if not is_logged():
        return redirect("/login")
    root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
    folder = askdirectory(initialdir=input_folder); root.destroy()
    if folder:
        input_folder = folder
        log(f"Input folder: {folder}", "info")
    return redirect("/section/creation")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def index():
    return redirect("/section/creation")


@app.route("/section/<name>")
def section(name):
    if not is_logged():
        return redirect("/login")

    users_list    = []
    login_history = []
    if name == "admin" and is_admin():
        raw = load_users()
        users_list = list(raw.keys())
        try:
            with open(LOGIN_LOG) as f:
                login_history = list(reversed(json.load(f)))[:50]
        except Exception:
            pass

    return render_template_string(
        DASHBOARD_HTML,
        active        = name,
        weights       = load_weights(),
        logs          = logs,
        output_folder = output_folder,
        input_folder  = input_folder,
        username      = session.get("user", ""),
        admin         = is_admin(),
        users_list    = users_list,
        login_history = login_history,
    )


@app.route("/download", methods=["POST"])
def download():
    url = request.form["url"]
    cat = request.form.get("category") or "funny"
    threading.Thread(target=lambda: download_video(url, log, cat)).start()
    return redirect("/section/download")


@app.route("/create", methods=["POST"])
def create():
    nb      = int(request.form["number"])
    cat     = request.form["category"]
    cible   = float(request.form["cible"])
    marge   = float(request.form["marge"])
    catcore = request.form.get("catcore") == "off"
    threading.Thread(target=lambda: create_video_batch(
        nb, "final", True, cat, log, cible, marge, catcore, output_folder
    )).start()
    return redirect("/section/creation")


@app.route("/auto_confirm")
def auto_confirm():
    threading.Thread(target=auto_launch).start()
    return redirect("/section/creation")


@app.route("/restore")
def restore():
    threading.Thread(target=lambda: restaurer_clips_json(
        log, str(CLIPS_JSON), str(BASE_DIR / "clips")
    )).start()
    return redirect("/section/creation")


@app.route("/update_weight", methods=["POST"])
def update_weight():
    cat     = request.form["category"]
    val     = float(request.form["value"])
    weights = load_weights()
    weights[cat] = val
    save_weights(weights)
    log(f"Weight updated: {cat} = {val}", "success")
    return redirect("/section/stats")


@app.route("/clear_logs")
def clear_logs():
    logs.clear()
    socketio.emit("clear_logs", {})
    return redirect("/section/creation")


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


# ================= HTML =================

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ClipTwist — Login</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0a0a0f; --surface: #111118; --border: #1e1e2e;
  --accent: #7c3aed; --glow: rgba(124,58,237,0.3);
  --accent2: #06b6d4; --text: #e2e8f0; --muted: #64748b; --err: #f87171;
}
body { background: var(--bg); color: var(--text); font-family: 'DM Sans', sans-serif; min-height: 100vh; display: flex; align-items: center; justify-content: center; overflow: hidden; }
.grid { position: fixed; inset: 0; background-image: linear-gradient(rgba(124,58,237,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(124,58,237,0.03) 1px, transparent 1px); background-size: 40px 40px; pointer-events: none; }
.orb { position: fixed; border-radius: 50%; filter: blur(80px); pointer-events: none; animation: drift 8s ease-in-out infinite alternate; }
.orb1 { width: 400px; height: 400px; background: rgba(124,58,237,0.12); top: -100px; left: -100px; }
.orb2 { width: 300px; height: 300px; background: rgba(6,182,212,0.08); bottom: -50px; right: -50px; animation-delay: -4s; }
@keyframes drift { from { transform: translate(0,0) scale(1); } to { transform: translate(30px,20px) scale(1.05); } }
.card { position: relative; background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 48px 40px; width: 360px; box-shadow: 0 0 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.03) inset; animation: up 0.5s ease forwards; }
@keyframes up { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
.logo { text-align: center; margin-bottom: 36px; }
.logo-sub { font-family: 'Space Mono', monospace; font-size: 11px; letter-spacing: 4px; text-transform: uppercase; color: var(--accent2); margin-bottom: 8px; }
.logo-name { font-family: 'Space Mono', monospace; font-size: 28px; font-weight: 700; background: linear-gradient(135deg, #fff, var(--accent)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.f { margin-bottom: 16px; }
label { display: block; font-size: 11px; font-weight: 500; letter-spacing: 1.5px; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }
input { width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; color: var(--text); font-family: 'DM Sans', sans-serif; font-size: 14px; outline: none; transition: border-color 0.2s, box-shadow 0.2s; }
input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--glow); }
.btn-in { width: 100%; padding: 13px; background: linear-gradient(135deg, var(--accent), #6d28d9); border: none; border-radius: 8px; color: #fff; font-family: 'Space Mono', monospace; font-size: 13px; letter-spacing: 1px; cursor: pointer; margin-top: 8px; transition: opacity 0.2s, transform 0.1s; box-shadow: 0 4px 20px rgba(124,58,237,0.4); }
.btn-in:hover { opacity: 0.9; transform: translateY(-1px); }
.err { background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.3); border-radius: 6px; padding: 10px 14px; font-size: 13px; color: var(--err); margin-bottom: 16px; text-align: center; }
</style>
</head>
<body>
<div class="grid"></div>
<div class="orb orb1"></div><div class="orb orb2"></div>
<div class="card">
  <div class="logo">
    <div class="logo-sub">Automated Clip Engine</div>
    <div class="logo-name">ClipTwist</div>
  </div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST">
    <div class="f"><label>Username</label><input name="username" autocomplete="username" autofocus></div>
    <div class="f"><label>Password</label><input type="password" name="password" autocomplete="current-password"></div>
    <button type="submit" class="btn-in">Sign In →</button>
  </form>
</div>
</body></html>
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ClipTwist — {{ active | capitalize }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #08080e; --surface: #0f0f17; --s2: #15151f;
  --border: #1a1a28; --b2: #252535;
  --accent: #7c3aed; --glow: rgba(124,58,237,0.2);
  --a2: #06b6d4; --a3: #10b981;
  --warn: #f59e0b; --danger: #ef4444;
  --text: #e2e8f0; --muted: #64748b; --m2: #94a3b8;
  --lg: #4ade80; --lbg: #050509;
  --sbw: 204px;
}
html, body { height: 100%; overflow: hidden; }
body { background: var(--bg); color: var(--text); font-family: 'DM Sans', sans-serif; display: flex; height: 100vh; }

/* SIDEBAR */
.sb { width: var(--sbw); flex-shrink: 0; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; padding: 16px 10px 12px; }
.sb-logo { font-family: 'Space Mono', monospace; font-weight: 700; font-size: 14px; color: #fff; display: flex; align-items: center; gap: 8px; padding: 0 8px; margin-bottom: 20px; }
.sb-logo::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: blink 2s ease-in-out infinite; flex-shrink: 0; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.3} }
.sb-grp { font-family: 'Space Mono', monospace; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); padding: 0 10px; margin: 12px 0 3px; }
.nl { display: flex; align-items: center; gap: 9px; padding: 7px 10px; border-radius: 7px; font-size: 13px; color: var(--m2); text-decoration: none; transition: all .12s; }
.nl:hover { background: rgba(255,255,255,.04); color: var(--text); }
.nl.active { background: rgba(124,58,237,.14); color: #c4b5fd; font-weight: 500; }
.nl.active .ni { color: var(--accent); }
.ni { font-size: 13px; width: 16px; text-align: center; flex-shrink: 0; }
.nl.red { color: var(--danger); }
.nl.red:hover { background: rgba(239,68,68,.07); }
.sb-sp { flex: 1; }
.sb-foot { border-top: 1px solid var(--border); padding-top: 8px; display: flex; flex-direction: column; gap: 2px; }
.fpill { font-family: 'Space Mono', monospace; background: var(--bg); border: 1px solid var(--border); border-radius: 5px; padding: 5px 8px; margin-bottom: 3px; display: flex; flex-direction: column; gap: 1px; overflow: hidden; }
.fpill span { font-size: 8px; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); }
.fpill b { font-weight: 400; font-size: 9px; color: var(--m2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }

/* MAIN */
.ab { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
.topbar { height: 42px; background: var(--surface); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 0 18px; gap: 8px; flex-shrink: 0; }
.tb-t { font-family: 'Space Mono', monospace; font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); flex: 1; }
.btn { padding: 5px 11px; border-radius: 6px; font-size: 11px; font-weight: 500; cursor: pointer; text-decoration: none; border: 1px solid transparent; transition: all .12s; display: inline-flex; align-items: center; gap: 5px; font-family: 'DM Sans', sans-serif; }
.bg { background: transparent; border-color: var(--b2); color: var(--m2); }
.bg:hover { background: var(--s2); color: var(--text); }
.bw { background: rgba(245,158,11,.08); border-color: rgba(245,158,11,.25); color: var(--warn); }
.bw:hover { background: rgba(245,158,11,.16); }

/* CONTENT AREA — two modes */
.cw { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

/* scrollable: download / stats / admin */
.cs { flex: 1; overflow-y: auto; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 20px 28px; }
/* admin: fills height, clips overflow to children can manage their own scroll */
.cs.cs-admin { overflow: hidden; justify-content: flex-start; padding: 14px 24px; }
.cs::-webkit-scrollbar { width: 3px; }
.cs::-webkit-scrollbar-thumb { background: var(--b2); border-radius: 2px; }
.cs > .sh, .cs > .wgrid, .cs > form, .cs > .agrid { width: 100%; max-width: 780px; }

/* no-scroll centered: creation only */
.cs-fit { flex: 1; overflow: hidden; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 10px 28px; }

/* LOG BAR */
.lbar { height: 150px; flex-shrink: 0; border-top: 1px solid var(--border); display: flex; flex-direction: column; }
.lbar-h { height: 28px; background: var(--surface); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 0 12px; gap: 6px; flex-shrink: 0; }
.ldot { width: 5px; height: 5px; border-radius: 50%; background: var(--a3); animation: blink 1.5s infinite; }
.ll { font-family: 'Space Mono', monospace; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); flex: 1; }
.lc { font-family: 'Space Mono', monospace; font-size: 9px; color: var(--muted); background: var(--s2); padding: 2px 6px; border-radius: 3px; border: 1px solid var(--border); }
.logbox { flex: 1; background: var(--lbg); overflow-y: auto; padding: 5px 12px; font-family: 'Space Mono', monospace; font-size: 10px; line-height: 1.9; }
.logbox::-webkit-scrollbar { width: 2px; }
.logbox::-webkit-scrollbar-thumb { background: var(--b2); }
.log-info .msg   { color: var(--lg); }
.log-success .msg{ color: #86efac; font-weight: 600; }
.log-warn .msg   { color: #fbbf24; }
.log-error .msg  { color: #f87171; font-weight: 600; }

/* SECTION HEAD */
.sh { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.sh-t { font-family: 'Space Mono', monospace; font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); white-space: nowrap; }
.sh-l { flex: 1; height: 1px; background: var(--border); }

/* ─── CREATION ─── */
.creation-wrap { width: 100%; max-width: 700px; }
.cgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; width: 100%; }
.cc { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; }
.cc-full { grid-column: 1 / -1; }
.cc-lbl { font-family: 'Space Mono', monospace; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); margin-bottom: 7px; display: flex; align-items: center; gap: 7px; }
.cc-lbl::after { content: ''; flex: 1; height: 1px; background: var(--border); }

/* slider */
.sl-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
.sl-name { font-size: 12px; color: var(--m2); }
.sl-val { font-family: 'Space Mono', monospace; font-size: 20px; font-weight: 700; color: var(--text); line-height: 1; }
.sl-val small { font-size: 10px; color: var(--muted); font-weight: 400; margin-left: 2px; }
input[type=range] { -webkit-appearance: none; width: 100%; height: 4px; background: var(--b2); border-radius: 2px; outline: none; cursor: pointer; }
input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 16px; height: 16px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px rgba(124,58,237,.5); cursor: pointer; transition: transform .1s; }
input[type=range]::-webkit-slider-thumb:hover { transform: scale(1.2); }
.sl-marks { display: flex; justify-content: space-between; font-family: 'Space Mono', monospace; font-size: 9px; color: var(--muted); margin-top: 3px; }

/* timing */
.ti-row { display: flex; gap: 8px; align-items: flex-end; }
.ti-f { flex: 1; }
.ti-lbl { font-size: 9px; font-weight: 500; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); margin-bottom: 5px; display: flex; justify-content: space-between; }
.ti-hint { font-size: 9px; color: var(--muted); letter-spacing: 0; text-transform: none; }
.ti-sep { font-size: 18px; color: var(--muted); padding-bottom: 6px; flex-shrink: 0; }
.ti-in { width: 100%; background: var(--bg); border: 1px solid var(--b2); border-radius: 7px; padding: 6px 8px; color: var(--text); font-family: 'Space Mono', monospace; font-size: 15px; font-weight: 700; outline: none; transition: border-color .2s, box-shadow .2s; text-align: center; }
.ti-in:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--glow); }

/* category chips */
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 7px; }
.chip { padding: 4px 11px; border-radius: 20px; border: 1px solid var(--b2); background: transparent; color: var(--m2); font-size: 11px; cursor: pointer; transition: all .12s; font-family: 'DM Sans', sans-serif; }
.chip:hover { border-color: var(--accent); color: var(--text); }
.chip.sel { background: rgba(124,58,237,.15); border-color: var(--accent); color: #c4b5fd; }
.cat-in { width: 100%; background: var(--bg); border: 1px solid var(--b2); border-radius: 7px; padding: 6px 10px; color: var(--text); font-family: 'DM Sans', sans-serif; font-size: 12px; outline: none; transition: border-color .2s, box-shadow .2s; }
.cat-in:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--glow); }
.cat-in::placeholder { color: var(--muted); }

/* toggle */
.tg { display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: var(--bg); border: 1px solid var(--b2); border-radius: 9px; cursor: pointer; transition: all .12s; }
.tg:hover { border-color: var(--accent); }
.tg:has(input:checked) { background: rgba(16,185,129,.05); border-color: rgba(16,185,129,.35); }
.tg input { display: none; }
.tsw { width: 38px; height: 21px; background: var(--b2); border-radius: 11px; flex-shrink: 0; position: relative; transition: background .2s; }
.tsw::after { content: ''; position: absolute; width: 15px; height: 15px; border-radius: 50%; background: #fff; top: 3px; left: 3px; transition: transform .2s; box-shadow: 0 1px 3px rgba(0,0,0,.3); }
.tg:has(input:checked) .tsw { background: var(--a3); }
.tg:has(input:checked) .tsw::after { transform: translateX(17px); }
.tg-tit { font-size: 13px; font-weight: 500; color: var(--text); margin-bottom: 1px; }
.tg-desc { font-size: 10px; color: var(--muted); }

/* submit */
.sub-row { display: flex; gap: 9px; }
.btn-gen { flex: 1; padding: 10px; background: linear-gradient(135deg, var(--accent), #5b21b6); border: none; border-radius: 9px; color: #fff; font-family: 'Space Mono', monospace; font-size: 11px; letter-spacing: 1px; cursor: pointer; transition: opacity .15s, transform .1s; box-shadow: 0 4px 20px rgba(124,58,237,.3); }
.btn-gen:hover { opacity: .88; transform: translateY(-1px); }
.btn-rst { padding: 10px 14px; background: transparent; border: 1px solid var(--b2); border-radius: 9px; color: var(--m2); font-family: 'Space Mono', monospace; font-size: 10px; cursor: pointer; transition: all .12s; }
.btn-rst:hover { border-color: var(--warn); color: var(--warn); }

/* ─── DOWNLOAD ─── */
.dl-f { margin-bottom: 14px; max-width: 480px; width: 100%; }
.dl-lbl { font-size: 10px; font-weight: 500; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; display: flex; justify-content: space-between; }
.dl-hint { font-size: 9px; color: var(--muted); letter-spacing: 0; text-transform: none; }
.dl-in { width: 100%; background: var(--bg); border: 1px solid var(--b2); border-radius: 8px; padding: 10px 13px; color: var(--text); font-family: 'DM Sans', sans-serif; font-size: 14px; outline: none; transition: border-color .2s, box-shadow .2s; }
.dl-in:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--glow); }
.btn-dl { padding: 10px 22px; background: linear-gradient(135deg, var(--a3), #059669); border: none; border-radius: 8px; color: #fff; font-family: 'Space Mono', monospace; font-size: 11px; letter-spacing: 1px; cursor: pointer; box-shadow: 0 4px 18px rgba(16,185,129,.25); transition: opacity .15s; }
.btn-dl:hover { opacity: .85; }

/* ─── STATS ─── */
.wgrid { display: flex; flex-direction: column; gap: 7px; max-width: 480px; width: 100%; }
.wr { display: flex; align-items: center; gap: 11px; padding: 9px 13px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }
.wcat { flex: 1; font-family: 'Space Mono', monospace; font-size: 11px; color: var(--m2); }
.wnum { width: 76px; text-align: right; padding: 5px 9px; font-size: 13px; background: var(--bg); border: 1px solid var(--b2); border-radius: 6px; color: var(--text); font-family: 'Space Mono', monospace; outline: none; }
.wnum:focus { border-color: var(--accent); }
.btn-upd { padding: 5px 11px; border-radius: 5px; background: rgba(245,158,11,.1); border: 1px solid rgba(245,158,11,.3); color: var(--warn); font-size: 10px; cursor: pointer; font-family: 'Space Mono', monospace; transition: all .12s; }
.btn-upd:hover { background: rgba(245,158,11,.2); }

/* ─── ADMIN ─── */
/* Two-column split: left = forms, right = history (scrolls independently) */
.admin-layout { display: flex; gap: 12px; width: 100%; max-width: 900px; flex: 1; min-height: 0; overflow: hidden; }
.admin-left  { display: flex; flex-direction: column; gap: 10px; width: 280px; flex-shrink: 0; overflow-y: auto; }
.admin-right { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.ac { background: var(--surface); border: 1px solid var(--border); border-radius: 11px; padding: 16px 18px; }
.ac-scroll { background: var(--surface); border: 1px solid var(--border); border-radius: 11px; display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
.ac-scroll-body { flex: 1; overflow-y: auto; }
.ac-scroll-body::-webkit-scrollbar { width: 3px; }
.ac-scroll-body::-webkit-scrollbar-thumb { background: var(--b2); border-radius: 2px; }
.ac-lbl { font-family: 'Space Mono', monospace; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); margin-bottom: 12px; display: flex; align-items: center; gap: 7px; }
.ac-lbl::after { content: ''; flex: 1; height: 1px; background: var(--border); }
.ac-lbl-pad { padding: 16px 18px 0; }
.af { margin-bottom: 10px; }
.af label { display: block; font-size: 10px; font-weight: 500; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); margin-bottom: 5px; }
.af input { width: 100%; background: var(--bg); border: 1px solid var(--b2); border-radius: 7px; padding: 8px 11px; color: var(--text); font-family: 'DM Sans', sans-serif; font-size: 13px; outline: none; transition: border-color .2s, box-shadow .2s; }
.af input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--glow); }
.btn-ac { width: 100%; padding: 9px; background: linear-gradient(135deg, var(--a3), #059669); border: none; border-radius: 8px; color: #fff; font-family: 'Space Mono', monospace; font-size: 10px; letter-spacing: 1px; cursor: pointer; margin-top: 2px; transition: opacity .15s; }
.btn-ac:hover { opacity: .85; }
.btn-ac.purple { background: linear-gradient(135deg, var(--accent), #5b21b6); }
.ulist { display: flex; flex-direction: column; gap: 5px; }
.urow { display: flex; align-items: center; gap: 8px; padding: 7px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 7px; }
.uname { flex: 1; font-family: 'Space Mono', monospace; font-size: 11px; color: var(--m2); }
.badge-admin { font-size: 9px; padding: 2px 7px; border-radius: 3px; background: rgba(124,58,237,.15); border: 1px solid rgba(124,58,237,.3); color: #c4b5fd; font-family: 'Space Mono', monospace; }
.xbtn { padding: 4px 8px; border-radius: 5px; font-size: 10px; cursor: pointer; border: 1px solid var(--b2); background: transparent; color: var(--m2); font-family: 'Space Mono', monospace; transition: all .12s; }
.xbtn:hover { background: var(--s2); color: var(--text); }
.xbtn.red { border-color: rgba(239,68,68,.3); color: var(--danger); }
.xbtn.red:hover { background: rgba(239,68,68,.1); }
.xbtn.yl { border-color: rgba(245,158,11,.3); color: var(--warn); }
.xbtn.yl:hover { background: rgba(245,158,11,.1); }
.ltbl { width: 100%; border-collapse: collapse; font-family: 'Space Mono', monospace; font-size: 10px; }
.ltbl th { color: var(--muted); font-weight: 400; text-align: left; padding: 6px 12px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--surface); }
.ltbl td { padding: 7px 12px; border-bottom: 1px solid var(--border); color: var(--m2); }
.ltbl tr:last-child td { border-bottom: none; }
.ltbl tr:hover td { background: rgba(255,255,255,.02); }
.badge-ok { display: inline-block; padding: 2px 7px; border-radius: 3px; font-size: 9px; background: rgba(16,185,129,.12); border: 1px solid rgba(16,185,129,.3); color: var(--a3); }
.badge-fail { display: inline-block; padding: 2px 7px; border-radius: 3px; font-size: 9px; background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.3); color: var(--danger); }
.empty-note { color: var(--muted); font-style: italic; font-size: 12px; padding: 14px 18px; text-align: center; }

/* ─── MODALS ─── */
.mo { position: fixed; inset: 0; background: rgba(0,0,0,.72); backdrop-filter: blur(4px); display: flex; align-items: center; justify-content: center; z-index: 200; opacity: 0; pointer-events: none; transition: opacity .2s; }
.mo.open { opacity: 1; pointer-events: all; }
.mo-box { background: var(--surface); border: 1px solid var(--b2); border-radius: 13px; padding: 26px 28px; width: 360px; box-shadow: 0 20px 60px rgba(0,0,0,.6); transform: scale(.95); transition: transform .2s; }
.mo.open .mo-box { transform: scale(1); }
.mo-title { font-family: 'Space Mono', monospace; font-size: 14px; font-weight: 700; margin-bottom: 6px; }
.mo-title.warn { color: var(--warn); }
.mo-title.danger { color: var(--danger); }
.mo-body { font-size: 13px; color: var(--m2); line-height: 1.6; margin-bottom: 20px; }
.mo-body strong { color: var(--text); }
.mo-actions { display: flex; gap: 8px; justify-content: flex-end; }
</style>
</head>
<body>

<nav class="sb">
  <div class="sb-logo">ClipTwist</div>
  <div class="sb-grp">Workflow</div>
  <a class="nl {{ 'active' if active == 'creation' }}" href="/section/creation"><span class="ni">▶</span> Creation</a>
  <a class="nl {{ 'active' if active == 'download' }}" href="/section/download"><span class="ni">↓</span> Download</a>
  <div class="sb-grp">Config</div>
  <a class="nl {{ 'active' if active == 'stats' }}" href="/section/stats"><span class="ni">◈</span> Weights</a>
  {% if admin %}
  <div class="sb-grp">Admin</div>
  <a class="nl {{ 'active' if active == 'admin' }}" href="/section/admin"><span class="ni">⚙</span> Admin Panel</a>
  {% endif %}
  <div class="sb-sp"></div>
  <div class="sb-foot">
    <div class="fpill"><span>Output</span><b title="{{ output_folder }}">{{ output_folder }}</b></div>
    <div class="fpill"><span>Input</span><b title="{{ input_folder }}">{{ input_folder }}</b></div>
    <a class="nl" href="/choose_output"><span class="ni">📁</span> Output folder</a>
    <a class="nl" href="/choose_input"><span class="ni">📂</span> Input folder</a>
    <a class="nl red" href="/logout" style="margin-top:2px"><span class="ni">⇥</span> Logout</a>
  </div>
</nav>

<div class="ab">
  <div class="topbar">
    <div class="tb-t">{{ active }}</div>
    <button class="btn bw" onclick="document.getElementById('moAuto').classList.add('open')">⚡ Auto Weekly</button>
    <a href="/clear_logs" class="btn bg">Clear logs</a>
  </div>

  <div class="cw">

    {% if active == 'creation' %}
    <!-- CREATION: centered, no scroll -->
    <div class="cs-fit">
      <div class="creation-wrap">
        <div class="sh"><div class="sh-t">Video Creation</div><div class="sh-l"></div></div>
        <form method="POST" action="/create">
          <div class="cgrid">

            <div class="cc">
              <div class="cc-lbl">Videos to generate</div>
              <div class="sl-top">
                <div class="sl-name">Quantity</div>
                <div class="sl-val" id="nb-d">6<small>videos</small></div>
              </div>
              <input type="range" name="number" min="1" max="20" value="6"
                oninput="document.getElementById('nb-d').innerHTML=this.value+'<small>videos</small>'">
              <div class="sl-marks"><span>1</span><span>5</span><span>10</span><span>15</span><span>20</span></div>
            </div>

            <div class="cc">
              <div class="cc-lbl">Timing</div>
              <div class="ti-row">
                <div class="ti-f">
                  <div class="ti-lbl">Target <span class="ti-hint">sec</span></div>
                  <input class="ti-in" type="number" name="cible" value="70" min="1" max="300">
                </div>
                <div class="ti-sep">±</div>
                <div class="ti-f">
                  <div class="ti-lbl">Margin <span class="ti-hint">sec</span></div>
                  <input class="ti-in" type="number" name="marge" value="5" min="0" max="60">
                </div>
              </div>
            </div>

            <div class="cc cc-full">
              <div class="cc-lbl">Category</div>
              <div class="chips">
                <div class="chip sel" onclick="pc(this,'funny')">funny</div>
                <div class="chip" onclick="pc(this,'gaming')">gaming</div>
                <div class="chip" onclick="pc(this,'satisfying')">satisfying</div>
                <div class="chip" onclick="pc(this,'fails')">fails</div>
                <div class="chip" onclick="pc(this,'animals')">animals</div>
                <div class="chip" onclick="pc(this,'cooking')">cooking</div>
              </div>
              <input class="cat-in" type="text" name="category" id="cat-v" value="funny" placeholder="Or type a custom category...">
            </div>

            <div class="cc cc-full">
              <div class="cc-lbl">Options</div>
              <label class="tg">
                <input type="checkbox" name="catcore" checked>
                <div class="tsw"></div>
                <div><div class="tg-tit">Catcore mode</div><div class="tg-desc">Insert cat clips between segments for engagement boost</div></div>
              </label>
            </div>

            <div class="cc-full" style="padding:0">
              <div class="sub-row">
                <button type="submit" class="btn-gen">▶ Generate Videos</button>
                <button type="button" class="btn-rst" onclick="document.getElementById('moRestore').classList.add('open')">↺ Restore JSON</button>
              </div>
            </div>

          </div>
        </form>
        <form id="rf" method="GET" action="/restore" style="display:none"></form>
      </div>
    </div>

    {% else %}
    <!-- OTHER SECTIONS: scrollable, centered -->
    <div class="cs{% if active == 'admin' and admin %} cs-admin{% endif %}">

      {% if active == 'download' %}
      <div class="sh"><div class="sh-t">Download Clip</div><div class="sh-l"></div></div>
      <form method="POST" action="/download" style="width:100%;max-width:780px">
        <div class="dl-f"><div class="dl-lbl">Video URL</div><input class="dl-in" type="url" name="url" placeholder="https://youtube.com/watch?v=..."></div>
        <div class="dl-f"><div class="dl-lbl">Category <span class="dl-hint">optional</span></div><input class="dl-in" type="text" name="category" placeholder="funny"></div>
        <button type="submit" class="btn-dl">↓ Download</button>
      </form>
      {% endif %}

      {% if active == 'stats' %}
      <div class="sh"><div class="sh-t">Category Weights</div><div class="sh-l"></div></div>
      <div class="wgrid">
        {% for cat, val in weights.items() %}
        <form method="POST" action="/update_weight">
          <input type="hidden" name="category" value="{{ cat }}">
          <div class="wr">
            <div class="wcat">{{ cat }}</div>
            <input class="wnum" type="number" step="0.01" name="value" value="{{ val }}">
            <button type="submit" class="btn-upd">Save</button>
          </div>
        </form>
        {% endfor %}
      </div>
      {% endif %}

      {% if active == 'admin' and admin %}
      <div style="width:100%;max-width:900px;flex-shrink:0"><div class="sh"><div class="sh-t">Admin Panel</div><div class="sh-l"></div></div></div>
      <div class="admin-layout">

        <!-- LEFT: create + users -->
        <div class="admin-left">

          <div class="ac">
            <div class="ac-lbl">Create User</div>
            <form method="POST" action="/admin/create_user">
              <div class="af"><label>Username</label><input type="text" name="username" placeholder="johndoe" required></div>
              <div class="af"><label>Password</label><input type="password" name="password" placeholder="••••••••" required></div>
              <button type="submit" class="btn-ac">+ Create User</button>
            </form>
          </div>

          <div class="ac">
            <div class="ac-lbl">Users ({{ users_list | length }})</div>
            {% if users_list %}
            <div class="ulist">
              {% for u in users_list %}
              <div class="urow">
                <div class="uname">{{ u }}</div>
                {% if u == 'admin' %}<span class="badge-admin">admin</span>{% endif %}
                <button class="xbtn yl" onclick="openPwd('{{ u }}')">pwd</button>
                {% if u != 'admin' %}
                <form method="POST" action="/admin/delete_user" style="display:inline">
                  <input type="hidden" name="username" value="{{ u }}">
                  <button type="submit" class="xbtn red" onclick="return confirm('Delete user {{ u }}?')">✕</button>
                </form>
                {% endif %}
              </div>
              {% endfor %}
            </div>
            {% else %}
            <div class="empty-note">No users.</div>
            {% endif %}
          </div>

        </div>

        <!-- RIGHT: connection history, scrolls independently -->
        <div class="admin-right">
          <div class="ac-scroll">
            <div class="ac-lbl ac-lbl-pad">Connection History (last 50)</div>
            <div class="ac-scroll-body">
              {% if login_history %}
              <table class="ltbl">
                <tr><th>Date / Time</th><th>Username</th><th>Status</th></tr>
                {% for e in login_history %}
                <tr>
                  <td>{{ e.ts }}</td>
                  <td>{{ e.username }}</td>
                  <td><span class="{{ 'badge-ok' if e.success else 'badge-fail' }}">{{ 'Success' if e.success else 'Failed' }}</span></td>
                </tr>
                {% endfor %}
              </table>
              {% else %}
              <div class="empty-note">No login history yet.</div>
              {% endif %}
            </div>
          </div>
        </div>

      </div>
      {% endif %}

    </div>
    {% endif %}

    <!-- LOG BAR -->
    <div class="lbar">
      <div class="lbar-h">
        <div class="ldot"></div>
        <div class="ll">Live Logs</div>
        <div class="lc" id="lc">{{ logs | length }} entries</div>
      </div>
      <div class="logbox" id="logbox">
        {% for l in logs %}
        <div class="log-line log-{{ l.level }}"><span class="msg">{{ l.text }}</span></div>
        {% endfor %}
      </div>
    </div>

  </div>
</div>

<!-- MODAL: AUTO WEEKLY -->
<div class="mo" id="moAuto" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="mo-box">
    <div class="mo-title warn">⚡ Auto Weekly</div>
    <div class="mo-body">This will <strong>delete all existing MP4 files</strong> in the output folder and generate <strong>6 new videos</strong> in /funny mode.<br><br>Are you sure?</div>
    <div class="mo-actions">
      <button class="btn bg" onclick="document.getElementById('moAuto').classList.remove('open')">Cancel</button>
      <a href="/auto_confirm" class="btn bw">Yes, Launch →</a>
    </div>
  </div>
</div>

<!-- MODAL: RESTORE JSON -->
<div class="mo" id="moRestore" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="mo-box">
    <div class="mo-title danger">⚠ Restore JSON</div>
    <div class="mo-body">This will <strong>overwrite clips_info.json</strong> by rescanning the clips folder.<br><br>Any manual edits will be <strong>permanently lost</strong>. Continue?</div>
    <div class="mo-actions">
      <button class="btn bg" onclick="document.getElementById('moRestore').classList.remove('open')">Cancel</button>
      <button class="btn bw" onclick="document.getElementById('rf').submit()">Yes, Restore</button>
    </div>
  </div>
</div>

<!-- MODAL: CHANGE PASSWORD -->
<div class="mo" id="moPwd" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="mo-box">
    <div class="mo-title warn">🔑 Change Password</div>
    <form method="POST" action="/admin/change_password">
      <input type="hidden" name="username" id="pwd-u">
      <div class="af" style="margin-bottom:14px">
        <label>New password for <span id="pwd-name" style="color:#c4b5fd"></span></label>
        <input type="password" name="new_password" placeholder="••••••••" required>
      </div>
      <div class="mo-actions">
        <button type="button" class="btn bg" onclick="document.getElementById('moPwd').classList.remove('open')">Cancel</button>
        <button type="submit" class="btn-ac purple" style="width:auto;padding:6px 16px;margin:0">Update</button>
      </div>
    </form>
  </div>
</div>

<script>
function pc(el, cat) {
  document.querySelectorAll('.chip').forEach(c => c.classList.remove('sel'));
  el.classList.add('sel');
  document.getElementById('cat-v').value = cat;
}
var ci = document.getElementById('cat-v');
if (ci) ci.addEventListener('input', () => document.querySelectorAll('.chip').forEach(c => c.classList.remove('sel')));

function openPwd(u) {
  document.getElementById('pwd-u').value = u;
  document.getElementById('pwd-name').textContent = u;
  document.getElementById('moPwd').classList.add('open');
}

var socket = io();
var logbox = document.getElementById('logbox');
var lcEl   = document.getElementById('lc');
var cnt    = {{ logs | length }};

socket.on('new_log', function(d) {
  var l = document.createElement('div');
  l.className = 'log-line log-' + (d.level || 'info');
  l.innerHTML = '<span class="msg">' + d.text + '</span>';
  logbox.appendChild(l);
  logbox.scrollTop = logbox.scrollHeight;
  cnt++;
  lcEl.textContent = cnt + ' entries';
});
socket.on('clear_logs', function() {
  logbox.innerHTML = '';
  cnt = 0;
  lcEl.textContent = '0 entries';
});
logbox.scrollTop = logbox.scrollHeight;
</script>
</body></html>
"""
def open_browser():
    webbrowser.open("http://127.0.0.1:5000/login")

if __name__ == "__main__":
    threading.Timer(1, open_browser).start()
    socketio.run(app, debug=False)